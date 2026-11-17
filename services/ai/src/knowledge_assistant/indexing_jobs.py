"""Dedicated worker for complete, fenced chunk and embedding publication."""

from collections.abc import Sequence
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast, get_args
from uuid import UUID, uuid4

from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.serializers import JSONSerializer
from sqlalchemy import Connection, Engine, RowMapping, func, insert, select, update

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.embeddings import (
    EmbeddingConfig,
    EmbeddingError,
    embed_texts,
    embedding_config,
    validate_embeddings,
)
from knowledge_assistant.indexing_chunks import (
    ChunkInputError,
    IndexChunk,
    build_chunks,
    chunk_input_hash,
)
from knowledge_assistant.indexing_store import (
    MANAGER_ROLES,
    SourceKind,
    ready_revision,
    source_row,
    source_scope,
)
from knowledge_assistant.models import (
    source_index_chunks,
    source_index_states,
    source_index_versions,
)
from knowledge_assistant.queueing import create_redis
from knowledge_assistant.worker import main as run_worker
from knowledge_assistant.workspace_store import lock_scope, member_role

INDEX_QUEUE_NAME = "indexing"
INDEX_POINTER_PATH = Path("/tmp/knowledge-assistant-index-worker.json")
QUEUE_LEASE_SECONDS = 300
JOB_TIMEOUT_SECONDS = 90
PROCESSING_LEASE_SECONDS = 120
_BATCH_SIZE = 64
_SAFE_ERRORS = frozenset(
    {
        "invalid_source_content",
        "index_limits_exceeded",
        "embedding_dimension_mismatch",
        "embedding_invalid_response",
        "embedding_unavailable",
        "embedding_input_limit",
        "embedding_credentials_missing",
        "embedding_config_invalid",
    }
)


class IndexQueueError(RuntimeError):
    """The fixed indexing task could not be dispatched."""


class IndexWorkError(RuntimeError):
    """Safe failure text for operational logs."""


class _StaleIndex(RuntimeError):
    """The publishing transaction must roll back after the lease expires."""


@dataclass(frozen=True)
class IndexAttempt:
    team_id: UUID
    kind: SourceKind
    source_id: UUID
    number: int

    def matches(self, status: str):
        table = source_index_states
        return (
            *source_scope(table, self.team_id, self.kind, self.source_id),
            table.c.attempt == self.number,
            table.c.status == status,
        )

    def active(self, status: str):
        return (*self.matches(status), source_index_states.c.lease_until > func.clock_timestamp())


def stage_index(
    connection: Connection,
    team_id: UUID,
    kind: SourceKind,
    source_id: UUID,
    initiator: UUID,
    input_revision: int,
) -> IndexAttempt | None:
    """Record intent under the extraction or manager transaction's team lock."""
    table = source_index_states
    scope = source_scope(table, team_id, kind, source_id)
    current = (
        connection.execute(
            select(table.c.status, table.c.attempt, table.c.input_revision)
            .where(*scope)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if current is not None and (
        current["status"] == "removed" or current["input_revision"] > input_revision
    ):
        return None
    attempt = 1 if current is None else current["attempt"] + 1
    pending = dict(
        requested_by=initiator,
        input_revision=input_revision,
        status="queued",
        attempt=attempt,
        processed=0,
        total=0,
        error=None,
        updated_at=func.clock_timestamp(),
        lease_until=func.clock_timestamp() + timedelta(seconds=QUEUE_LEASE_SECONDS),
    )
    if current is None:
        connection.execute(
            insert(table).values(team_id=team_id, source_kind=kind, source_id=source_id, **pending)
        )
    else:
        connection.execute(update(table).where(*scope).values(**pending))
    return IndexAttempt(team_id, kind, source_id, attempt)


def enqueue_index(
    settings: Settings, team_id: UUID, kind: SourceKind, source_id: UUID, attempt: int
) -> None:
    try:
        with closing(create_redis(settings)) as client:
            Queue(INDEX_QUEUE_NAME, connection=client, serializer=JSONSerializer).enqueue(
                "knowledge_assistant.indexing_jobs.process_index",
                str(team_id),
                kind,
                str(source_id),
                attempt,
                job_id=f"aka-index-{kind}-{team_id.hex}-{source_id.hex}-{attempt}",
                unique=True,
                job_timeout=JOB_TIMEOUT_SECONDS,
                ttl=QUEUE_LEASE_SECONDS,
                result_ttl=300,
                failure_ttl=3600,
                description="Index source",
            )
    except DuplicateJobError:
        return
    except Exception:
        raise IndexQueueError("Source indexing could not be queued") from None


def _fail(connection: Connection, work: IndexAttempt, status: str, error: str) -> None:
    connection.execute(
        update(source_index_states)
        .where(*work.matches(status))
        .values(status="failed", lease_until=None, error=error, updated_at=func.clock_timestamp())
    )


def dispatch_index(engine: Engine, settings: Settings, work: IndexAttempt | None) -> None:
    """Enqueue only after commit, recording outages against the same queued attempt."""
    if work is None:
        return
    try:
        enqueue_index(settings, work.team_id, work.kind, work.source_id, work.number)
    except IndexQueueError:
        with tenant_connection(engine, work.team_id) as connection:
            _fail(connection, work, "queued", "queue_unavailable")


def _lock_ready(
    connection: Connection, work: IndexAttempt, status: str, *, include_entries: bool = False
) -> RowMapping | None:
    lock_scope(connection, f"aka:team:{work.team_id}")
    table = source_index_states
    state = (
        connection.execute(
            select(table.c.requested_by, table.c.input_revision)
            .where(*work.active(status))
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        return None
    if member_role(connection, work.team_id, state["requested_by"]) not in MANAGER_ROLES:
        _fail(connection, work, status, "authorization_revoked")
        return None
    source = source_row(
        connection, work.team_id, work.kind, work.source_id, include_entries=include_entries
    )
    if source is None or ready_revision(source, work.kind) != state["input_revision"]:
        _fail(connection, work, status, "source_changed")
        return None
    return source


def _claim(engine: Engine, work: IndexAttempt) -> list[dict] | None:
    with tenant_connection(engine, work.team_id) as connection:
        row = _lock_ready(connection, work, "queued", include_entries=True)
        if row is None:
            return None
        started = connection.execute(
            update(source_index_states)
            .where(*work.active("queued"))
            .values(
                status="processing",
                updated_at=func.clock_timestamp(),
                lease_until=func.clock_timestamp() + timedelta(seconds=PROCESSING_LEASE_SECONDS),
            )
        )
        return row["entries"] if started.rowcount == 1 else None


def _progress(engine: Engine, work: IndexAttempt, processed: int, total: int) -> bool:
    with tenant_connection(engine, work.team_id) as connection:
        if _lock_ready(connection, work, "processing") is None:
            return False
        saved = connection.execute(
            update(source_index_states)
            .where(*work.active("processing"))
            .values(
                processed=processed,
                total=total,
                updated_at=func.clock_timestamp(),
            )
        )
        return saved.rowcount == 1


def _fingerprint(work: IndexAttempt, digest: bytes, config: EmbeddingConfig):
    table = source_index_versions
    return select(table.c.id).where(
        *source_scope(table, work.team_id, work.kind, work.source_id),
        table.c.input_hash == digest,
        table.c.provider == config.provider,
        table.c.model == config.model,
        table.c.dimension == config.dimension,
        table.c.indexing_version == config.index_version,
    )


def _publish(
    engine: Engine,
    work: IndexAttempt,
    chunks: list[IndexChunk],
    digest: bytes,
    config: EmbeddingConfig,
    vectors: list[list[float]] | None = None,
) -> bool:
    with tenant_connection(engine, work.team_id) as connection:
        if _lock_ready(connection, work, "processing") is None:
            return True
        version_id = connection.scalar(_fingerprint(work, digest, config))
        if version_id is None:
            if vectors is None:
                return False
            version_id = uuid4()
            scope = dict(team_id=work.team_id, source_kind=work.kind, source_id=work.source_id)
            connection.execute(
                insert(source_index_versions).values(
                    **scope,
                    id=version_id,
                    input_hash=digest,
                    provider=config.provider,
                    model=config.model,
                    dimension=config.dimension,
                    indexing_version=config.index_version,
                )
            )
            connection.execute(
                insert(source_index_chunks),
                [
                    dict(
                        **scope,
                        **chunk,
                        version_id=version_id,
                        dimension=config.dimension,
                        embedding=vector,
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
        changed = connection.execute(
            update(source_index_states)
            .where(*work.active("processing"))
            .values(
                active_version_id=version_id,
                status="completed",
                lease_until=None,
                error=None,
                processed=len(chunks),
                total=len(chunks),
                updated_at=func.clock_timestamp(),
            )
        )
        if changed.rowcount != 1:
            raise _StaleIndex
    return True


def _run_index(engine: Engine, work: IndexAttempt, settings: Settings, entries: list[dict]) -> None:
    config = embedding_config(settings)
    chunks = build_chunks(work.kind, entries)
    if not chunks:
        raise ChunkInputError("invalid_source_content")
    digest = chunk_input_hash(chunks)
    if _publish(engine, work, chunks, digest, config):
        return
    total = len(chunks)
    if not _progress(engine, work, 0, total):
        return
    vectors: list[list[float]] = []
    for start in range(0, total, _BATCH_SIZE):
        texts = [chunk["text"] for chunk in chunks[start : start + _BATCH_SIZE]]
        batch = embed_texts(texts, config, settings)
        vectors.extend(validate_embeddings(batch, len(texts), config.dimension))
        if not _progress(engine, work, len(vectors), total):
            return
    _publish(engine, work, chunks, digest, config, vectors)


def process_index(
    team_id: str, kind: str, source_id: str, attempt: int, *, settings: Settings | None = None
) -> None:
    """Perform provider work outside transactions and publish only a current, live attempt."""
    if type(attempt) is not int or attempt < 1 or kind not in get_args(SourceKind):
        raise IndexWorkError("Invalid indexing attempt")
    engine = None
    try:
        work = IndexAttempt(UUID(team_id), cast(SourceKind, kind), UUID(source_id), attempt)
        config = settings if settings is not None else Settings()
        if config.database_user != "aka_app":
            raise IndexWorkError("Index worker requires the runtime role")
        engine = create_engine_from_settings(config)
        entries = _claim(engine, work)
        if entries is None:
            return
        try:
            _run_index(engine, work, config, entries)
            return
        except _StaleIndex:
            return
        except (ChunkInputError, EmbeddingError) as failure:
            error = failure.code if failure.code in _SAFE_ERRORS else "indexing_failed"
        except Exception:
            error = "indexing_failed"
        with tenant_connection(engine, work.team_id) as connection:
            if _lock_ready(connection, work, "processing") is not None:
                _fail(connection, work, "processing", error)
    except Exception:
        raise IndexWorkError("Source indexing could not complete") from None
    finally:
        if engine is not None:
            with suppress(Exception):
                engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    return run_worker(argv, pointer_path=INDEX_POINTER_PATH, queue_name=INDEX_QUEUE_NAME)


if __name__ == "__main__":
    raise SystemExit(main())
