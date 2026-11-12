"""The initial infrastructure schema; Alembic alone creates or changes tables."""

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
