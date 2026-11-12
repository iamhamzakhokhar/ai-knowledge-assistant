"""Account creation, password authentication, and revocable cookie sessions."""

import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from knowledge_assistant.auth_security import (
    check_auth_attempt,
    clear_session_cookie,
    hash_password,
    session_digest,
    verify_password,
    write_session_cookie,
)
from knowledge_assistant.models import auth_credentials, auth_users
from knowledge_assistant.workspace_store import (
    authenticated_transaction,
    create_team,
    issue_session,
    list_teams,
    revoke_cookie_session,
    set_context,
    workspace_transaction,
)

router = APIRouter(prefix="/api/v1/auth", tags=["accounts"])
TeamRole = Literal["owner", "admin", "member"]


def _valid_team_name(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid team name") from None
    if "\x00" in value:
        raise ValueError("Invalid team name")
    return value


TeamName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    AfterValidator(_valid_team_name),
]


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.isascii() or not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", normalized
        ):
            raise ValueError("Invalid email")
        return normalized


class LoginInput(EmailInput):
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def encodable_password(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Invalid password") from None
        return value


class SignupInput(LoginInput):
    password: str = Field(min_length=15, max_length=128, repr=False)
    team_name: TeamName | None = None


class Account(BaseModel):
    id: UUID
    email: str


class TeamSummary(BaseModel):
    id: UUID
    name: str
    role: TeamRole


class SessionResponse(BaseModel):
    user: Account
    teams: list[TeamSummary]


def _session_payload(connection: Connection, user_id: UUID, email: str) -> dict[str, object]:
    return {"user": {"id": user_id, "email": email}, "teams": list_teams(connection, user_id)}


@router.post("/signup", status_code=201, response_model=SessionResponse)
def signup(data: SignupInput, request: Request, response: Response) -> dict[str, object]:
    check_auth_attempt(request, data.email)
    hashed = hash_password(data.password)
    user_id = uuid4()
    with workspace_transaction(request) as connection:
        set_context(connection, "app.auth_email", data.email)
        set_context(connection, "app.user_id", str(user_id))
        created = connection.scalar(
            pg_insert(auth_users)
            .values(id=user_id, email=data.email)
            .on_conflict_do_nothing(index_elements=[auth_users.c.email])
            .returning(auth_users.c.id)
        )
        if created is None:
            raise HTTPException(409, "Unable to create account")
        connection.execute(insert(auth_credentials).values(user_id=user_id, password_hash=hashed))
        token = issue_session(connection, request, user_id)
        if data.team_name is not None:
            create_team(connection, user_id, data.team_name)
        payload = _session_payload(connection, user_id, data.email)
    write_session_cookie(request, response, token)
    return payload


@router.post("/login", response_model=SessionResponse)
def login(data: LoginInput, request: Request, response: Response) -> dict[str, object]:
    check_auth_attempt(request, data.email)
    with workspace_transaction(request) as connection:
        set_context(connection, "app.auth_email", data.email)
        found = connection.scalar(select(auth_users.c.id).where(auth_users.c.email == data.email))
        user_id = found if found is not None else UUID(int=0)
        set_context(connection, "app.user_id", str(user_id))
        hashed = connection.scalar(
            select(auth_credentials.c.password_hash).where(auth_credentials.c.user_id == user_id)
        )
        if not verify_password(data.password, hashed) or found is None:
            raise HTTPException(401, "Invalid email or password")
        token = issue_session(connection, request, user_id)
        payload = _session_payload(connection, user_id, data.email)
    write_session_cookie(request, response, token)
    return payload


@router.get("/session", response_model=SessionResponse)
def session(request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, email):
        return _session_payload(connection, user_id, email)


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    if session_digest(request) is not None:
        with workspace_transaction(request) as connection:
            revoke_cookie_session(connection, request)
    response = Response(status_code=204)
    clear_session_cookie(request, response)
    return response
