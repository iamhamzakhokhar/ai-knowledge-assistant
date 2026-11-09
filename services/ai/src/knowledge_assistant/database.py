"""Synchronous database access and the transaction boundary for tenant-scoped work."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import URL, Connection, Engine, create_engine, text

from knowledge_assistant.config import Settings
from knowledge_assistant.secret_files import read_secret_file


def create_engine_from_settings(settings: Settings) -> Engine:
    """Keep connection passwords in memory and SQL parameters out of logs/errors."""
    password = (
        read_secret_file(settings.database_password_file)
        if settings.database_password_file is not None
        else None
    )
    url = URL.create(
        "postgresql+psycopg",
        username=settings.database_user,
        password=password,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3, "application_name": "knowledge-assistant"},
    )


@contextmanager
def tenant_connection(engine: Engine, team_id: UUID) -> Iterator[Connection]:
    """Commit/rollback tenant work together; its context ends before pool reuse.

    The caller must obtain the team from trusted server-side authorization. This
    database boundary does not authenticate a client-supplied tenant identifier.
    """
    if not isinstance(team_id, UUID):
        raise TypeError("A tenant context requires a UUID")
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.team_id', :team_id, true)"),
            {"team_id": str(team_id)},
        )
        yield connection
