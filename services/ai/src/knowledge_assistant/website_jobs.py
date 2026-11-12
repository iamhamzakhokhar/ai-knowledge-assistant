"""Dedicated website worker and tenant-scoped, fenced crawl transitions."""

from collections.abc import Sequence
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal, TypedDict
from uuid import UUID

from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.serializers import JSONSerializer
from sqlalchemy import Connection, Engine, RowMapping, func, select, update

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.models import websites
from knowledge_assistant.queueing import create_redis
from knowledge_assistant.website_crawler import (
    MAX_CRAWL_PAGES,
    MAX_SITEMAPS,
    MAX_TEXT_BYTES,
    MAX_TITLE_LENGTH,
    CrawlInputError,
    CrawlPage,
    crawl_website,
    normalize_website_url,
)
from knowledge_assistant.worker import main as run_worker
from knowledge_assistant.workspace_store import lock_scope, member_role

WEBSITE_QUEUE_NAME = "website_ingestion"
WEBSITE_POINTER_PATH = Path("/tmp/knowledge-assistant-website-worker.json")
MAX_WEBSITE_ATTEMPTS = 3
MAX_PAGES = MAX_CRAWL_PAGES + MAX_SITEMAPS
QUEUE_LEASE_SECONDS = 300
JOB_TIMEOUT_SECONDS = 45
PROCESSING_LEASE_SECONDS = 75
MANAGER_ROLES = frozenset({"owner", "admin"})
PageStatus = Literal["completed", "skipped", "failed"]
_PAGE_ERRORS = frozenset(
    {
        "invalid_url",
        "blocked_destination",
        "outside_origin",
        "dns_failure",
        "timeout",
        "network_error",
        "invalid_response",
        "http_error",
        "response_too_large",
        "unsupported_content_type",
        "unsupported_encoding",
        "redirect_limit",
        "duplicate",
        "robots_disallowed",
        "robots_unavailable",
        "invalid_sitemap",
        "empty_page",
        "crawl_limit",
    }
)


class WebsitePage(TypedDict):
    url: str
    final_url: str | None
    status: PageStatus
    title: str | None
    text: str | None
    error: str | None


class WebsiteQueueError(RuntimeError):
    """The fixed crawl task could not be dispatched."""


class WebsiteWorkError(RuntimeError):
    """Safe failure text for the worker's operational logs."""


class _StaleCrawl(RuntimeError):
    """The worker may no longer process this attempt."""


@dataclass(frozen=True)
class WebsiteAttempt:
    team_id: UUID
    website_id: UUID
    number: int

    def matches(self, status: str):
        return (
            websites.c.team_id == self.team_id,
            websites.c.id == self.website_id,
            websites.c.attempt == self.number,
            websites.c.status == status,
        )

    def active(self, status: str):
        return (*self.matches(status), websites.c.lease_until > func.clock_timestamp())


def enqueue_website(settings: Settings, team_id: UUID, website_id: UUID, attempt: int) -> None:
    """Deliver only a tenant, import, and attempt identifier on the dedicated queue."""
    try:
        with closing(create_redis(settings)) as client:
            Queue(WEBSITE_QUEUE_NAME, connection=client, serializer=JSONSerializer).enqueue(
                "knowledge_assistant.website_jobs.process_website",
                str(team_id),
                str(website_id),
                attempt,
                job_id=f"aka-website-{team_id.hex}-{website_id.hex}-{attempt}",
                unique=True,
                job_timeout=JOB_TIMEOUT_SECONDS,
                ttl=QUEUE_LEASE_SECONDS,
                result_ttl=300,
                failure_ttl=3600,
                description="Crawl a website import",
            )
    except DuplicateJobError:
        return
    except Exception:
        raise WebsiteQueueError("Website crawl could not be queued") from None


def _fail(connection: Connection, work: WebsiteAttempt, status: str, error: str) -> None:
    connection.execute(
        update(websites)
        .where(*work.matches(status))
        .values(status="failed", lease_until=None, error=error, updated_at=func.clock_timestamp())
    )


def mark_dispatch_failed(engine: Engine, team_id: UUID, website_id: UUID, attempt: int) -> None:
    with tenant_connection(engine, team_id) as connection:
        _fail(
            connection, WebsiteAttempt(team_id, website_id, attempt), "queued", "queue_unavailable"
        )


def _lock_attempt(connection: Connection, work: WebsiteAttempt, status: str) -> RowMapping | None:
    lock_scope(connection, f"aka:team:{work.team_id}")
    row = (
        connection.execute(
            select(websites.c.requested_by, websites.c.url, websites.c.sitemap)
            .where(*work.active(status))
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is not None
        and member_role(connection, work.team_id, row["requested_by"]) not in MANAGER_ROLES
    ):
        _fail(connection, work, status, "authorization_revoked")
        return None
    return row


def _claim(engine: Engine, work: WebsiteAttempt) -> tuple[str, bool] | None:
    with tenant_connection(engine, work.team_id) as connection:
        row = _lock_attempt(connection, work, "queued")
        if row is None:
            return None
        started = connection.execute(
            update(websites)
            .where(*work.active("queued"))
            .values(
                status="processing",
                updated_at=func.clock_timestamp(),
                lease_until=func.clock_timestamp() + timedelta(seconds=PROCESSING_LEASE_SECONDS),
            )
        )
        if started.rowcount == 1:
            return row["url"], row["sitemap"]
    return None


def _record_progress(engine: Engine, work: WebsiteAttempt, page: WebsitePage) -> bool:
    with tenant_connection(engine, work.team_id) as connection:
        if _lock_attempt(connection, work, "processing") is None:
            return False
        metadata: WebsitePage = {**page, "text": None}
        saved = connection.execute(
            update(websites)
            .where(*work.active("processing"))
            .values(
                working_pages=websites.c.working_pages.op("||")([metadata]),
                updated_at=func.clock_timestamp(),
            )
        )
        return saved.rowcount == 1


def _finish(
    engine: Engine, work: WebsiteAttempt, pages: list[WebsitePage], error: str | None
) -> None:
    with tenant_connection(engine, work.team_id) as connection:
        if _lock_attempt(connection, work, "processing") is None:
            return
        if error is not None:
            _fail(connection, work, "processing", error)
            return
        connection.execute(
            update(websites)
            .where(*work.active("processing"))
            .values(
                status="completed",
                lease_until=None,
                published_attempt=work.number,
                pages=pages,
                error=None,
                updated_at=func.clock_timestamp(),
            )
        )


def _page_result(page: CrawlPage) -> WebsitePage:
    # URLs are canonicalized again before storage. Only bounded plain text and
    # finite error codes cross the boundary from the crawler into the workspace.
    url = normalize_website_url(page["url"])
    final_url = None
    if page["final_url"] is not None:
        with suppress(CrawlInputError):
            final_url = normalize_website_url(page["final_url"])
    title = (
        page["title"].replace("\x00", "")[:MAX_TITLE_LENGTH] if page["title"] is not None else None
    )
    status: PageStatus = "skipped" if page["status"] == "skipped" else "failed"
    if page["status"] == "fetched" and page["text"]:
        text = (
            page["text"]
            .replace("\x00", "")
            .encode("utf-8")[:MAX_TEXT_BYTES]
            .decode("utf-8", "ignore")
        )
        if text.strip():
            return WebsitePage(
                url=url, final_url=final_url, status="completed", title=title, text=text, error=None
            )
    reason = page["reason"]
    error = reason if reason in _PAGE_ERRORS else "invalid_response"
    return WebsitePage(
        url=url, final_url=final_url, status=status, title=title, text=None, error=error
    )


def process_website(
    team_id: str, website_id: str, attempt: int, *, settings: Settings | None = None
) -> None:
    """Claim a live attempt, record bounded progress, and publish complete results atomically."""
    if type(attempt) is not int or not 1 <= attempt <= MAX_WEBSITE_ATTEMPTS:
        raise WebsiteWorkError("Invalid website attempt")
    engine = None
    try:
        work = WebsiteAttempt(UUID(team_id), UUID(website_id), attempt)
        config = settings if settings is not None else Settings()
        if config.database_user != "aka_app":
            raise WebsiteWorkError("Website worker requires the runtime role")
        engine = create_engine_from_settings(config)
        request = _claim(engine, work)
        if request is None:
            return
        url, sitemap = request
        pages: list[WebsitePage] = []

        def record_progress(record: CrawlPage) -> None:
            if len(pages) >= MAX_PAGES:
                raise WebsiteWorkError("Website crawl result limit exceeded")
            page = _page_result(record)
            if not _record_progress(engine, work, page):
                raise _StaleCrawl
            pages.append(page)

        error = None
        try:
            records = crawl_website(url, sitemap=sitemap, on_result=record_progress)
            if len(records) != len(pages):
                error = "processing_failed"
            elif not any(page["status"] == "completed" for page in pages):
                error = "no_pages_fetched"
        except _StaleCrawl:
            return
        except Exception:
            error = "processing_failed"
        _finish(engine, work, pages, error)
    except Exception:
        raise WebsiteWorkError("Website crawl could not complete") from None
    finally:
        if engine is not None:
            with suppress(Exception):
                engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    return run_worker(argv, pointer_path=WEBSITE_POINTER_PATH, queue_name=WEBSITE_QUEUE_NAME)


if __name__ == "__main__":
    raise SystemExit(main())
