"""Tenant-scoped website imports, crawl snapshots, and bounded recrawls."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import Connection, RowMapping, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from knowledge_assistant.models import websites
from knowledge_assistant.secret_files import SecretFileError
from knowledge_assistant.website_crawler import (
    MAX_URL_LENGTH,
    CrawlInputError,
    normalize_website_url,
)
from knowledge_assistant.website_jobs import (
    MANAGER_ROLES,
    MAX_WEBSITE_ATTEMPTS,
    QUEUE_LEASE_SECONDS,
    WebsitePage,
    WebsiteQueueError,
    enqueue_website,
    mark_dispatch_failed,
)
from knowledge_assistant.workspace_store import authenticated_transaction, require_team

router = APIRouter(prefix="/api/v1/teams/{team_id}/websites", tags=["websites"])

_SUMMARY_COLUMNS = (
    websites.c.id,
    websites.c.url,
    websites.c.sitemap,
    websites.c.status,
    websites.c.attempt,
    websites.c.error,
    websites.c.updated_at,
    websites.c.lease_until,
    func.jsonb_array_length(websites.c.working_pages).label("processed"),
    func.clock_timestamp().label("db_time"),
)
_DETAIL_COLUMNS = (
    *_SUMMARY_COLUMNS,
    websites.c.pages,
    websites.c.published_attempt,
    websites.c.working_pages,
)


class WebsiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: Annotated[str, Field(min_length=1, max_length=MAX_URL_LENGTH, repr=False)]
    sitemap: StrictBool = False


class RecrawlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebsiteSummary(BaseModel):
    id: UUID
    url: str
    sitemap: bool
    status: Literal["queued", "processing", "completed", "failed"]
    attempt: int
    processed: int
    error: str | None
    updated_at: datetime


class WebsiteDetail(WebsiteSummary):
    published_attempt: int | None
    pages: list[WebsitePage]
    last_attempt_pages: list[WebsitePage]


class WebsiteList(BaseModel):
    websites: list[WebsiteSummary]


def _summary(row: RowMapping) -> WebsiteSummary:
    values = {key: row[key] for key in WebsiteSummary.model_fields}
    if row["lease_until"] is not None and row["lease_until"] <= row["db_time"]:
        values.update(status="failed", error="attempt_expired")
    return WebsiteSummary.model_validate(values)


def _read_website(
    connection: Connection,
    team_id: UUID,
    website_id: UUID,
    *,
    detail: bool = False,
    for_update: bool = False,
) -> RowMapping:
    columns = _DETAIL_COLUMNS if detail else _SUMMARY_COLUMNS
    statement = select(*columns).where(websites.c.team_id == team_id, websites.c.id == website_id)
    if for_update:
        statement = statement.with_for_update()
    row = connection.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Website not found")
    return row


def _require_manager(connection: Connection, team_id: UUID, actor_id: UUID) -> None:
    if require_team(connection, team_id, actor_id, write=True) not in MANAGER_ROLES:
        raise HTTPException(403, "Owner or admin role required")


def _dispatch(request: Request, team_id: UUID, website_id: UUID, attempt: int) -> WebsiteSummary:
    # The crawl request is durable before an RQ worker can claim it.
    try:
        enqueue_website(request.app.state.workspace_settings, team_id, website_id, attempt)
    except WebsiteQueueError:
        try:
            engine = request.app.state.workspace_store.engine()
            mark_dispatch_failed(engine, team_id, website_id, attempt)
        except SQLAlchemyError, SecretFileError, OSError:
            raise HTTPException(503, "Workspace service is unavailable") from None
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        return _summary(_read_website(connection, team_id, website_id))


@router.get("")
def list_websites(team_id: UUID, request: Request) -> WebsiteList:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        rows = connection.execute(
            select(*_SUMMARY_COLUMNS)
            .where(websites.c.team_id == team_id)
            .order_by(websites.c.updated_at.desc(), websites.c.id)
        ).mappings()
        return WebsiteList(websites=[_summary(row) for row in rows])


@router.post("", status_code=201, responses={200: {"model": WebsiteSummary}})
def submit_website(
    team_id: UUID, request: Request, response: Response, data: WebsiteInput
) -> WebsiteSummary:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        try:
            url = normalize_website_url(data.url)
        except CrawlInputError:
            raise HTTPException(422, "Enter a valid public HTTP or HTTPS URL") from None
        website_id = connection.scalar(
            pg_insert(websites)
            .values(
                team_id=team_id,
                id=uuid4(),
                url=url,
                sitemap=data.sitemap,
                requested_by=actor_id,
                status="queued",
                attempt=1,
                lease_until=func.clock_timestamp() + timedelta(seconds=QUEUE_LEASE_SECONDS),
            )
            .on_conflict_do_nothing(
                index_elements=[websites.c.team_id, websites.c.url, websites.c.sitemap]
            )
            .returning(websites.c.id)
        )
        if website_id is None:
            row = (
                connection.execute(
                    select(*_SUMMARY_COLUMNS).where(
                        websites.c.team_id == team_id,
                        websites.c.url == url,
                        websites.c.sitemap == data.sitemap,
                    )
                )
                .mappings()
                .one()
            )
            response.status_code = 200
            return _summary(row)
    return _dispatch(request, team_id, website_id, 1)


@router.get("/{website_id}")
def website_detail(team_id: UUID, website_id: UUID, request: Request) -> WebsiteDetail:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        row = _read_website(connection, team_id, website_id, detail=True)
        summary = _summary(row)
        return WebsiteDetail(
            **summary.model_dump(),
            published_attempt=row["published_attempt"],
            pages=row["pages"] or [],
            last_attempt_pages=row["working_pages"] if summary.status == "failed" else [],
        )


@router.post("/{website_id}/recrawl")
def recrawl_website(
    team_id: UUID, website_id: UUID, request: Request, data: RecrawlInput
) -> WebsiteSummary:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        current = _summary(_read_website(connection, team_id, website_id, for_update=True))
        if current.status not in {"completed", "failed"} or current.attempt >= MAX_WEBSITE_ATTEMPTS:
            raise HTTPException(409, "Website cannot be recrawled")
        attempt = current.attempt + 1
        connection.execute(
            update(websites)
            .where(websites.c.team_id == team_id, websites.c.id == website_id)
            .values(
                requested_by=actor_id,
                status="queued",
                attempt=attempt,
                working_pages=[],
                error=None,
                updated_at=func.clock_timestamp(),
                lease_until=func.clock_timestamp() + timedelta(seconds=QUEUE_LEASE_SECONDS),
            )
        )
    return _dispatch(request, team_id, website_id, attempt)
