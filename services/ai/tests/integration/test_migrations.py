"""Run reversible migrations only inside a uniquely created disposable database."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import text

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.seed import DEMO_TEAM_ID, OTHER_TEAM_ID, seed_demo

pytestmark = pytest.mark.skipif(
    os.environ.get("AKA_INTEGRATION_TESTS") != "1",
    reason="Migration roundtrips require AKA_INTEGRATION_TESTS=1 and an isolated database",
)


def migrate(database_name: str, *arguments: str) -> None:
    assert database_name.startswith("aka_test_") and len(database_name) == 41
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_NAME": database_name,
            "DATABASE_USER": "aka_owner",
            "DATABASE_PASSWORD_FILE": os.environ["ADMIN_DATABASE_PASSWORD_FILE"],
            "APP_DATABASE_PASSWORD_FILE": os.environ["DATABASE_PASSWORD_FILE"],
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "knowledge_assistant.migrate", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (
        f"Migration {' '.join(arguments)} failed in disposable test database"
    )


def test_migrations_roundtrip_and_seed_remains_idempotent(
    integration_settings: Settings, admin_connection, disposable_database: str
) -> None:
    database_name = disposable_database
    assert database_name != integration_settings.database_name
    migrate(database_name, "upgrade", "head")
    settings = integration_settings.model_copy(update={"database_name": database_name})
    engine = create_engine_from_settings(settings)
    try:
        seed_demo(engine)
        first_seed = {}
        for team_id in (DEMO_TEAM_ID, OTHER_TEAM_ID):
            with tenant_connection(engine, team_id) as connection:
                assert connection.scalars(text("SELECT id FROM teams")).all() == [team_id]
                sources = (
                    connection.execute(
                        text("SELECT id, team_id, name, content, created_at FROM knowledge_sources")
                    )
                    .mappings()
                    .all()
                )
                assert len(sources) == 1
                assert sources[0]["team_id"] == team_id
                first_seed[team_id] = [dict(row) for row in sources]
        seed_demo(engine)
        for team_id in (DEMO_TEAM_ID, OTHER_TEAM_ID):
            with tenant_connection(engine, team_id) as connection:
                assert connection.scalars(text("SELECT id FROM teams")).all() == [team_id]
                sources = (
                    connection.execute(
                        text("SELECT id, team_id, name, content, created_at FROM knowledge_sources")
                    )
                    .mappings()
                    .all()
                )
                assert [dict(row) for row in sources] == first_seed[team_id]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM knowledge_sources")) == 0
    finally:
        engine.dispose()

    migrate(database_name, "downgrade", "base")
    # Catalog visibility inspects this disposable database without another admin connection.
    engine = create_engine_from_settings(settings)
    try:
        with engine.connect() as connection:
            for table in (
                "teams",
                "knowledge_sources",
                "job_runs",
                "auth_users",
                "auth_credentials",
                "auth_sessions",
                "team_memberships",
                "team_invites",
                "documents",
                "document_originals",
            ):
                assert (
                    connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None
                )
    finally:
        engine.dispose()

    migrate(database_name, "upgrade", "head")
    engine = create_engine_from_settings(settings)
    try:
        seed_demo(engine)
        with tenant_connection(engine, DEMO_TEAM_ID) as connection:
            assert connection.scalars(text("SELECT id FROM teams")).all() == [DEMO_TEAM_ID]
            assert (
                connection.scalar(
                    text("SELECT id FROM teams WHERE id = :id"), {"id": OTHER_TEAM_ID}
                )
                is None
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM teams")) == 0
    finally:
        engine.dispose()
