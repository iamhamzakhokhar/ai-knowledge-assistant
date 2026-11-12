"""Queue delivery and fenced PostgreSQL transitions for document extraction."""

from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.serializers import JSONSerializer
from sqlalchemy import Connection, Engine, func, select, update

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.document_parser import DocumentParseError, DocumentSection, parse_document
from knowledge_assistant.models import document_originals, documents
from knowledge_assistant.queueing import QUEUE_NAME, create_redis
from knowledge_assistant.workspace_store import lock_scope, member_role

DocumentFormat = Literal["pdf", "docx", "markdown"]
MAX_DOCUMENT_ATTEMPTS = 3
QUEUE_LEASE_SECONDS = 300
PROCESSING_LEASE_SECONDS = 90
JOB_TIMEOUT_SECONDS = 60
MANAGER_ROLES = frozenset({"owner", "admin"})
_PARSER_ERRORS = frozenset(
    {
        "unsupported_type",
        "document_too_large",
        "invalid_document",
        "encrypted_document",
        "empty_document",
        "scanned_pdf",
        "document_limits_exceeded",
    }
)


class DocumentQueueError(RuntimeError):
    """The fixed task could not be dispatched."""


class DocumentWorkError(RuntimeError):
    """Safe failure text for the worker's operational logs."""


@dataclass(frozen=True)
class DocumentAttempt:
    team_id: UUID
    document_id: UUID
    number: int

    def matches(self):
        return (
            documents.c.team_id == self.team_id,
            documents.c.id == self.document_id,
            documents.c.attempt == self.number,
        )


def enqueue_document(settings: Settings, team_id: UUID, document_id: UUID, attempt: int) -> None:
    """Deliver only identifiers to the existing JSON worker after the database commit."""
    try:
        with closing(create_redis(settings)) as client:
            Queue(QUEUE_NAME, connection=client, serializer=JSONSerializer).enqueue(
                "knowledge_assistant.document_jobs.process_document",
                str(team_id),
                str(document_id),
                attempt,
                job_id=f"aka-document-{team_id.hex}-{document_id.hex}-{attempt}",
                unique=True,
                job_timeout=JOB_TIMEOUT_SECONDS,
                ttl=QUEUE_LEASE_SECONDS,
                result_ttl=300,
                failure_ttl=3600,
                description="Extract document sections",
            )
    except DuplicateJobError:
        return
    except Exception:
        raise DocumentQueueError("Document extraction could not be queued") from None


def _fail(connection: Connection, attempt: DocumentAttempt, status: str, error: str) -> None:
    connection.execute(
        update(documents)
        .where(*attempt.matches(), documents.c.status == status)
        .values(
            status="failed",
            lease_until=None,
            sections=None,
            error=error,
            updated_at=func.clock_timestamp(),
        )
    )


def mark_dispatch_failed(engine: Engine, team_id: UUID, document_id: UUID, attempt: int) -> None:
    """A broker error can only change the still-queued attempt it was dispatching."""
    with tenant_connection(engine, team_id) as connection:
        _fail(
            connection,
            DocumentAttempt(team_id, document_id, attempt),
            "queued",
            "queue_unavailable",
        )


def _claim(engine: Engine, attempt: DocumentAttempt) -> tuple[bytes, DocumentFormat] | None:
    with tenant_connection(engine, attempt.team_id) as connection:
        lock_scope(connection, f"aka:team:{attempt.team_id}")
        row = (
            connection.execute(
                select(documents.c.requested_by, documents.c.format)
                .where(
                    *attempt.matches(),
                    documents.c.status == "queued",
                    documents.c.lease_until > func.clock_timestamp(),
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if member_role(connection, attempt.team_id, row["requested_by"]) not in MANAGER_ROLES:
            _fail(connection, attempt, "queued", "authorization_revoked")
            return None
        data = connection.scalar(
            select(document_originals.c.data).where(
                document_originals.c.team_id == attempt.team_id,
                document_originals.c.id == attempt.document_id,
            )
        )
        if data is None:
            _fail(connection, attempt, "queued", "invalid_document")
            return None
        connection.execute(
            update(documents)
            .where(*attempt.matches(), documents.c.status == "queued")
            .values(
                status="processing",
                updated_at=func.clock_timestamp(),
                lease_until=func.clock_timestamp() + timedelta(seconds=PROCESSING_LEASE_SECONDS),
            )
        )
        return bytes(data), cast(DocumentFormat, row["format"])


def _publish(
    engine: Engine,
    attempt: DocumentAttempt,
    sections: list[DocumentSection] | None,
    error: str | None,
) -> None:
    with tenant_connection(engine, attempt.team_id) as connection:
        lock_scope(connection, f"aka:team:{attempt.team_id}")
        active = (
            *attempt.matches(),
            documents.c.status == "processing",
            documents.c.lease_until > func.clock_timestamp(),
        )
        initiator = connection.scalar(
            select(documents.c.requested_by).where(*active).with_for_update()
        )
        if initiator is None:
            return
        if member_role(connection, attempt.team_id, initiator) not in MANAGER_ROLES:
            _fail(connection, attempt, "processing", "authorization_revoked")
        elif error is not None:
            _fail(connection, attempt, "processing", error)
        else:
            connection.execute(
                update(documents)
                .where(*active)
                .values(
                    status="completed",
                    lease_until=None,
                    sections=sections,
                    error=None,
                    updated_at=func.clock_timestamp(),
                )
            )


def process_document(
    team_id: str, document_id: str, attempt: int, *, settings: Settings | None = None
) -> None:
    """Claim a live attempt, extract outside the transaction, and atomically publish it."""
    if type(attempt) is not int or not 1 <= attempt <= MAX_DOCUMENT_ATTEMPTS:
        raise DocumentWorkError("Invalid document attempt")
    engine = None
    try:
        work = DocumentAttempt(UUID(team_id), UUID(document_id), attempt)
        config = settings if settings is not None else Settings()
        if config.database_user != "aka_app":
            raise DocumentWorkError("Document worker requires the runtime role")
        engine = create_engine_from_settings(config)
        original = _claim(engine, work)
        if original is None:
            return
        data, kind = original
        sections = None
        error = None
        try:
            sections = parse_document(data, kind)
            if not sections:
                error = "empty_document"
        except DocumentParseError as failure:
            error = failure.code if failure.code in _PARSER_ERRORS else "invalid_document"
        except Exception:
            error = "processing_failed"
        _publish(engine, work, sections, error)
    except Exception:
        raise DocumentWorkError("Document extraction could not complete") from None
    finally:
        if engine is not None:
            with suppress(Exception):
                engine.dispose()
