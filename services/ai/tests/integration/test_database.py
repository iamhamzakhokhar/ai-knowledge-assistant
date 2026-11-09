"""Exercise the application role and transaction boundary against real PostgreSQL."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection

pytestmark = pytest.mark.skipif(
    os.environ.get("AKA_INTEGRATION_TESTS") != "1",
    reason="Real PostgreSQL tests require AKA_INTEGRATION_TESTS=1",
)

TABLES = ("teams", "knowledge_sources", "job_runs")


def assert_permission_denied(connection: Connection, statement, parameters=None) -> None:
    """Rollback even when a permission regression unexpectedly permits the statement."""
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(DBAPIError) as raised:
            connection.execute(statement, parameters or {})
        assert raised.value.orig.sqlstate == "42501"
    finally:
        savepoint.rollback()


def test_pgvector_extension_executes_a_vector_distance(app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        assert connection.scalar(
            text("SELECT '[1,0,0]'::vector <-> '[0,0,0]'::vector")
        ) == pytest.approx(1.0)


def test_application_role_cannot_bypass_rls_or_own_tenant_tables(app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        role = (
            connection.execute(
                text(
                    "SELECT current_user AS name, rolsuper, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
            .mappings()
            .one()
        )
        assert dict(role) == {
            "name": "aka_app",
            "rolsuper": False,
            "rolbypassrls": False,
            "rolinherit": False,
        }
        tables = (
            connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity, "
                    "pg_get_userbyid(relowner) AS owner FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace "
                    "AND relname IN ('teams', 'knowledge_sources', 'job_runs')"
                )
            )
            .mappings()
            .all()
        )
        assert {row["relname"] for row in tables} == set(TABLES)
        for row in tables:
            assert row["relrowsecurity"] is True
            assert row["relforcerowsecurity"] is True
            assert row["owner"] != "aka_app"
            assert (
                connection.scalar(
                    text("SELECT pg_has_role(current_user, :owner, 'MEMBER')"),
                    {"owner": row["owner"]},
                )
                is False
            )


@pytest.mark.parametrize("table", TABLES)
def test_missing_tenant_context_cannot_read_rows(
    app_engine: Engine, tenant_rows, table: str
) -> None:
    with app_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT NULLIF(current_setting('app.team_id', true), '')"))
            is None
        )
        assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize("table", TABLES)
def test_tenant_reads_exclude_other_tenants(app_engine: Engine, tenant_rows, table: str) -> None:
    own_id, other_id = tenant_rows.row_ids(table)
    with tenant_connection(app_engine, tenant_rows.team_a) as connection:
        assert connection.scalars(text(f"SELECT id FROM {table}")).all() == [own_id]
        assert (
            connection.scalar(text(f"SELECT id FROM {table} WHERE id = :id"), {"id": other_id})
            is None
        )
    with tenant_connection(app_engine, tenant_rows.team_b) as connection:
        assert (
            connection.scalar(text(f"SELECT id FROM {table} WHERE id = :id"), {"id": other_id})
            == other_id
        )


@pytest.mark.parametrize("table", TABLES)
def test_tenant_cannot_insert_rows_for_another_tenant(
    app_engine: Engine, tenant_rows, table: str
) -> None:
    statement = {
        "teams": "INSERT INTO teams (id, name) VALUES (:id, 'Forbidden example tenant')",
        "knowledge_sources": "INSERT INTO knowledge_sources (id, team_id, name, content) "
        "VALUES (:id, :team_id, 'Forbidden example source', 'Fictional content')",
        "job_runs": "INSERT INTO job_runs (id, team_id, kind, status) "
        "VALUES (:id, :team_id, 'forbidden_example', 'queued')",
    }[table]
    with tenant_connection(app_engine, tenant_rows.team_a) as connection:
        assert_permission_denied(
            connection, text(statement), {"id": uuid4(), "team_id": tenant_rows.team_b}
        )


@pytest.mark.parametrize("table", TABLES)
def test_tenant_cannot_update_or_delete_other_tenants_rows(
    app_engine: Engine, tenant_rows, table: str
) -> None:
    _, other_id = tenant_rows.row_ids(table)
    column = "kind" if table == "job_runs" else "name"
    with tenant_connection(app_engine, tenant_rows.team_a) as connection:
        updated = connection.execute(
            text(f"UPDATE {table} SET {column} = 'Forbidden change' WHERE id = :id"),
            {"id": other_id},
        )
        assert updated.rowcount == 0
        deleted = connection.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": other_id})
        assert deleted.rowcount == 0
    with tenant_connection(app_engine, tenant_rows.team_b) as connection:
        value = connection.scalar(
            text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": other_id}
        )
        assert value is not None and value != "Forbidden change"


@pytest.mark.parametrize("table", ("knowledge_sources", "job_runs"))
def test_tenant_cannot_reassign_its_rows_to_another_tenant(
    app_engine: Engine, tenant_rows, table: str
) -> None:
    own_id, _ = tenant_rows.row_ids(table)
    with tenant_connection(app_engine, tenant_rows.team_a) as connection:
        assert_permission_denied(
            connection,
            text(f"UPDATE {table} SET team_id = :team_id WHERE id = :id"),
            {"team_id": tenant_rows.team_b, "id": own_id},
        )


def test_tenant_context_is_local_to_the_reused_pooled_connection(
    integration_settings: Settings, tenant_rows
) -> None:
    engine = create_engine_from_settings(integration_settings)
    try:
        # No concurrent checkout occurs: QueuePool returns this same connection each time.
        with tenant_connection(engine, tenant_rows.team_a) as connection:
            backend_pid = connection.scalar(text("SELECT pg_backend_pid()"))
            assert connection.scalar(text("SELECT id FROM teams")) == tenant_rows.team_a
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT pg_backend_pid()")) == backend_pid
            assert (
                connection.scalar(text("SELECT NULLIF(current_setting('app.team_id', true), '')"))
                is None
            )
            assert connection.scalar(text("SELECT count(*) FROM teams")) == 0
        with tenant_connection(engine, tenant_rows.team_b) as connection:
            assert connection.scalar(text("SELECT pg_backend_pid()")) == backend_pid
            assert connection.scalar(text("SELECT id FROM teams")) == tenant_rows.team_b
        with pytest.raises(RuntimeError, match="Rollback the fictional transaction"):
            with tenant_connection(engine, tenant_rows.team_a) as connection:
                assert connection.scalar(text("SELECT pg_backend_pid()")) == backend_pid
                raise RuntimeError("Rollback the fictional transaction")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT pg_backend_pid()")) == backend_pid
            assert connection.scalar(text("SELECT count(*) FROM teams")) == 0
    finally:
        engine.dispose()


def test_application_role_cannot_create_tables_or_read_migration_state(app_engine: Engine) -> None:
    with app_engine.begin() as connection:
        assert_permission_denied(
            connection, text(f"CREATE TABLE public.aka_test_forbidden_{uuid4().hex} (id integer)")
        )
        assert_permission_denied(connection, text("SELECT version_num FROM public.alembic_version"))
