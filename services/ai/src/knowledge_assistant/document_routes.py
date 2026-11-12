"""Tenant-scoped document upload, extraction status, and retry routes."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import unquote_to_bytes
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, RowMapping, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from knowledge_assistant.document_jobs import (
    MANAGER_ROLES,
    MAX_DOCUMENT_ATTEMPTS,
    QUEUE_LEASE_SECONDS,
    DocumentFormat,
    DocumentQueueError,
    enqueue_document,
    mark_dispatch_failed,
)
from knowledge_assistant.document_parser import DocumentParseError, validate_signature
from knowledge_assistant.models import document_originals, documents
from knowledge_assistant.secret_files import SecretFileError
from knowledge_assistant.workspace_store import authenticated_transaction, require_team

router = APIRouter(prefix="/api/v1/teams/{team_id}/documents", tags=["documents"])

_FORMATS: dict[str, DocumentFormat] = {
    "pdf": "pdf",
    "docx": "docx",
    "md": "markdown",
    "markdown": "markdown",
}
_SUMMARY_COLUMNS = (
    documents.c.id,
    documents.c.name,
    documents.c.format,
    documents.c.status,
    documents.c.attempt,
    documents.c.error,
    documents.c.updated_at,
    documents.c.lease_until,
    func.clock_timestamp().label("db_time"),
)


class DocumentSummary(BaseModel):
    id: UUID
    name: str
    format: DocumentFormat
    status: Literal["queued", "processing", "completed", "failed"]
    attempt: int
    error: str | None
    updated_at: datetime


class Section(BaseModel):
    text: str
    page: int | None
    heading: str | None


class DocumentDetail(DocumentSummary):
    sections: list[Section]


class DocumentList(BaseModel):
    documents: list[DocumentSummary]


class RetryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _summary(row: RowMapping) -> DocumentSummary:
    values = {key: row[key] for key in DocumentSummary.model_fields}
    if row["lease_until"] is not None and row["lease_until"] <= row["db_time"]:
        values.update(status="failed", error="attempt_expired")
    return DocumentSummary.model_validate(values)


def _read_document(
    connection: Connection, team_id: UUID, document_id: UUID, *, for_retry: bool = False
) -> RowMapping:
    columns = _SUMMARY_COLUMNS if for_retry else (*_SUMMARY_COLUMNS, documents.c.sections)
    statement = select(*columns).where(
        documents.c.team_id == team_id, documents.c.id == document_id
    )
    if for_retry:
        statement = statement.with_for_update()
    row = connection.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Document not found")
    return row


def _require_manager(connection: Connection, team_id: UUID, actor_id: UUID) -> None:
    if require_team(connection, team_id, actor_id, write=True) not in MANAGER_ROLES:
        raise HTTPException(403, "Owner or admin role required")


def _file_type(encoded_name: str, data: bytes) -> tuple[str, DocumentFormat]:
    if len(encoded_name) > 765 or re.search(r"%(?![0-9a-fA-F]{2})", encoded_name):
        raise HTTPException(422, "Invalid document name")
    try:
        name = unquote_to_bytes(encoded_name).decode("utf-8")
    except UnicodeError:
        raise HTTPException(422, "Invalid document name") from None
    if (
        not name.strip()
        or name in {".", ".."}
        or len(name.encode("utf-8")) > 255
        or any(char in "/\\" or not char.isprintable() for char in name)
    ):
        raise HTTPException(422, "Invalid document name")
    stem, separator, suffix = name.rpartition(".")
    kind = _FORMATS.get(suffix.lower()) if stem.strip() and separator else None
    if kind is None:
        raise HTTPException(415, "Unsupported document type")
    try:
        validate_signature(data, kind)
    except DocumentParseError as error:
        if error.code == "document_too_large":
            raise HTTPException(413, "Document is too large") from None
        if error.code in {"unsupported_type", "invalid_document"}:
            raise HTTPException(415, "Document does not match its type") from None
        raise HTTPException(422, "Invalid document") from None
    return name, kind


def _dispatch(request: Request, team_id: UUID, document_id: UUID, attempt: int) -> DocumentSummary:
    # The upload or retry is committed before Redis can hand work to a worker.
    try:
        enqueue_document(request.app.state.workspace_settings, team_id, document_id, attempt)
    except DocumentQueueError:
        try:
            engine = request.app.state.workspace_store.engine()
            mark_dispatch_failed(engine, team_id, document_id, attempt)
        except SQLAlchemyError, SecretFileError, OSError:
            raise HTTPException(503, "Workspace service is unavailable") from None
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        return _summary(_read_document(connection, team_id, document_id))


@router.get("")
def list_documents(team_id: UUID, request: Request) -> DocumentList:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        rows = connection.execute(
            select(*_SUMMARY_COLUMNS)
            .where(documents.c.team_id == team_id)
            .order_by(documents.c.updated_at.desc(), documents.c.id)
        ).mappings()
        return DocumentList(documents=[_summary(row) for row in rows])


@router.post("", status_code=201, responses={200: {"model": DocumentSummary}})
def upload_document(
    team_id: UUID,
    request: Request,
    response: Response,
    data: Annotated[bytes, Body(media_type="application/octet-stream")],
    encoded_name: Annotated[str, Header(alias="X-Document-Name")],
) -> DocumentSummary:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        name, kind = _file_type(encoded_name, data)
        digest = hashlib.sha256(data).digest()
        document_id = connection.scalar(
            pg_insert(documents)
            .values(
                team_id=team_id,
                id=uuid4(),
                name=name,
                format=kind,
                sha256=digest,
                requested_by=actor_id,
                status="queued",
                attempt=1,
                lease_until=func.clock_timestamp() + timedelta(seconds=QUEUE_LEASE_SECONDS),
            )
            .on_conflict_do_nothing(index_elements=[documents.c.team_id, documents.c.sha256])
            .returning(documents.c.id)
        )
        if document_id is None:
            row = (
                connection.execute(
                    select(*_SUMMARY_COLUMNS).where(
                        documents.c.team_id == team_id, documents.c.sha256 == digest
                    )
                )
                .mappings()
                .one()
            )
            response.status_code = 200
            return _summary(row)
        connection.execute(
            insert(document_originals).values(team_id=team_id, id=document_id, data=data)
        )
    return _dispatch(request, team_id, document_id, 1)


@router.get("/{document_id}")
def document_detail(team_id: UUID, document_id: UUID, request: Request) -> DocumentDetail:
    with authenticated_transaction(request) as (connection, actor_id, _):
        require_team(connection, team_id, actor_id)
        row = _read_document(connection, team_id, document_id)
        return DocumentDetail.model_validate(
            {**_summary(row).model_dump(), "sections": row["sections"] or []}
        )


@router.post("/{document_id}/retry")
def retry_document(
    team_id: UUID, document_id: UUID, request: Request, data: RetryInput
) -> DocumentSummary:
    with authenticated_transaction(request) as (connection, actor_id, _):
        _require_manager(connection, team_id, actor_id)
        current = _summary(_read_document(connection, team_id, document_id, for_retry=True))
        if current.status != "failed" or current.attempt >= MAX_DOCUMENT_ATTEMPTS:
            raise HTTPException(409, "Document cannot be retried")
        attempt = current.attempt + 1
        connection.execute(
            update(documents)
            .where(documents.c.team_id == team_id, documents.c.id == document_id)
            .values(
                requested_by=actor_id,
                status="queued",
                attempt=attempt,
                sections=None,
                error=None,
                updated_at=func.clock_timestamp(),
                lease_until=func.clock_timestamp() + timedelta(seconds=QUEUE_LEASE_SECONDS),
            )
        )
    return _dispatch(request, team_id, document_id, attempt)
