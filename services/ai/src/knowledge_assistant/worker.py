"""Run the private RQ worker and its real, tenant-scoped infrastructure probe."""

import argparse
import json
import os
import re
import socket
import stat
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from redis import Redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer
from sqlalchemy import Engine, func, select, update

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.models import job_runs
from knowledge_assistant.queueing import (
    PROBE_KIND,
    PROBE_RESULT_TTL_SECONDS,
    QUEUE_NAME,
    _mark_probe_failed,
    create_redis,
)

WORKER_TTL_SECONDS = 45
HEARTBEAT_MAX_AGE_SECONDS = 60
JOB_MONITORING_INTERVAL_SECONDS = 10
WORKER_POINTER_PATH = Path("/tmp/knowledge-assistant-worker.json")


@dataclass(frozen=True)
class WorkerIdentity:
    name: str
    hostname: str
    pid: int


class ProbeExecutionError(RuntimeError):
    """A generic failure safe for RQ's exception/result logging."""


def run_probe(team_id: str, run_id: str) -> dict[str, str]:
    """Complete an existing probe row, not a document ingestion or model call."""
    if not isinstance(team_id, str) or not isinstance(run_id, str):
        raise ValueError("Probe identifiers must be UUID strings")
    try:
        team_uuid, run_uuid = UUID(team_id), UUID(run_id)
    except ValueError:
        raise ValueError("Probe identifiers must be UUID strings") from None

    engine: Engine | None = None
    claimed = False
    result = {"status": "completed", "run_id": str(run_uuid)}
    try:
        engine = create_engine_from_settings(Settings())
        with tenant_connection(engine, team_uuid) as connection:
            row = (
                connection.execute(
                    select(job_runs.c.status)
                    .where(
                        job_runs.c.id == run_uuid,
                        job_runs.c.team_id == team_uuid,
                        job_runs.c.kind == PROBE_KIND,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] not in {"queued", "completed"}:
                raise ProbeExecutionError("Probe run is unavailable")
            if row["status"] == "completed":
                return result
            connection.execute(
                update(job_runs)
                .where(
                    job_runs.c.id == run_uuid,
                    job_runs.c.team_id == team_uuid,
                    job_runs.c.kind == PROBE_KIND,
                    job_runs.c.status == "queued",
                )
                .values(status="running", updated_at=func.now())
            )
        claimed = True

        with tenant_connection(engine, team_uuid) as connection:
            updated = connection.execute(
                update(job_runs)
                .where(
                    job_runs.c.id == run_uuid,
                    job_runs.c.team_id == team_uuid,
                    job_runs.c.kind == PROBE_KIND,
                    job_runs.c.status == "running",
                )
                .values(status="completed", result=result, updated_at=func.now())
            )
            if updated.rowcount != 1:
                raise ProbeExecutionError("Probe run is unavailable")
        return result
    except ProbeExecutionError:
        raise
    except Exception:
        if claimed and engine is not None:
            with suppress(Exception):
                _mark_probe_failed(
                    engine,
                    team_uuid,
                    run_uuid,
                    expected_status="running",
                    error="probe_failed",
                )
        raise ProbeExecutionError("Infrastructure probe failed") from None
    finally:
        if engine is not None:
            with suppress(Exception):
                engine.dispose()


def worker_name() -> str:
    """An unclean restart must not collide with the previous RQ registration."""
    return f"aka-ingestion-{socket.gethostname()}-{uuid4().hex}"


def _write_worker_pointer(path: Path, identity: WorkerIdentity) -> None:
    """Atomically publish this process's nonsecret identity with owner-only access."""
    descriptor, temporary = tempfile.mkstemp(prefix=".aka-worker-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(
                {"name": identity.name, "hostname": identity.hostname, "pid": identity.pid},
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_worker_pointer(path: Path) -> WorkerIdentity:
    """Inspect only this worker's small regular file, without following symlinks."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("Invalid worker pointer")
        payload = stream.read(1025)
    if len(payload) > 1024:
        raise ValueError("Invalid worker pointer")
    data = json.loads(payload)
    hostname = socket.gethostname()
    if not isinstance(data, dict):
        raise ValueError("Invalid worker pointer")
    name, pid = data.get("name"), data.get("pid")
    if (
        data.get("hostname") != hostname
        or not isinstance(name, str)
        or re.fullmatch(rf"aka-ingestion-{re.escape(hostname)}-[0-9a-f]{{32}}", name) is None
        or type(pid) is not int
        or pid <= 0
    ):
        raise ValueError("Invalid worker pointer")
    return WorkerIdentity(name=name, hostname=hostname, pid=pid)


def _remove_worker_pointer(path: Path, identity: WorkerIdentity) -> None:
    """An older process must not remove a newer process's current pointer."""
    with suppress(Exception):
        if _read_worker_pointer(path) == identity:
            path.unlink()


def check_worker_health(
    settings: Settings, *, pointer_path: Path | None = None, queue_name: str = QUEUE_NAME
) -> bool:
    """Require a live local process and a fresh named RQ worker, including while busy."""
    client: Redis | None = None
    try:
        identity = _read_worker_pointer(pointer_path or WORKER_POINTER_PATH)
        client = create_redis(settings)
        worker = Worker.find_by_key(
            f"{Worker.redis_worker_namespace_prefix}{identity.name}",
            connection=client,
            serializer=JSONSerializer,
        )
        if (
            worker is None
            or worker.name != identity.name
            or worker.hostname != identity.hostname
            or set(worker.queue_names()) != {queue_name}
            or worker.get_state() not in {"started", "idle", "busy"}
            or worker.pid != identity.pid
            or worker.last_heartbeat is None
        ):
            return False
        heartbeat = worker.last_heartbeat
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - heartbeat).total_seconds()
        if not 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS:
            return False
        # Redis can retain a dead worker's record. Check this specific local PID,
        # rather than counting historical registrations or inspecting containers.
        os.kill(worker.pid, 0)
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()


def main(
    argv: Sequence[str] | None = None,
    *,
    pointer_path: Path | None = None,
    queue_name: str = QUEUE_NAME,
) -> int:
    parser = argparse.ArgumentParser(description="Run a KnowledgeDesk worker")
    parser.add_argument("--healthcheck", action="store_true", help="check this named worker")
    args = parser.parse_args(argv)
    client: Redis | None = None
    identity: WorkerIdentity | None = None
    pointer = pointer_path or WORKER_POINTER_PATH
    try:
        if args.healthcheck:
            return (
                0
                if check_worker_health(Settings(), pointer_path=pointer, queue_name=queue_name)
                else 1
            )
        # Clear the old identity before any connection can block. A replacement
        # process may reuse PID 1, but its fresh UUID has no old Redis history.
        pointer.unlink(missing_ok=True)
        identity = WorkerIdentity(worker_name(), socket.gethostname(), os.getpid())
        _write_worker_pointer(pointer, identity)
        settings = Settings()
        client = create_redis(settings)
        queue = Queue(queue_name, connection=client, serializer=JSONSerializer)
        worker = Worker(
            [queue],
            connection=client,
            name=identity.name,
            serializer=JSONSerializer,
            default_result_ttl=PROBE_RESULT_TTL_SECONDS,
            worker_ttl=WORKER_TTL_SECONDS,
            job_monitoring_interval=JOB_MONITORING_INTERVAL_SECONDS,
            log_job_description=False,
        )
        worker.work()
        return 0
    except Exception:
        print("KnowledgeDesk worker is unavailable", file=sys.stderr)
        return 1
    finally:
        if identity is not None:
            _remove_worker_pointer(pointer, identity)
        if client is not None:
            with suppress(Exception):
                client.close()


if __name__ == "__main__":
    raise SystemExit(main())
