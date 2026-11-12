"""Team membership, single-use invitations, and scoped read-only resource routes."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from knowledge_assistant.auth_routes import Account, EmailInput, TeamName, TeamRole, TeamSummary
from knowledge_assistant.auth_security import INVITE_SECONDS, TOKEN_PATTERN, token_digest
from knowledge_assistant.models import (
    auth_users,
    job_runs,
    knowledge_sources,
    team_invites,
    team_memberships,
    teams,
)
from knowledge_assistant.workspace_store import (
    authenticated_transaction,
    create_team,
    list_teams,
    lock_scope,
    member_role,
    require_team,
    set_context,
)

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])
AssignableRole = Literal["admin", "member"]


class TeamInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: TeamName


class InviteInput(EmailInput):
    role: AssignableRole


class JoinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Annotated[str, Field(pattern=TOKEN_PATTERN, repr=False)]


class RoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: AssignableRole


class TeamList(BaseModel):
    teams: list[TeamSummary]


class Member(Account):
    role: TeamRole


class MemberList(BaseModel):
    members: list[Member]


class InviteResult(BaseModel):
    token: str = Field(repr=False)
    expires_at: datetime
    email: str
    role: AssignableRole


class Source(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class SourceList(BaseModel):
    sources: list[Source]


class Job(BaseModel):
    id: UUID
    kind: str
    status: str
    result: JsonValue
    created_at: datetime
    updated_at: datetime


class JobList(BaseModel):
    jobs: list[Job]


def _can_assign(actor: str | None, target: str) -> bool:
    return actor == "owner" or (actor == "admin" and target == "member")


def _read_member(connection: Connection, team_id: UUID, user_id: UUID) -> dict[str, object]:
    result = (
        connection.execute(
            select(auth_users.c.id, auth_users.c.email, team_memberships.c.role)
            .join(team_memberships, team_memberships.c.user_id == auth_users.c.id)
            .where(team_memberships.c.team_id == team_id, team_memberships.c.user_id == user_id)
        )
        .mappings()
        .one_or_none()
    )
    if result is None:
        raise HTTPException(404, "Member not found")
    return dict(result)


@router.get("", response_model=TeamList)
def get_teams(request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        return {"teams": list_teams(connection, user_id)}


@router.post("", status_code=201, response_model=TeamSummary)
def add_team(data: TeamInput, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        return create_team(connection, user_id, data.name)


@router.post("/join", response_model=TeamSummary)
def join(data: JoinInput, request: Request) -> dict[str, object]:
    digest = token_digest(data.token)
    with authenticated_transaction(request) as (connection, user_id, email):
        # Before membership exists, only the digest and email can reveal the invite.
        set_context(connection, "app.auth_email", email)
        set_context(connection, "app.invite_hash", digest.hex())
        pending = select(
            team_invites.c.team_id, team_invites.c.role, team_invites.c.created_by
        ).where(
            team_invites.c.token_hash == digest,
            team_invites.c.email == email,
            team_invites.c.used_at.is_(None),
            team_invites.c.expires_at > func.clock_timestamp(),
        )
        team_id = connection.scalar(pending)
        if team_id is None:
            raise HTTPException(404, "Invitation not found")
        lock_scope(connection, f"aka:team:{team_id}", exclusive=True)
        invite = connection.execute(pending).mappings().one_or_none()
        if invite is None:
            raise HTTPException(404, "Invitation not found")
        set_context(connection, "app.team_id", str(team_id))
        # A removed or demoted inviter cannot confer a role they can no longer assign.
        inviter_role = member_role(connection, team_id, invite["created_by"])
        if not _can_assign(inviter_role, invite["role"]):
            raise HTTPException(404, "Invitation not found")
        added = connection.scalar(
            pg_insert(team_memberships)
            .values(team_id=team_id, user_id=user_id, role=invite["role"])
            .on_conflict_do_nothing(
                index_elements=[team_memberships.c.team_id, team_memberships.c.user_id]
            )
            .returning(team_memberships.c.user_id)
        )
        if added is None:
            raise HTTPException(409, "Already a member of this team")
        consumed = connection.execute(
            update(team_invites)
            .where(team_invites.c.token_hash == digest, team_invites.c.used_at.is_(None))
            .values(used_at=func.clock_timestamp())
        )
        if consumed.rowcount != 1:
            raise HTTPException(404, "Invitation not found")
        name = connection.scalar(select(teams.c.name).where(teams.c.id == team_id))
        return {"id": team_id, "name": name, "role": invite["role"]}


@router.get("/{team_id}/members", response_model=MemberList)
def members(team_id: UUID, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        require_team(connection, team_id, user_id)
        rows = connection.execute(
            select(auth_users.c.id, auth_users.c.email, team_memberships.c.role)
            .join(team_memberships, team_memberships.c.user_id == auth_users.c.id)
            .where(team_memberships.c.team_id == team_id)
            .order_by(auth_users.c.email)
        ).mappings()
        return {"members": [dict(row) for row in rows]}


@router.post("/{team_id}/invites", status_code=201, response_model=InviteResult)
def invite_member(team_id: UUID, data: InviteInput, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        actor_role = require_team(connection, team_id, user_id, write=True)
        if not _can_assign(actor_role, data.role):
            raise HTTPException(403, "Insufficient team role")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=INVITE_SECONDS)
        connection.execute(
            insert(team_invites).values(
                id=uuid4(),
                team_id=team_id,
                email=data.email,
                role=data.role,
                token_hash=token_digest(token),
                created_by=user_id,
                expires_at=expires_at,
            )
        )
        return {"token": token, "expires_at": expires_at, "email": data.email, "role": data.role}


def _managed_member(
    connection: Connection, team_id: UUID, actor_id: UUID, member_id: UUID
) -> tuple[str, dict[str, object]]:
    actor_role = require_team(connection, team_id, actor_id, write=True)
    if actor_role == "member":
        raise HTTPException(403, "Insufficient team role")
    target = _read_member(connection, team_id, member_id)
    target_role = str(target["role"])
    if target_role == "owner" or not _can_assign(actor_role, target_role):
        raise HTTPException(403, "Insufficient team role")
    return actor_role, target


@router.patch("/{team_id}/members/{member_id}", response_model=Member)
def change_role(
    team_id: UUID, member_id: UUID, data: RoleInput, request: Request
) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        actor_role, target = _managed_member(connection, team_id, user_id, member_id)
        if not _can_assign(actor_role, data.role):
            raise HTTPException(403, "Insufficient team role")
        changed = connection.execute(
            update(team_memberships)
            .where(team_memberships.c.team_id == team_id, team_memberships.c.user_id == member_id)
            .values(role=data.role)
        )
        if changed.rowcount != 1:
            raise HTTPException(403, "Insufficient team role")
        return {**target, "role": data.role}


@router.delete("/{team_id}/members/{member_id}", status_code=204)
def remove_member(team_id: UUID, member_id: UUID, request: Request) -> Response:
    with authenticated_transaction(request) as (connection, user_id, _):
        _, target = _managed_member(connection, team_id, user_id, member_id)
        # Close outstanding invitations while the target membership is still
        # visible to the manager policy. This and removal commit together.
        connection.execute(
            update(team_invites)
            .where(
                team_invites.c.team_id == team_id,
                team_invites.c.email == target["email"],
                team_invites.c.used_at.is_(None),
            )
            .values(used_at=func.clock_timestamp())
        )
        removed = connection.execute(
            delete(team_memberships).where(
                team_memberships.c.team_id == team_id, team_memberships.c.user_id == member_id
            )
        )
        if removed.rowcount != 1:
            raise HTTPException(403, "Insufficient team role")
    return Response(status_code=204)


_SOURCE_FIELDS = (knowledge_sources.c.id, knowledge_sources.c.name, knowledge_sources.c.created_at)
_JOB_FIELDS = (
    job_runs.c.id,
    job_runs.c.kind,
    job_runs.c.status,
    job_runs.c.result,
    job_runs.c.created_at,
    job_runs.c.updated_at,
)


@router.get("/{team_id}/sources", response_model=SourceList)
def sources(team_id: UUID, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        require_team(connection, team_id, user_id)
        rows = connection.execute(
            select(*_SOURCE_FIELDS)
            .where(knowledge_sources.c.team_id == team_id)
            .order_by(knowledge_sources.c.created_at, knowledge_sources.c.id)
        ).mappings()
        return {"sources": [dict(row) for row in rows]}


@router.get("/{team_id}/sources/{source_id}", response_model=Source)
def source(team_id: UUID, source_id: UUID, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        require_team(connection, team_id, user_id)
        row = (
            connection.execute(
                select(*_SOURCE_FIELDS).where(
                    knowledge_sources.c.team_id == team_id, knowledge_sources.c.id == source_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Source not found")
        return dict(row)


@router.get("/{team_id}/jobs", response_model=JobList)
def jobs(team_id: UUID, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        require_team(connection, team_id, user_id)
        rows = connection.execute(
            select(*_JOB_FIELDS)
            .where(job_runs.c.team_id == team_id)
            .order_by(job_runs.c.created_at, job_runs.c.id)
        ).mappings()
        return {"jobs": [dict(row) for row in rows]}


@router.get("/{team_id}/jobs/{job_id}", response_model=Job)
def job(team_id: UUID, job_id: UUID, request: Request) -> dict[str, object]:
    with authenticated_transaction(request) as (connection, user_id, _):
        require_team(connection, team_id, user_id)
        row = (
            connection.execute(
                select(*_JOB_FIELDS).where(job_runs.c.team_id == team_id, job_runs.c.id == job_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Job not found")
        return dict(row)
