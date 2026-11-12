"""Opt-in fixtures for real services; never provision or connect during unit tests."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from redis import Redis
from sqlalchemy.engine import Engine

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings
from knowledge_assistant.queueing import create_redis


@dataclass(frozen=True)
class TenantFixtures:
    team_a: UUID
    team_b: UUID
    source_a: UUID
    source_b: UUID
    job_a: UUID
    job_b: UUID

    def row_ids(self, table: str) -> tuple[UUID, UUID]:
        return {
            "teams": (self.team_a, self.team_b),
            "knowledge_sources": (self.source_a, self.source_b),
            "job_runs": (self.job_a, self.job_b),
        }[table]


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    if os.environ.get("AKA_INTEGRATION_TESTS") != "1":
        pytest.skip("Real services require AKA_INTEGRATION_TESTS=1")
    settings = Settings()
    assert settings.database_user == "aka_app", "Integration reads must use the application role"
    return settings


@pytest.fixture(scope="session")
def app_engine(integration_settings: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(integration_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def admin_connection(integration_settings: Settings) -> Iterator[psycopg.Connection]:
    """Read exactly the mounted admin secret, without putting its value into a URL or argv."""
    secret_file = os.environ.get("ADMIN_DATABASE_PASSWORD_FILE")
    assert secret_file, "Mount an admin password file for isolated integration-test setup"
    with psycopg.connect(
        host=integration_settings.database_host,
        port=integration_settings.database_port,
        dbname=integration_settings.database_name,
        user=os.environ.get("ADMIN_DATABASE_USER", "aka_owner"),
        password=Path(secret_file).read_text().strip(),
        autocommit=True,
    ) as connection:
        yield connection


@pytest.fixture
def tenant_rows(admin_connection: psycopg.Connection) -> Iterator[TenantFixtures]:
    rows = TenantFixtures(*(uuid4() for _ in range(6)))
    try:
        for label, team_id, source_id, job_id in (
            ("A", rows.team_a, rows.source_a, rows.job_a),
            ("B", rows.team_b, rows.source_b, rows.job_b),
        ):
            admin_connection.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)",
                (team_id, f"Fictional integration team {label}"),
            )
            admin_connection.execute(
                "INSERT INTO knowledge_sources (id, team_id, name, content) "
                "VALUES (%s, %s, %s, %s)",
                (
                    source_id,
                    team_id,
                    f"Example guide {label}",
                    f"Fictional content for team {label}",
                ),
            )
            admin_connection.execute(
                "INSERT INTO job_runs (id, team_id, kind, status) VALUES (%s, %s, %s, %s)",
                (job_id, team_id, "integration_fixture", "queued"),
            )
        yield rows
    finally:
        # Only the random tenants created by this fixture are eligible for cleanup.
        for table in ("job_runs", "knowledge_sources"):
            admin_connection.execute(
                sql.SQL("DELETE FROM {} WHERE team_id IN (%s, %s)").format(sql.Identifier(table)),
                (rows.team_a, rows.team_b),
            )
        admin_connection.execute(
            "DELETE FROM teams WHERE id IN (%s, %s)", (rows.team_a, rows.team_b)
        )


@pytest.fixture
def redis_connection(integration_settings: Settings) -> Iterator[Redis]:
    connection = create_redis(integration_settings)
    assert connection.ping() is True
    try:
        yield connection
    finally:
        connection.close()


def _temporary_database(admin_connection: psycopg.Connection) -> Iterator[str]:
    """Create and drop one uniquely named database, never the shared application database."""
    name = f"aka_test_{uuid4().hex}"
    assert name.startswith("aka_test_") and len(name) == 41
    admin_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        admin_connection.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def disposable_database(admin_connection: psycopg.Connection) -> Iterator[str]:
    yield from _temporary_database(admin_connection)


@pytest.fixture(scope="module")
def auth_disposable_database(admin_connection: psycopg.Connection) -> Iterator[str]:
    yield from _temporary_database(admin_connection)
