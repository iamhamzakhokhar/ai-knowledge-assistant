"""A private, JSON-only infrastructure probe queue; no public enqueue API."""

from contextlib import suppress
from uuid import UUID, uuid4

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.serializers import JSONSerializer
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.models import job_runs
from knowledge_assistant.secret_files import read_secret_file

QUEUE_NAME = "ingestion"
PROBE_KIND = "probe"
PROBE_TIMEOUT_SECONDS = 30
PROBE_QUEUE_TTL_SECONDS = 3600
PROBE_RESULT_TTL_SECONDS = 3600
PROBE_FAILURE_TTL_SECONDS = 86400


class ProbeEnqueueError(RuntimeError):
    """A safe public error that does not include a connection or credential."""


def create_redis(settings: Settings) -> Redis:
    """Read the password directly into the client, never into a URL or argv."""
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=read_secret_file(settings.redis_password_file),
        decode_responses=False,
        protocol=2,
        socket_connect_timeout=2,
        socket_timeout=3,
        retry=Retry(NoBackoff(), 0),
    )


def _prepare_probe(engine: Engine, team_id: UUID, run_id: UUID) -> bool:
    """Commit the durable record before Redis can deliver its job."""
    with tenant_connection(engine, team_id) as connection:
        connection.execute(
            insert(job_runs)
            .values(id=run_id, team_id=team_id, kind=PROBE_KIND, status="queued")
            .on_conflict_do_nothing(index_elements=[job_runs.c.id])
        )
        row = (
            connection.execute(
                select(job_runs.c.status).where(
                    job_runs.c.id == run_id,
                    job_runs.c.team_id == team_id,
                    job_runs.c.kind == PROBE_KIND,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["status"] == "failed":
            raise ProbeEnqueueError("Probe run is unavailable")
        return row["status"] == "queued"


def _mark_probe_failed(
    engine: Engine,
    team_id: UUID,
    run_id: UUID,
    *,
    expected_status: str,
    error: str,
) -> None:
    """Do not overwrite a completed run or another worker's state transition."""
    with tenant_connection(engine, team_id) as connection:
        connection.execute(
            update(job_runs)
            .where(
                job_runs.c.id == run_id,
                job_runs.c.team_id == team_id,
                job_runs.c.kind == PROBE_KIND,
                job_runs.c.status == expected_status,
            )
            .values(
                status="failed",
                result={"status": "failed", "error": error},
                updated_at=func.now(),
            )
        )


def enqueue_probe(settings: Settings, team_id: UUID, run_id: UUID | None = None) -> str:
    """Enqueue only the fixed probe task, using JSON-safe identifiers."""
    if not isinstance(team_id, UUID) or (run_id is not None and not isinstance(run_id, UUID)):
        raise ValueError("Probe identifiers must be UUID values")
    run_id = run_id if run_id is not None else uuid4()
    job_id = f"aka-probe-{run_id}"
    engine: Engine | None = None
    client: Redis | None = None
    prepared = False
    try:
        engine = create_engine_from_settings(settings)
        if not _prepare_probe(engine, team_id, run_id):
            return job_id
        prepared = True
        client = create_redis(settings)
        queue = Queue(QUEUE_NAME, connection=client, serializer=JSONSerializer)
        queue.enqueue(
            "knowledge_assistant.worker.run_probe",
            str(team_id),
            str(run_id),
            job_id=job_id,
            unique=True,
            job_timeout=PROBE_TIMEOUT_SECONDS,
            ttl=PROBE_QUEUE_TTL_SECONDS,
            result_ttl=PROBE_RESULT_TTL_SECONDS,
            failure_ttl=PROBE_FAILURE_TTL_SECONDS,
            description="Infrastructure probe",
        )
        return job_id
    except DuplicateJobError:
        # The durable record and deterministic ID make a repeated enqueue safe.
        return job_id
    except ProbeEnqueueError:
        raise
    except Exception:
        if prepared and engine is not None:
            with suppress(Exception):
                _mark_probe_failed(
                    engine,
                    team_id,
                    run_id,
                    expected_status="queued",
                    error="queue_unavailable",
                )
        raise ProbeEnqueueError("Infrastructure probe could not be queued") from None
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
        if engine is not None:
            with suppress(Exception):
                engine.dispose()
