"""Readiness is generic on failure and releases both dependency clients."""

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from knowledge_assistant import readiness
from knowledge_assistant.config import Settings


@pytest.fixture
def configured_settings() -> Settings:
    return Settings(
        database_password_file=Path("unused-database-fixture"),
        redis_password_file=Path("unused-redis-fixture"),
    )


def test_unconfigured_dependencies_are_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock()
    monkeypatch.setattr(readiness, "create_engine_from_settings", factory)
    assert not readiness.check_dependencies(
        Settings(database_password_file=None, redis_password_file=None)
    )
    factory.assert_not_called()


def test_readiness_requires_sql_and_redis_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch, configured_settings: Settings
) -> None:
    engine, client = MagicMock(), Mock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.return_value = 1
    client.ping.return_value = True
    database_factory = Mock(return_value=engine)
    redis_factory = Mock(return_value=client)
    monkeypatch.setattr(readiness, "create_engine_from_settings", database_factory)
    monkeypatch.setattr(readiness, "create_redis", redis_factory)

    assert readiness.check_dependencies(configured_settings)
    database_factory.assert_called_once_with(configured_settings)
    redis_factory.assert_called_once_with(configured_settings)
    assert str(connection.scalar.call_args.args[0]) == "SELECT 1"
    engine.connect.return_value.__exit__.assert_called_once()
    client.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_database_failure_is_generic_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    configured_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("synthetic-private-connection-marker")
    redis_factory = Mock()
    monkeypatch.setattr(readiness, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(readiness, "create_redis", redis_factory)

    assert not readiness.check_dependencies(configured_settings)
    engine.dispose.assert_called_once_with()
    redis_factory.assert_not_called()
    assert "synthetic-private-connection-marker" not in caplog.text


@pytest.mark.parametrize("ping_result", [False, RuntimeError("synthetic-private-redis-marker")])
def test_redis_failure_is_generic_and_closes_both_clients(
    monkeypatch: pytest.MonkeyPatch,
    configured_settings: Settings,
    ping_result: bool | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, client = MagicMock(), Mock()
    engine.connect.return_value.__enter__.return_value.scalar.return_value = 1
    if isinstance(ping_result, Exception):
        client.ping.side_effect = ping_result
    else:
        client.ping.return_value = ping_result
    monkeypatch.setattr(readiness, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(readiness, "create_redis", Mock(return_value=client))

    assert not readiness.check_dependencies(configured_settings)
    client.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()
    assert "synthetic-private-redis-marker" not in caplog.text


def test_unexpected_sql_result_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, configured_settings: Settings
) -> None:
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.scalar.return_value = 0
    redis_factory = Mock()
    monkeypatch.setattr(readiness, "create_engine_from_settings", Mock(return_value=engine))
    monkeypatch.setattr(readiness, "create_redis", redis_factory)

    assert not readiness.check_dependencies(configured_settings)
    redis_factory.assert_not_called()
    engine.dispose.assert_called_once_with()
