"""Database table mappings. Alembic alone creates or changes tables."""

import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()

teams = sa.Table(
    "teams",
    metadata,
    sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

knowledge_sources = sa.Table(
    "knowledge_sources",
    metadata,
    sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
sa.Index("ix_knowledge_sources_team_id", knowledge_sources.c.team_id)

job_runs = sa.Table(
    "job_runs",
    metadata,
    sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False, server_default="infrastructure_probe"),
    sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
    sa.Column("result", JSONB(), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed')", name="ck_job_runs_status"
    ),
)
sa.Index("ix_job_runs_team_id", job_runs.c.team_id)

auth_users = sa.Table(
    "auth_users",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

auth_credentials = sa.Table(
    "auth_credentials",
    metadata,
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), primary_key=True),
    sa.Column("password_hash", sa.Text(), nullable=False),
)

auth_sessions = sa.Table(
    "auth_sessions",
    metadata,
    sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
sa.Index("ix_auth_sessions_user_id", auth_sessions.c.user_id)

team_memberships = sa.Table(
    "team_memberships",
    metadata,
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), primary_key=True),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_team_memberships_role"),
)
sa.Index("ix_team_memberships_user_id", team_memberships.c.user_id)
sa.Index(
    "uq_team_memberships_one_owner",
    team_memberships.c.team_id,
    unique=True,
    postgresql_where=team_memberships.c.role == "owner",
)

team_invites = sa.Table(
    "team_invites",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
    sa.Column("email", sa.String(254), nullable=False),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
    sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('admin', 'member')", name="ck_team_invites_role"),
)

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("format", sa.Text(), nullable=False),
    sa.Column("sha256", sa.LargeBinary(), nullable=False),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("sections", JSONB(none_as_null=True), nullable=True),
    sa.Column("error", sa.Text(), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("team_id", "sha256", name="uq_documents_team_sha256"),
    sa.CheckConstraint("format IN ('pdf', 'docx', 'markdown')", name="ck_documents_format"),
    sa.CheckConstraint(
        "status IN ('queued', 'processing', 'completed', 'failed')", name="ck_documents_status"
    ),
    sa.CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_documents_attempt"),
    sa.CheckConstraint("revision >= 1", name="ck_documents_revision"),
    sa.CheckConstraint("octet_length(sha256) = 32", name="ck_documents_sha256"),
    sa.CheckConstraint(
        "(status IN ('queued', 'processing')) = (lease_until IS NOT NULL)",
        name="ck_documents_lease",
    ),
    sa.CheckConstraint(
        "(status = 'completed') = (sections IS NOT NULL)", name="ck_documents_sections"
    ),
    sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="ck_documents_error"),
)

document_originals = sa.Table(
    "document_originals",
    metadata,
    sa.Column("team_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("data", sa.LargeBinary(), nullable=False),
    sa.ForeignKeyConstraint(["team_id", "id"], ["documents.team_id", "documents.id"]),
    sa.CheckConstraint(
        "octet_length(data) BETWEEN 1 AND 5242880", name="ck_document_originals_byte_limit"
    ),
)

websites = sa.Table(
    "websites",
    metadata,
    sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("url", sa.Text(), nullable=False),
    sa.Column("sitemap", sa.Boolean(), nullable=False),
    sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("lease_until", sa.DateTime(timezone=True)),
    sa.Column("published_attempt", sa.Integer()),
    sa.Column("pages", JSONB(none_as_null=True)),
    sa.Column("working_pages", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("error", sa.Text()),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("team_id", "url", "sitemap", name="uq_websites_team_url_sitemap"),
    sa.CheckConstraint("octet_length(url) BETWEEN 1 AND 2048", name="ck_websites_url_size"),
    sa.CheckConstraint(
        "status IN ('queued', 'processing', 'completed', 'failed')", name="ck_websites_status"
    ),
    sa.CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_websites_attempt"),
    sa.CheckConstraint(
        "(status IN ('queued', 'processing')) = (lease_until IS NOT NULL)", name="ck_websites_lease"
    ),
    sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="ck_websites_error"),
    sa.CheckConstraint(
        "(published_attempt IS NULL) = (pages IS NULL) AND "
        "(published_attempt IS NULL OR published_attempt BETWEEN 1 AND attempt) AND "
        "(status <> 'completed' OR published_attempt IS NOT DISTINCT FROM attempt)",
        name="ck_websites_publication",
    ),
    sa.CheckConstraint("jsonb_array_length(pages) BETWEEN 1 AND 24", name="ck_websites_pages"),
    sa.CheckConstraint("jsonb_array_length(working_pages) <= 24", name="ck_websites_working_pages"),
)


class Vector(sa.types.UserDefinedType):
    """An untyped pgvector column, with dimensions enforced by the index schema."""

    cache_ok = True

    def get_col_spec(self, **kwargs) -> str:
        return "vector"

    def bind_expression(self, bindvalue):
        return sa.cast(bindvalue, self)

    def bind_processor(self, dialect):
        def process(value):
            return json.dumps(value, allow_nan=False, separators=(",", ":"))

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            return json.loads(value) if value is not None else None

        return process


_INDEX_SCOPE = ("team_id", "source_kind", "source_id")


def _index_scope_columns():
    return (
        sa.Column("team_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_kind", sa.Text(), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True), primary_key=True),
    )


source_index_states = sa.Table(
    "source_index_states",
    metadata,
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
    sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
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
    sa.ForeignKeyConstraint(
        (*_INDEX_SCOPE, "active_version_id"),
        (
            "source_index_versions.team_id",
            "source_index_versions.source_kind",
            "source_index_versions.source_id",
            "source_index_versions.id",
        ),
        name="fk_source_index_active_version",
        use_alter=True,
    ),
    sa.CheckConstraint("source_kind IN ('document', 'website')", name="ck_index_state_kind"),
    sa.CheckConstraint(
        "status IN ('queued', 'processing', 'completed', 'failed', 'removed')",
        name="ck_index_state_status",
    ),
    sa.CheckConstraint("attempt >= 1 AND input_revision >= 1", name="ck_index_state_revision"),
    sa.CheckConstraint(
        "0 <= processed AND processed <= total AND total <= 2048", name="ck_index_state_progress"
    ),
    sa.CheckConstraint(
        "(status IN ('queued', 'processing')) = (lease_until IS NOT NULL)",
        name="ck_index_state_lease",
    ),
    sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="ck_index_state_error"),
    sa.CheckConstraint(
        "(status <> 'completed' OR active_version_id IS NOT NULL) AND "
        "(status <> 'removed' OR active_version_id IS NULL)",
        name="ck_index_state_publication",
    ),
)

source_index_versions = sa.Table(
    "source_index_versions",
    metadata,
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

source_index_chunks = sa.Table(
    "source_index_chunks",
    metadata,
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
