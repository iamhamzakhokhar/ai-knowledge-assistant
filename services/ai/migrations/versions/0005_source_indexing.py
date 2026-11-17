"""Add versioned, tenant-scoped chunks and vectors for derived source indexes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
TEAM = "NULLIF(current_setting('app.team_id', true), '')::uuid"
STATES = "source_index_states"
VERSIONS = "source_index_versions"
CHUNKS = "source_index_chunks"


class Vector(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kwargs) -> str:
        return "vector"


_INDEX_SCOPE = ("team_id", "source_kind", "source_id")


def _index_scope_columns():
    return (
        sa.Column("team_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_kind", sa.Text(), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True), primary_key=True),
    )


def _create_tables() -> None:
    op.create_table(
        "source_index_states",
        *_index_scope_columns(),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.Computed("CASE WHEN source_kind = 'document' THEN source_id END", persisted=True),
        ),
        sa.Column(
            "website_id",
            UUID(as_uuid=True),
            sa.Computed("CASE WHEN source_kind = 'website' THEN source_id END", persisted=True),
        ),
        sa.Column("active_version_id", UUID(as_uuid=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("input_revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False
        ),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ("team_id", "document_id"), ("documents.team_id", "documents.id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ("team_id", "website_id"), ("websites.team_id", "websites.id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("source_kind IN ('document', 'website')", name="ck_index_state_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'removed')",
            name="ck_index_state_status",
        ),
        sa.CheckConstraint("attempt >= 1 AND input_revision >= 1", name="ck_index_state_revision"),
        sa.CheckConstraint(
            "0 <= processed AND processed <= total AND total <= 2048",
            name="ck_index_state_progress",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'processing')) = (lease_until IS NOT NULL)",
            name="ck_index_state_lease",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)", name="ck_index_state_error"
        ),
        sa.CheckConstraint(
            "(status <> 'completed' OR active_version_id IS NOT NULL) AND "
            "(status <> 'removed' OR active_version_id IS NULL)",
            name="ck_index_state_publication",
        ),
    )

    op.create_table(
        "source_index_versions",
        *_index_scope_columns(),
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("input_hash", sa.LargeBinary(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("indexing_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            _INDEX_SCOPE,
            (
                "source_index_states.team_id",
                "source_index_states.source_kind",
                "source_index_states.source_id",
            ),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(*_INDEX_SCOPE, "id", "dimension", name="uq_index_version_dimension"),
        sa.UniqueConstraint(
            *_INDEX_SCOPE,
            "input_hash",
            "provider",
            "model",
            "dimension",
            "indexing_version",
            name="uq_index_version_fingerprint",
        ),
        sa.CheckConstraint("octet_length(input_hash) = 32", name="ck_index_version_hash"),
        sa.CheckConstraint("dimension BETWEEN 1 AND 1536", name="ck_index_version_dimension"),
        sa.CheckConstraint("indexing_version >= 1", name="ck_index_version_algorithm"),
        sa.CheckConstraint(
            "octet_length(provider) BETWEEN 1 AND 100 AND octet_length(model) BETWEEN 1 AND 200",
            name="ck_index_version_model",
        ),
    )

    op.create_table(
        "source_index_chunks",
        *_index_scope_columns(),
        sa.Column("version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("heading", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.ForeignKeyConstraint(
            (*_INDEX_SCOPE, "version_id", "dimension"),
            (
                "source_index_versions.team_id",
                "source_index_versions.source_kind",
                "source_index_versions.source_id",
                "source_index_versions.id",
                "source_index_versions.dimension",
            ),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(*_INDEX_SCOPE, "version_id", "anchor", name="uq_index_chunk_anchor"),
        sa.CheckConstraint("ordinal BETWEEN 0 AND 2047", name="ck_index_chunk_ordinal"),
        sa.CheckConstraint(
            "octet_length(text) BETWEEN 1 AND 8192 AND octet_length(anchor) BETWEEN 1 AND 128",
            name="ck_index_chunk_text",
        ),
        sa.CheckConstraint(
            "(source_kind = 'document' AND url IS NULL AND title IS NULL) OR "
            "(source_kind = 'website' AND url IS NOT NULL AND page IS NULL AND heading IS NULL)",
            name="ck_index_chunk_provenance",
        ),
        sa.CheckConstraint("page BETWEEN 1 AND 100", name="ck_index_chunk_page"),
        sa.CheckConstraint(
            "octet_length(url) BETWEEN 1 AND 2048 AND url ~ '^https?://'", name="ck_index_chunk_url"
        ),
        sa.CheckConstraint(
            "char_length(heading) BETWEEN 1 AND 200 AND char_length(title) BETWEEN 1 AND 200",
            name="ck_index_chunk_labels",
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimension AND vector_norm(embedding) > 0",
            name="ck_index_chunk_embedding",
        ),
    )


def _membership(table: str, *, manager: bool = False) -> str:
    role = " AND m.role IN ('owner', 'admin')" if manager else ""
    return (
        f"EXISTS (SELECT 1 FROM team_memberships m WHERE m.team_id = {table}.team_id "
        f"AND m.user_id = {ACTOR}{role})"
    )


def _has_state(table: str, condition: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM {STATES} s WHERE s.team_id = {table}.team_id "
        f"AND s.source_kind = {table}.source_kind AND s.source_id = {table}.source_id "
        f"AND {condition})"
    )


def _protect_tables() -> None:
    for table in (STATES, VERSIONS, CHUNKS):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, aka_app")
        op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO aka_app")
    op.execute(
        f"GRANT UPDATE (active_version_id, status, attempt, lease_until, input_revision, "
        f"processed, total, requested_by, error, updated_at) ON TABLE public.{STATES} TO aka_app"
    )
    op.execute(f"GRANT DELETE ON TABLE public.{VERSIONS} TO aka_app")

    manager = _membership(STATES, manager=True)
    authored = f"requested_by = {ACTOR} AND {manager}"
    no_progress = "processed = 0 AND total = 0"
    queued = f"status = 'queued' AND {no_progress}"
    op.execute(
        f"CREATE POLICY index_state_read ON {STATES} FOR SELECT TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {_membership(STATES)}))"
    )
    op.execute(
        f"CREATE POLICY index_state_create ON {STATES} FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR ({authored} "
        f"AND status IN ('queued', 'removed') AND {no_progress} "
        "AND attempt = 1 AND active_version_id IS NULL)))"
    )
    op.execute(
        f"CREATE POLICY index_state_transition ON {STATES} FOR UPDATE TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {manager})) WITH CHECK ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR ({authored} AND "
        f"(({queued}) OR status = 'removed'))))"
    )

    for table, version_column in ((VERSIONS, "id"), (CHUNKS, "version_id")):
        active = _has_state(table, f"s.active_version_id = {table}.{version_column}")
        processing = _has_state(
            table,
            f"s.status = 'processing' AND s.active_version_id IS DISTINCT FROM "
            f"{table}.{version_column}",
        )
        op.execute(
            f"CREATE POLICY index_snapshot_read ON {table} FOR SELECT TO aka_app USING ("
            f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {active}))"
        )
        op.execute(
            f"CREATE POLICY index_snapshot_insert ON {table} FOR INSERT TO aka_app WITH CHECK ("
            f"team_id = {TEAM} AND {ACTOR} IS NULL AND {processing})"
        )
    removed = _has_state(VERSIONS, "s.status = 'removed'")
    op.execute(
        f"CREATE POLICY index_version_purge ON {VERSIONS} FOR DELETE TO aka_app USING ("
        f"team_id = {TEAM} AND {ACTOR} IS NULL AND {removed})"
    )


def upgrade() -> None:
    _create_tables()
    op.create_foreign_key(
        "fk_source_index_active_version",
        STATES,
        VERSIONS,
        [*_INDEX_SCOPE, "active_version_id"],
        [*_INDEX_SCOPE, "id"],
    )
    _protect_tables()


def downgrade() -> None:
    op.drop_constraint("fk_source_index_active_version", STATES, type_="foreignkey")
    op.drop_table(CHUNKS)
    op.drop_table(VERSIONS)
    op.drop_table(STATES)
