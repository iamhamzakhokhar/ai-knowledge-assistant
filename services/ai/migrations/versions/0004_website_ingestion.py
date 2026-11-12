"""Add tenant-scoped website imports with fenced snapshots and progress."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
TEAM = "NULLIF(current_setting('app.team_id', true), '')::uuid"
MEMBERSHIP = f"m.team_id = websites.team_id AND m.user_id = {ACTOR}"
MEMBER = f"EXISTS (SELECT 1 FROM team_memberships m WHERE {MEMBERSHIP})"
MANAGER = (
    f"EXISTS (SELECT 1 FROM team_memberships m WHERE {MEMBERSHIP} AND m.role IN ('owner', 'admin'))"
)
QUEUED_BY_ACTOR = f"requested_by = {ACTOR} AND status = 'queued' AND working_pages = '[]'::jsonb"


def upgrade() -> None:
    op.create_table(
        "websites",
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sitemap", sa.Boolean(), nullable=False),
        sa.Column(
            "requested_by", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False
        ),
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
            "(status IN ('queued', 'processing')) = (lease_until IS NOT NULL)",
            name="ck_websites_lease",
        ),
        sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="ck_websites_error"),
        sa.CheckConstraint(
            "(published_attempt IS NULL) = (pages IS NULL) AND "
            "(published_attempt IS NULL OR published_attempt BETWEEN 1 AND attempt) AND "
            "(status <> 'completed' OR published_attempt IS NOT DISTINCT FROM attempt)",
            name="ck_websites_publication",
        ),
        sa.CheckConstraint("jsonb_array_length(pages) BETWEEN 1 AND 24", name="ck_websites_pages"),
        sa.CheckConstraint(
            "jsonb_array_length(working_pages) <= 24", name="ck_websites_working_pages"
        ),
    )
    op.execute("ALTER TABLE public.websites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.websites FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE public.websites FROM PUBLIC, aka_app")
    op.execute("GRANT SELECT, INSERT ON TABLE public.websites TO aka_app")
    op.execute(
        "GRANT UPDATE (requested_by, status, attempt, lease_until, published_attempt, "
        "pages, working_pages, error, updated_at) ON TABLE public.websites TO aka_app"
    )
    op.execute(
        "CREATE POLICY website_read ON websites FOR SELECT TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {MEMBER}))"
    )
    op.execute(
        "CREATE POLICY website_create ON websites FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND {MANAGER} AND {QUEUED_BY_ACTOR} "
        "AND attempt = 1 AND published_attempt IS NULL)"
    )
    op.execute(
        "CREATE POLICY website_transition ON websites FOR UPDATE TO aka_app USING ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR {MANAGER})) WITH CHECK ("
        f"team_id = {TEAM} AND ({ACTOR} IS NULL OR ({MANAGER} AND {QUEUED_BY_ACTOR})))"
    )


def downgrade() -> None:
    op.drop_table("websites")
