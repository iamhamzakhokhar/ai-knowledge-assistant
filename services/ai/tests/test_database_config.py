"""Configuration and transaction contracts that require no database server."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.migrate import MigrationError, run_migrations
from knowledge_assistant.secret_files import SecretFileError, read_secret_file


def test_connection_settings_load_named_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in {
        "DATABASE_HOST": "database",
        "DATABASE_PORT": "5433",
        "DATABASE_NAME": "knowledge_test",
        "DATABASE_USER": "aka_app",
        "DATABASE_PASSWORD_FILE": "/run/secrets/database_password",
        "REDIS_HOST": "redis",
        "REDIS_PORT": "6380",
        "REDIS_PASSWORD_FILE": "/run/secrets/redis_password",
    }.items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    assert (settings.database_host, settings.database_port, settings.database_name) == (
        "database",
        5433,
        "knowledge_test",
    )
    assert settings.database_user == "aka_app"
    assert settings.database_password_file == Path("/run/secrets/database_password")
    assert (settings.redis_host, settings.redis_port) == ("redis", 6380)
    assert settings.redis_password_file == Path("/run/secrets/redis_password")


@pytest.mark.parametrize("field", ["database_port", "redis_port"])
@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_service_ports_fail_validation(field: str, port: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: port})


def test_secret_reader_handles_trailing_newline_and_preserves_value(tmp_path: Path) -> None:
    path = tmp_path / "test_secret"
    path.write_text("fictional-test-value\n", encoding="utf-8")
    assert read_secret_file(path) == "fictional-test-value"


@pytest.mark.parametrize("contents", ["", "\n", "one\ntwo", "value\x00", "x" * 8193])
def test_invalid_secret_errors_exclude_file_contents(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "test_secret"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(SecretFileError, match="one nonempty line"):
        read_secret_file(path)


def test_missing_secret_configuration_and_file_have_generic_errors(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError, match="not configured"):
        read_secret_file(None)
    with pytest.raises(SecretFileError, match="Unable to read") as captured:
        read_secret_file(tmp_path / "missing")
    assert str(tmp_path) not in str(captured.value)


def test_engine_hides_password_and_parameters_without_connecting(tmp_path: Path) -> None:
    path = tmp_path / "test_secret"
    path.write_text("fictional@password:/with-specials", encoding="utf-8")
    engine = create_engine_from_settings(Settings(database_password_file=path))
    try:
        assert engine.url.password == "fictional@password:/with-specials"
        assert "fictional@password" not in repr(engine)
        assert engine.hide_parameters is True
    finally:
        engine.dispose()


def test_tenant_context_uses_bound_uuid_inside_one_transaction() -> None:
    connection = Mock()
    events = []

    @contextmanager
    def transaction():
        events.append("begin")
        try:
            yield connection
        except RuntimeError:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    engine = Mock()
    engine.begin.side_effect = transaction
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    with tenant_connection(engine, tenant_id) as active:
        assert active is connection
        assert events == ["begin"]
    statement, parameters = connection.execute.call_args.args
    assert str(statement) == "SELECT set_config('app.team_id', :team_id, true)"
    assert parameters == {"team_id": str(tenant_id)}
    assert events == ["begin", "commit"]

    with pytest.raises(RuntimeError), tenant_connection(engine, tenant_id):
        raise RuntimeError("fictional transaction failure")
    assert events[-2:] == ["begin", "rollback"]


def test_invalid_tenant_context_is_rejected_before_connection_checkout() -> None:
    engine = Mock()
    with pytest.raises(TypeError, match="UUID"), tenant_connection(engine, "not-a-uuid"):
        pass
    engine.begin.assert_not_called()


def test_runtime_role_cannot_invoke_owner_migrations() -> None:
    with pytest.raises(MigrationError, match="dedicated database owner"):
        run_migrations(Settings(database_user="aka_app"), "upgrade", "head")


def test_unknown_or_ambiguous_migration_operations_fail_before_connecting() -> None:
    owner = Settings(database_user="aka_owner")
    with pytest.raises(MigrationError, match="Unsupported"):
        run_migrations(owner, "erase")
    with pytest.raises(MigrationError, match="revision is required"):
        run_migrations(owner, "downgrade")


def test_migration_cli_does_not_echo_driver_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from knowledge_assistant import migrate

    def fail(*args, **kwargs):
        raise RuntimeError("fictional-credential-must-not-be-printed")

    monkeypatch.setattr(migrate, "run_migrations", fail)
    monkeypatch.setattr("sys.argv", ["migrate", "upgrade", "head"])
    assert migrate.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Migration failed; check database availability and configuration.\n"
