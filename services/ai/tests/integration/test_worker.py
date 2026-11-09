"""Prove a real JSON RQ job runs in the Compose worker and persists tenant-scoped state."""

import json
import os
import time
import zlib
from uuid import uuid4

import pytest
from redis import Redis
from rq.command import send_stop_job_command
from rq.job import Job
from rq.serializers import JSONSerializer
from sqlalchemy import text
from sqlalchemy.engine import Engine

from knowledge_assistant.config import Settings
from knowledge_assistant.database import tenant_connection
from knowledge_assistant.queueing import QUEUE_NAME, enqueue_probe
from knowledge_assistant.seed import DEMO_TEAM_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("AKA_INTEGRATION_TESTS") != "1",
    reason="Real Redis and worker tests require AKA_INTEGRATION_TESTS=1",
)


def test_probe_is_json_serialized_completed_and_tenant_scoped(
    integration_settings: Settings,
    app_engine: Engine,
    admin_connection,
    redis_connection: Redis,
    tenant_rows,
) -> None:
    run_id = uuid4()
    job_id = None
    try:
        with tenant_connection(app_engine, DEMO_TEAM_ID) as connection:
            assert connection.scalar(text("SELECT id FROM teams")) == DEMO_TEAM_ID, (
                "The migration/seed service must finish before integration tests start"
            )
        job_id = enqueue_probe(integration_settings, DEMO_TEAM_ID, run_id)
        assert job_id == f"aka-probe-{run_id}"
        job = Job.fetch(job_id, connection=redis_connection, serializer=JSONSerializer)
        assert job.origin == QUEUE_NAME
        assert job.serializer is JSONSerializer

        # Inspect Redis bytes independently of RQ's deserializer. Pickle is never loaded.
        stored = redis_connection.hget(job.key, "data")
        assert isinstance(stored, bytes)
        try:
            serialized = zlib.decompress(stored)
        except zlib.error:
            serialized = stored
        payload = json.loads(serialized)
        assert payload[0] == "knowledge_assistant.worker.run_probe"
        assert payload[2] == [str(DEMO_TEAM_ID), str(run_id)]
        assert payload[3] == {}

        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline:
            status = job.get_status(refresh=True)
            if status in {"finished", "failed", "stopped", "canceled"}:
                break
            time.sleep(0.2)
        assert status == "finished", (
            f"Worker did not complete the probe within 20 seconds: {status}"
        )
        expected_result = {"status": "completed", "run_id": str(run_id)}
        assert job.return_value(refresh=True) == expected_result

        with tenant_connection(app_engine, DEMO_TEAM_ID) as connection:
            row = (
                connection.execute(
                    text("SELECT team_id, kind, status, result FROM job_runs WHERE id = :id"),
                    {"id": run_id},
                )
                .mappings()
                .one()
            )
            assert dict(row) == {
                "team_id": DEMO_TEAM_ID,
                "kind": "probe",
                "status": "completed",
                "result": expected_result,
            }
        with tenant_connection(app_engine, tenant_rows.team_b) as connection:
            assert (
                connection.scalar(text("SELECT id FROM job_runs WHERE id = :id"), {"id": run_id})
                is None
            )
        with app_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT id FROM job_runs WHERE id = :id"), {"id": run_id})
                is None
            )
    finally:
        if job_id is not None and redis_connection.exists(f"rq:job:{job_id}"):
            cleanup_job = Job.fetch(job_id, connection=redis_connection, serializer=JSONSerializer)
            if cleanup_job.get_status(refresh=True) == "started":
                send_stop_job_command(redis_connection, job_id)
                stop_deadline = time.monotonic() + 5
                while (
                    cleanup_job.get_status(refresh=True) == "started"
                    and time.monotonic() < stop_deadline
                ):
                    time.sleep(0.1)
                assert cleanup_job.get_status(refresh=True) != "started", (
                    "Cleanup could not confirm this probe stopped; its record was retained"
                )
            cleanup_job.delete(remove_from_queue=True)
        admin_connection.execute(
            "DELETE FROM job_runs WHERE id = %s AND team_id = %s", (run_id, DEMO_TEAM_ID)
        )
