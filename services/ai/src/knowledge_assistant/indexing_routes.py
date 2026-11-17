"""Live team access to indexing status, reindexing, and active citations."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    Connection,
    RowMapping,
    and_,
    delete,
    func,
    insert,
    literal,
    select,
    union_all,
    update,
)
from sqlalchemy.exc import SQLAlchemyError

from knowledge_assistant.indexing_jobs import dispatch_index, stage_index
from knowledge_assistant.indexing_store import (
    MANAGER_ROLES,
    SourceKind,
    active_chunk_statement,
    ready_revision,
    source_row,
    source_scope,
    source_statement,
)
from knowledge_assistant.models import (
    source_index_chunks,
    source_index_states,
    source_index_versions,
)
from knowledge_assistant.secret_files import SecretFileError
from knowledge_assistant.workspace_store import authenticated_transaction, require_team, set_context

router = APIRouter(prefix="/api/v1/teams/{team_id}/index", tags=["index"])
_STATE = source_index_states


class IndexSource(BaseModel):
    kind: SourceKind
    id: UUID
    name: str
    status: Literal["unindexed", "queued", "processing", "completed", "failed", "removed"]
    attempt: int
    processed: int
    total: int
    error: str | None
    active_version_id: UUID | None
    updated_at: datetime


class IndexList(BaseModel):
    sources: list[IndexSource]


class IndexMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActiveCitation(BaseModel):
    kind: SourceKind
    id: UUID
    version_id: UUID
    anchor: str
    text: str
    page: int | None
    heading: str | None
    url: str | None
    title: str | None


def _summary_statement(team_id: UUID, kind: SourceKind, source_id: UUID | None = None):
    source = source_statement(kind).subquery()
    statement = (
        select(
            literal(kind).label("kind"),
            source.c.id,
            source.c.name,
            func.coalesce(_STATE.c.status, "unindexed").label("status"),
            func.coalesce(_STATE.c.attempt, 0).label("attempt"),
            func.coalesce(_STATE.c.processed, 0).label("processed"),
            func.coalesce(_STATE.c.total, 0).label("total"),
            _STATE.c.error,
            _STATE.c.active_version_id,
            _STATE.c.lease_until,
            func.greatest(_STATE.c.updated_at, source.c.extraction_updated_at).label("updated_at"),
            func.clock_timestamp().label("db_time"),
        )
        .select_from(
            source.outerjoin(
                _STATE,
                and_(
                    source.c.team_id == _STATE.c.team_id,
                    source.c.id == _STATE.c.source_id,
                    _STATE.c.source_kind == kind,
                ),
            )
        )
        .where(source.c.team_id == team_id)
    )
    if source_id is not None:
        statement = statement.where(source.c.id == source_id)
    return statement


def _summary(row: RowMapping) -> IndexSource:
    values = {key: row[key] for key in IndexSource.model_fields}
    if row["lease_until"] is not None and row["lease_until"] <= row["db_time"]:
        values.update(status="failed", error="attempt_expired")
    return IndexSource.model_validate(values)


def _read_summary(connection: Connection, team_id: UUID, kind: SourceKind, source_id: UUID):
    statement = _summary_statement(team_id, kind, source_id)
    row = connection.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Source not found")
    return _summary(row)


def _require_manager(connection: Connection, team_id: UUID, actor_id: UUID) -> None:
    if require_team(connection, team_id, actor_id, write=True) not in MANAGER_ROLES:
        raise HTTPException(403, "Owner or admin role required")


@router.get("")
def list_index(team_id: UUID, request: Request) -> IndexList:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        sources = union_all(
            _summary_statement(team_id, "document"), _summary_statement(team_id, "website")
        ).subquery()
        rows = connection.execute(
            select(sources).order_by(sources.c.updated_at.desc(), sources.c.kind, sources.c.id)
        ).mappings()
        return IndexList(sources=[_summary(row) for row in rows])


@router.get("/{kind}/{source_id}")
def index_detail(team_id: UUID, kind: SourceKind, source_id: UUID, request: Request) -> IndexSource:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        return _read_summary(connection, team_id, kind, source_id)


@router.post("/{kind}/{source_id}/reindex")
def reindex_source(
    team_id: UUID, kind: SourceKind, source_id: UUID, request: Request, data: IndexMutation
) -> IndexSource:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        current = _read_summary(connection, team_id, kind, source_id)
        if current.status in {"queued", "processing", "removed"}:
            raise HTTPException(409, "Source cannot be reindexed in its current state")
        source = source_row(connection, team_id, kind, source_id)
        revision = ready_revision(source, kind) if source is not None else None
        if revision is None:
            raise HTTPException(409, "Source extraction must complete before indexing")
        work = stage_index(connection, team_id, kind, source_id, actor_id, revision)
        if work is None:
            raise HTTPException(409, "Source cannot be reindexed in its current state")
    try:
        dispatch_index(
            request.app.state.workspace_store.engine(), request.app.state.workspace_settings, work
        )
    except SQLAlchemyError, SecretFileError, OSError:
        raise HTTPException(503, "Workspace service is unavailable") from None
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        return _read_summary(connection, team_id, kind, source_id)


@router.delete("/{kind}/{source_id}", status_code=204)
def remove_source(
    team_id: UUID, kind: SourceKind, source_id: UUID, request: Request, data: IndexMutation
) -> Response:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        current = _read_summary(connection, team_id, kind, source_id)
        removed = dict(
            requested_by=actor_id,
            status="removed",
            active_version_id=None,
            lease_until=None,
            error=None,
            processed=0,
            total=0,
            updated_at=func.clock_timestamp(),
        )
        if current.status == "unindexed":
            connection.execute(
                insert(_STATE).values(
                    team_id=team_id, source_kind=kind, source_id=source_id, attempt=1, **removed
                )
            )
        elif current.status != "removed":
            connection.execute(
                update(_STATE)
                .where(*source_scope(_STATE, team_id, kind, source_id))
                .values(**removed)
            )
        # Archived versions are only visible to the trusted service. The worker
        # and this purge share the team lock and the tombstone precedes deletion.
        set_context(connection, "app.user_id", "")
        versions = source_index_versions
        connection.execute(
            delete(versions).where(*source_scope(versions, team_id, kind, source_id))
        )
    return Response(status_code=204)


@router.get("/{kind}/{source_id}/versions/{version_id}/anchors/{anchor}")
def active_citation(
    team_id: UUID,
    kind: SourceKind,
    source_id: UUID,
    version_id: UUID,
    anchor: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
) -> ActiveCitation:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        chunks = source_index_chunks
        statement = (
            active_chunk_statement(team_id)
            .where(
                *source_scope(chunks, team_id, kind, source_id),
                chunks.c.version_id == version_id,
                chunks.c.anchor == anchor,
            )
            .with_only_columns(
                chunks.c.text, chunks.c.page, chunks.c.heading, chunks.c.url, chunks.c.title
            )
        )
        row = connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Active citation not found")
        return ActiveCitation(kind=kind, id=source_id, version_id=version_id, anchor=anchor, **row)
