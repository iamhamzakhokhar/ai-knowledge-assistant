"""Live session and membership checks on the same transaction as protected queries."""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from sqlalchemy import Connection, Engine, delete, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError

from knowledge_assistant.auth_security import SESSION_SECONDS, session_digest, token_digest
from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings
from knowledge_assistant.models import auth_sessions, auth_users, team_memberships, teams
from knowledge_assistant.secret_files import SecretFileError


class WorkspaceStore:
    """Lazy per-app engine, so liveness can run without a configured database."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None
        self._lock = Lock()

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                if self.settings.database_user != "aka_app":
                    raise HTTPException(503, "Workspace service is unavailable")
                self._engine = create_engine_from_settings(self.settings)
            return self._engine

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


@contextmanager
def workspace_transaction(request: Request) -> Iterator[Connection]:
    try:
        with request.app.state.workspace_store.engine().begin() as connection:
            yield connection
    except SQLAlchemyError, SecretFileError, OSError:
        raise HTTPException(503, "Workspace service is unavailable") from None


def set_context(connection: Connection, setting: str, value: str) -> None:
    connection.execute(
        text("SELECT set_config(:setting, :value, true)"), {"setting": setting, "value": value}
    )


def lock_scope(connection: Connection, key: str, *, exclusive: bool = False) -> None:
    # App operations take the session lock first, then the team lock. Shared
    # reads can run together; logout/role changes wait for in-flight operations.
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    connection.execute(text(f"SELECT {function}(hashtextextended(:key, 0))"), {"key": key})


def revoke_cookie_session(connection: Connection, request: Request) -> None:
    digest = session_digest(request)
    if digest is not None:
        lock_scope(connection, f"aka:session:{digest.hex()}", exclusive=True)
        set_context(connection, "app.session_hash", digest.hex())
        connection.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == digest))


def issue_session(connection: Connection, request: Request, user_id: UUID) -> str:
    revoke_cookie_session(connection, request)
    connection.execute(
        delete(auth_sessions).where(
            auth_sessions.c.user_id == user_id, auth_sessions.c.expires_at <= func.clock_timestamp()
        )
    )
    token = secrets.token_urlsafe(32)
    connection.execute(
        insert(auth_sessions).values(
            token_hash=token_digest(token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS),
        )
    )
    return token


def require_user(connection: Connection, digest: bytes) -> tuple[UUID, str]:
    lock_scope(connection, f"aka:session:{digest.hex()}")
    set_context(connection, "app.session_hash", digest.hex())
    user_id = connection.scalar(
        select(auth_sessions.c.user_id).where(
            auth_sessions.c.token_hash == digest,
            auth_sessions.c.expires_at > func.clock_timestamp(),
        )
    )
    if user_id is None:
        raise HTTPException(401, "Sign in required")
    set_context(connection, "app.user_id", str(user_id))
    email = connection.scalar(select(auth_users.c.email).where(auth_users.c.id == user_id))
    if email is None:
        raise HTTPException(401, "Sign in required")
    return user_id, email


@contextmanager
def authenticated_transaction(request: Request) -> Iterator[tuple[Connection, UUID, str]]:
    digest = session_digest(request)
    if digest is None:
        raise HTTPException(401, "Sign in required")
    with workspace_transaction(request) as connection:
        user_id, email = require_user(connection, digest)
        yield connection, user_id, email


def member_role(connection: Connection, team_id: UUID, user_id: UUID) -> str | None:
    return connection.scalar(
        select(team_memberships.c.role).where(
            team_memberships.c.team_id == team_id, team_memberships.c.user_id == user_id
        )
    )


def require_team(
    connection: Connection, team_id: UUID, user_id: UUID, *, write: bool = False
) -> str:
    lock_scope(connection, f"aka:team:{team_id}", exclusive=write)
    # Actor-only RLS discovery comes before selecting a tenant context. Never
    # set the team from a request UUID without a live membership in this transaction.
    role = member_role(connection, team_id, user_id)
    if role is None:
        raise HTTPException(404, "Team not found")
    set_context(connection, "app.team_id", str(team_id))
    set_context(connection, "app.team_role", role)
    return role


def create_team(connection: Connection, user_id: UUID, name: str) -> dict[str, object]:
    team_id = uuid4()
    set_context(connection, "app.team_id", str(team_id))
    set_context(connection, "app.team_bootstrap", "1")
    connection.execute(insert(teams).values(id=team_id, name=name))
    connection.execute(
        insert(team_memberships).values(team_id=team_id, user_id=user_id, role="owner")
    )
    set_context(connection, "app.team_bootstrap", "")
    return {"id": team_id, "name": name, "role": "owner"}


def list_teams(connection: Connection, user_id: UUID) -> list[dict[str, object]]:
    rows = connection.execute(
        select(teams.c.id, teams.c.name, team_memberships.c.role)
        .join(team_memberships, team_memberships.c.team_id == teams.c.id)
        .where(team_memberships.c.user_id == user_id)
        .order_by(teams.c.created_at, teams.c.id)
    ).mappings()
    return [dict(row) for row in rows]
