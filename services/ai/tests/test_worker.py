"""Queue safety, durable probe behavior and live-worker health decisions."""

import json
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import UUID, uuid4

import pytest
from redis import Redis
from rq.exceptions import DuplicateJobError
from rq.job import Job
from rq.serializers import JSONSerializer

from knowledge_assistant import queueing, worker
from knowledge_assistant.config import Settings


def test_redis_uses_a_password_file_and_binary_safe_connection(tmp_path: Path) -> None:
    password_file = tmp_path / "redis-test-password"
    password_file.write_text("synthetic-test-password\n")
    client = queueing.create_redis(Settings(redis_password_file=password_file))
    try:
        options = client.connection_pool.connection_kwargs
        assert options["password"] == "synthetic-test-password"
        assert options["decode_responses"] is False
        assert options["protocol"] == 2
        assert options["socket_connect_timeout"] <= 3
    finally:
        client.close()


def test_enqueue_commits_before_delivery_and_uses_json_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, run_id = uuid4(), uuid4()
    engine, client, queue = Mock(), Mock(), Mock()
    events = []
    monkeypatch.setattr(queueing, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(
        queueing,
        "_prepare_probe",
        lambda *args: events.append("durable-record-committed") or True,
    )
    monkeypatch.setattr(queueing, "create_redis", Mock(return_value=client))
    factory = Mock(return_value=queue)
    monkeypatch.setattr(queueing, "Queue", factory)
    queue.enqueue.side_effect = lambda *args, **kwargs: events.append("redis-enqueue")

    job_id = queueing.enqueue_probe(Settings(), team_id, run_id)
    assert events == ["durable-record-committed", "redis-enqueue"]
    assert job_id == f"aka-probe-{run_id}"
    assert factory.call_args.kwargs["serializer"] is JSONSerializer
    task, *payload = queue.enqueue.call_args.args
    assert task == "knowledge_assistant.worker.run_probe"
    assert payload == [str(team_id), str(run_id)]
    options = queue.enqueue.call_args.kwargs
    assert options["job_id"] == job_id and options["unique"] is True
    assert options["job_timeout"] > 0 and options["ttl"] > 0
    assert options["result_ttl"] > 0 and options["failure_ttl"] > 0

    # Exercise the real RQ serializer without connecting to Redis or using pickle.
    job = Job.create(task, args=payload, connection=Mock(spec=Redis), serializer=JSONSerializer)
    decoded = json.loads(job.data)
    assert decoded[0] == task and decoded[2] == payload
    client.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_enqueue_failure_records_only_a_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    team_id, run_id = uuid4(), uuid4()
    engine, queue = Mock(), Mock()
    mark_failed = Mock()
    monkeypatch.setattr(queueing, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(queueing, "_prepare_probe", Mock(return_value=True))
    monkeypatch.setattr(queueing, "create_redis", Mock(return_value=Mock()))
    monkeypatch.setattr(queueing, "Queue", Mock(return_value=queue))
    monkeypatch.setattr(queueing, "_mark_probe_failed", mark_failed)
    queue.enqueue.side_effect = RuntimeError("synthetic-private-enqueue-marker")

    with pytest.raises(queueing.ProbeEnqueueError) as caught:
        queueing.enqueue_probe(Settings(), team_id, run_id)
    assert "synthetic-private-enqueue-marker" not in str(caught.value)
    mark_failed.assert_called_once_with(
        engine, team_id, run_id, expected_status="queued", error="queue_unavailable"
    )


def test_duplicate_enqueue_does_not_mark_the_durable_record_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    queue, mark_failed = Mock(), Mock()
    monkeypatch.setattr(queueing, "create_engine_from_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(queueing, "_prepare_probe", Mock(return_value=True))
    monkeypatch.setattr(queueing, "create_redis", Mock(return_value=Mock()))
    monkeypatch.setattr(queueing, "Queue", Mock(return_value=queue))
    monkeypatch.setattr(queueing, "_mark_probe_failed", mark_failed)
    queue.enqueue.side_effect = DuplicateJobError("existing synthetic job")

    assert queueing.enqueue_probe(Settings(), uuid4(), run_id) == f"aka-probe-{run_id}"
    mark_failed.assert_not_called()


def test_completed_run_is_not_delivered_again(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid4()
    redis_factory = Mock()
    monkeypatch.setattr(queueing, "create_engine_from_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(queueing, "_prepare_probe", Mock(return_value=False))
    monkeypatch.setattr(queueing, "create_redis", redis_factory)
    assert queueing.enqueue_probe(Settings(), uuid4(), run_id) == f"aka-probe-{run_id}"
    redis_factory.assert_not_called()


@pytest.mark.parametrize("team_id,run_id", [(None, "bad"), ({}, "bad"), ("bad", "bad")])
def test_invalid_probe_payload_never_connects(
    monkeypatch: pytest.MonkeyPatch, team_id: object, run_id: object
) -> None:
    engine_factory = Mock()
    monkeypatch.setattr(worker, "create_engine_from_settings", engine_factory)
    with pytest.raises(ValueError, match="UUID strings"):
        worker.run_probe(team_id, run_id)  # type: ignore[arg-type]
    engine_factory.assert_not_called()


@pytest.fixture
def probe_connection(monkeypatch: pytest.MonkeyPatch):
    engine, connection = Mock(), MagicMock()
    tenants = []

    @contextmanager
    def scoped_connection(_engine: object, team_id: UUID):
        tenants.append(team_id)
        yield connection

    monkeypatch.setattr(worker, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(worker, "tenant_connection", scoped_connection)
    return engine, connection, tenants


@pytest.mark.parametrize("state", [None, "running", "failed"])
def test_probe_rejects_missing_or_ineligible_runs(probe_connection, state: str | None) -> None:
    engine, connection, _ = probe_connection
    row = None if state is None else {"status": state}
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = row
    with pytest.raises(worker.ProbeExecutionError, match="unavailable"):
        worker.run_probe(str(uuid4()), str(uuid4()))
    assert connection.execute.call_count == 1
    engine.dispose.assert_called_once_with()


def test_probe_completes_only_its_existing_tenant_run(probe_connection) -> None:
    engine, connection, tenants = probe_connection
    team_id, run_id = uuid4(), uuid4()
    selected = MagicMock()
    selected.mappings.return_value.one_or_none.return_value = {"status": "queued"}
    connection.execute.side_effect = [selected, Mock(rowcount=1), Mock(rowcount=1)]

    result = worker.run_probe(str(team_id), str(run_id))
    assert result == {"status": "completed", "run_id": str(run_id)}
    assert tenants == [team_id, team_id]
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0].compile().params["team_id_1"] == team_id
    assert statements[1].compile().params["status"] == "running"
    assert statements[2].compile().params["result"] == result
    assert statements[2].compile().params["team_id_1"] == team_id
    engine.dispose.assert_called_once_with()


def test_completed_probe_replay_has_no_write(probe_connection) -> None:
    _, connection, _ = probe_connection
    run_id = uuid4()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "status": "completed"
    }
    assert worker.run_probe(str(uuid4()), str(run_id)) == {
        "status": "completed",
        "run_id": str(run_id),
    }
    assert connection.execute.call_count == 1


def test_claimed_probe_failure_is_generic_and_marks_its_run_failed(
    monkeypatch: pytest.MonkeyPatch, probe_connection
) -> None:
    engine, connection, _ = probe_connection
    team_id, run_id = uuid4(), uuid4()
    selected = MagicMock()
    selected.mappings.return_value.one_or_none.return_value = {"status": "queued"}
    connection.execute.side_effect = [
        selected,
        Mock(rowcount=1),
        RuntimeError("synthetic-private-database-marker"),
    ]
    mark_failed = Mock()
    monkeypatch.setattr(worker, "_mark_probe_failed", mark_failed)

    with pytest.raises(worker.ProbeExecutionError) as caught:
        worker.run_probe(str(team_id), str(run_id))
    assert "synthetic-private-database-marker" not in str(caught.value)
    mark_failed.assert_called_once_with(
        engine, team_id, run_id, expected_status="running", error="probe_failed"
    )


@pytest.fixture
def worker_pointer(tmp_path: Path) -> tuple[Path, worker.WorkerIdentity]:
    pointer = tmp_path / "worker.json"
    identity = worker.WorkerIdentity(worker.worker_name(), socket.gethostname(), 4242)
    worker._write_worker_pointer(pointer, identity)
    assert pointer.stat().st_mode & 0o777 == 0o600
    return pointer, identity


def healthy_registration(name: str, state: str = "idle") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        hostname=socket.gethostname(),
        queue_names=lambda: [queueing.QUEUE_NAME],
        get_state=lambda: state,
        pid=4242,
        last_heartbeat=datetime.now(UTC),
    )


@pytest.mark.parametrize("state", ["idle", "busy", "started"])
def test_named_live_worker_is_healthy_even_during_a_job(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
    state: str,
) -> None:
    pointer, identity = worker_pointer
    client = Mock()
    find = Mock(return_value=healthy_registration(identity.name, state))
    alive = Mock()
    monkeypatch.setattr(worker, "create_redis", Mock(return_value=client))
    monkeypatch.setattr(worker.Worker, "find_by_key", find)
    monkeypatch.setattr(worker.os, "kill", alive)

    assert worker.check_worker_health(Settings(), pointer_path=pointer)
    assert find.call_args.args[0] == f"rq:worker:{identity.name}"
    assert find.call_args.kwargs["serializer"] is JSONSerializer
    alive.assert_called_once_with(4242, 0)
    client.close.assert_called_once_with()


@pytest.mark.parametrize("case", ["missing", "stale", "wrong-queue", "suspended", "dead-pid"])
def test_historical_or_inactive_worker_is_not_healthy(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
    case: str,
) -> None:
    pointer, identity = worker_pointer
    registration = healthy_registration(identity.name)
    if case == "stale":
        registration.last_heartbeat -= timedelta(seconds=worker.HEARTBEAT_MAX_AGE_SECONDS + 1)
    elif case == "wrong-queue":
        registration.queue_names = lambda: ["another-queue"]
    elif case == "suspended":
        registration.get_state = lambda: "suspended"
    client = Mock()
    monkeypatch.setattr(worker, "create_redis", Mock(return_value=client))
    monkeypatch.setattr(
        worker.Worker,
        "find_by_key",
        Mock(return_value=None if case == "missing" else registration),
    )
    monkeypatch.setattr(
        worker.os,
        "kill",
        Mock(side_effect=ProcessLookupError() if case == "dead-pid" else None),
    )

    assert not worker.check_worker_health(Settings(), pointer_path=pointer)
    client.close.assert_called_once_with()


def test_worker_instances_use_distinct_names_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pointer = tmp_path / "worker.json"
    client, process = Mock(), Mock()
    queue_factory, worker_factory = Mock(return_value=Mock()), Mock(return_value=process)
    monkeypatch.setattr(worker, "create_redis", Mock(return_value=client))
    monkeypatch.setattr(worker, "Queue", queue_factory)
    monkeypatch.setattr(worker, "Worker", worker_factory)

    assert worker.main([], pointer_path=pointer) == 0
    first_name = worker_factory.call_args.kwargs["name"]
    assert not pointer.exists()
    assert worker.main([], pointer_path=pointer) == 0
    second_name = worker_factory.call_args.kwargs["name"]
    assert first_name != second_name
    assert first_name.startswith(f"aka-ingestion-{socket.gethostname()}-")
    assert queue_factory.call_args.kwargs["serializer"] is JSONSerializer
    assert worker_factory.call_args.kwargs["serializer"] is JSONSerializer
    assert process.work.call_count == 2
    assert client.close.call_count == 2
    assert not pointer.exists()


def test_worker_start_failure_prints_no_connection_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        worker,
        "create_redis",
        Mock(side_effect=RuntimeError("synthetic-private-start-marker")),
    )
    assert worker.main([], pointer_path=tmp_path / "worker.json") == 1
    output = capsys.readouterr().err
    assert "unavailable" in output
    assert "synthetic-private-start-marker" not in output


def test_missing_pointer_is_unhealthy_without_connecting_or_generating_a_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    redis_factory, name_factory = Mock(), Mock(side_effect=AssertionError("not a startup"))
    monkeypatch.setattr(worker, "create_redis", redis_factory)
    monkeypatch.setattr(worker, "worker_name", name_factory)
    assert worker.main(["--healthcheck"], pointer_path=tmp_path / "missing.json") == 1
    redis_factory.assert_not_called()
    name_factory.assert_not_called()


@pytest.mark.parametrize("case", ["json", "hostname", "name", "pid", "permissions", "symlink"])
def test_invalid_pointer_is_unhealthy_before_redis(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
    case: str,
) -> None:
    pointer, identity = worker_pointer
    data = {"name": identity.name, "hostname": identity.hostname, "pid": identity.pid}
    if case == "json":
        pointer.write_text("not JSON")
    elif case == "permissions":
        pointer.chmod(0o644)
    elif case == "symlink":
        target = pointer.with_name("target.json")
        pointer.rename(target)
        pointer.symlink_to(target)
    else:
        data[case] = {"hostname": "another-host", "name": "unscoped-worker", "pid": True}[case]
        pointer.write_text(json.dumps(data))
    redis_factory = Mock()
    monkeypatch.setattr(worker, "create_redis", redis_factory)
    assert not worker.check_worker_health(Settings(), pointer_path=pointer)
    redis_factory.assert_not_called()


def test_old_registration_with_recycled_pid_cannot_satisfy_current_pointer(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
) -> None:
    pointer, identity = worker_pointer
    old = healthy_registration(worker.worker_name())
    assert old.name != identity.name and old.pid == identity.pid
    find = Mock(return_value=old)
    monkeypatch.setattr(worker, "create_redis", Mock(return_value=Mock()))
    monkeypatch.setattr(worker.Worker, "find_by_key", find)
    monkeypatch.setattr(worker.os, "kill", Mock())
    assert not worker.check_worker_health(Settings(), pointer_path=pointer)
    assert find.call_args.args[0] == f"rq:worker:{identity.name}"


def test_startup_replaces_old_pointer_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
) -> None:
    pointer, old = worker_pointer

    def connection_attempt(_settings: Settings):
        current = worker._read_worker_pointer(pointer)
        assert current.name != old.name
        assert current.pid == worker.os.getpid()
        raise RuntimeError("synthetic connection failure")

    monkeypatch.setattr(worker, "create_redis", connection_attempt)
    assert worker.main([], pointer_path=pointer) == 1
    assert not pointer.exists()


def test_healthcheck_keeps_the_current_identity_without_generating_another(
    monkeypatch: pytest.MonkeyPatch,
    worker_pointer: tuple[Path, worker.WorkerIdentity],
) -> None:
    pointer, identity = worker_pointer
    monkeypatch.setattr(worker, "worker_name", Mock(side_effect=AssertionError("not startup")))
    monkeypatch.setattr(worker, "create_redis", Mock(return_value=Mock()))
    monkeypatch.setattr(
        worker.Worker, "find_by_key", Mock(return_value=healthy_registration(identity.name))
    )
    monkeypatch.setattr(worker.os, "kill", Mock())
    assert worker.main(["--healthcheck"], pointer_path=pointer) == 0
    assert worker._read_worker_pointer(pointer) == identity


def test_an_old_process_cannot_remove_a_new_instance_pointer(
    worker_pointer: tuple[Path, worker.WorkerIdentity],
) -> None:
    pointer, old = worker_pointer
    current = worker.WorkerIdentity(worker.worker_name(), old.hostname, old.pid)
    worker._write_worker_pointer(pointer, current)
    worker._remove_worker_pointer(pointer, old)
    assert worker._read_worker_pointer(pointer) == current
