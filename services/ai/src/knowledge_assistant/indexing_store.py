"""Tenant-scoped current extraction and active-version queries."""

from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, RowMapping, and_, select

from knowledge_assistant.embeddings import EmbeddingConfig
from knowledge_assistant.models import (
    documents,
    source_index_chunks,
    source_index_states,
    source_index_versions,
    websites,
)

SourceKind = Literal["document", "website"]
MANAGER_ROLES = frozenset({"owner", "admin"})


def source_statement(kind: SourceKind, *, include_entries: bool = False):
    if kind == "document":
        table = documents
        name = table.c.name
        revision = table.c.revision
        entries = table.c.sections
    else:
        table = websites
        name = table.c.url
        revision = table.c.published_attempt
        entries = table.c.pages
    statement = select(
        table.c.team_id,
        table.c.id,
        name.label("name"),
        table.c.status.label("extraction_status"),
        revision.label("input_revision"),
        table.c.updated_at.label("extraction_updated_at"),
    )
    if include_entries:
        statement = statement.add_columns(entries.label("entries"))
    return statement


def source_row(
    connection: Connection,
    team_id: UUID,
    kind: SourceKind,
    source_id: UUID,
    *,
    include_entries: bool = False,
) -> RowMapping | None:
    table = documents if kind == "document" else websites
    return (
        connection.execute(
            source_statement(kind, include_entries=include_entries).where(
                table.c.team_id == team_id, table.c.id == source_id
            )
        )
        .mappings()
        .one_or_none()
    )


def ready_revision(row: RowMapping, kind: SourceKind) -> int | None:
    if kind == "document" and row["extraction_status"] != "completed":
        return None
    # Website imports preserve their last published snapshot during a recrawl.
    return row["input_revision"]


def source_scope(table, team_id: UUID, kind: SourceKind, source_id: UUID):
    return (table.c.team_id == team_id, table.c.source_kind == kind, table.c.source_id == source_id)


def _same_source(left, right):
    return and_(
        left.c.team_id == right.c.team_id,
        left.c.source_kind == right.c.source_kind,
        left.c.source_id == right.c.source_id,
    )


def active_chunk_statement(team_id: UUID, config: EmbeddingConfig | None = None):
    """Select active chunks in the caller's existing trusted team transaction."""
    state, version, chunk = source_index_states, source_index_versions, source_index_chunks
    statement = (
        select(chunk)
        .select_from(
            state.join(
                version,
                and_(_same_source(state, version), state.c.active_version_id == version.c.id),
            ).join(
                chunk,
                and_(
                    _same_source(version, chunk),
                    version.c.id == chunk.c.version_id,
                    version.c.dimension == chunk.c.dimension,
                ),
            )
        )
        .where(state.c.team_id == team_id, state.c.status != "removed")
    )
    if config is not None:
        statement = statement.where(
            version.c.provider == config.provider,
            version.c.model == config.model,
            version.c.dimension == config.dimension,
            version.c.indexing_version == config.index_version,
        )
    return statement
