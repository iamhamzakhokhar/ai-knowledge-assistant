"""Add tenant-scoped extraction state and private original document bytes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
TEAM = "NULLIF(current_setting('app.team_id', true), '')::uuid"


def _create_tables() -> None:
    op.create_table(
        "documents",
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column(
            "requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False
        ),
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
    op.create_table(
        "document_originals",
        sa.Column("team_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["team_id", "id"], ["documents.team_id", "documents.id"]),
        sa.CheckConstraint(
            "octet_length(data) BETWEEN 1 AND 5242880", name="ck_document_originals_byte_limit"
        ),
    )


def _member(table: str, *, manager: bool = False) -> str:
    role = " AND m.role IN ('owner', 'admin')" if manager else ""
    return (
        f"EXISTS (SELECT 1 FROM team_memberships m WHERE m.team_id = {table}.team_id "
        f"AND m.user_id = {ACTOR}{role})"
    )


def upgrade() -> None:
    _create_tables()
    for table in ("documents", "document_originals"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, aka_app")
        op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO aka_app")
    op.execute(
        "GRANT UPDATE (requested_by, status, attempt, lease_until, sections, error, updated_at) "
        "ON TABLE public.documents TO aka_app"
    )

    manager = _member("documents", manager=True)
    pending = f"requested_by = {ACTOR} AND status = 'queued'"
    op.execute(
        "CREATE POLICY document_read ON documents FOR SELECT TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {_member('documents')}))"
    )
    op.execute(
        "CREATE POLICY document_upload ON documents FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND {manager} AND {pending} AND attempt = 1)"
    )
    op.execute(
        "CREATE POLICY document_transition ON documents FOR UPDATE TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {manager})) WITH CHECK ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR ({manager} AND {pending})))"
    )
    op.execute(
        "CREATE POLICY original_read ON document_originals FOR SELECT TO aka_app USING ("
        f"team_id = {TEAM} AND {ACTOR} IS NULL)"
    )
    op.execute(
        "CREATE POLICY original_upload ON document_originals FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND {_member('document_originals', manager=True)} AND EXISTS ("
        "SELECT 1 FROM documents d WHERE d.team_id = document_originals.team_id "
        f"AND d.id = document_originals.id AND d.requested_by = {ACTOR} "
        "AND d.status = 'queued' AND d.attempt = 1))"
    )


def downgrade() -> None:
    op.drop_table("document_originals")
    op.drop_table("documents")
