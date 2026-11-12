"""Add account, session, membership and invite state without changing existing rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
TEAM = "NULLIF(current_setting('app.team_id', true), '')::uuid"
ROLE = "current_setting('app.team_role', true)"
AUTH_EMAIL = "current_setting('app.auth_email', true)"
SESSION = "decode(NULLIF(current_setting('app.session_hash', true), ''), 'hex')"
INVITE = "decode(NULLIF(current_setting('app.invite_hash', true), ''), 'hex')"


def _create_tables() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "auth_credentials",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "team_memberships",
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("auth_users.id"), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_team_memberships_role"),
    )
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"])
    op.create_index(
        "uq_team_memberships_one_owner",
        "team_memberships",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_table(
        "team_invites",
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


def _account_policies() -> None:
    # All interpolated fragments are fixed migration text. Context values are
    # set by bound statements in the service, with transaction-local lifetime.
    op.execute(
        f"CREATE POLICY user_lookup ON auth_users FOR SELECT TO aka_app USING ("
        f"id = {ACTOR} OR email = {AUTH_EMAIL} OR EXISTS ("
        f"SELECT 1 FROM team_memberships m WHERE m.user_id = auth_users.id AND m.team_id = {TEAM}))"
    )
    op.execute(
        f"CREATE POLICY user_signup ON auth_users FOR INSERT TO aka_app "
        f"WITH CHECK (id = {ACTOR} AND email = {AUTH_EMAIL})"
    )
    op.execute(
        f"CREATE POLICY credential_read ON auth_credentials FOR SELECT TO aka_app "
        f"USING (user_id = {ACTOR})"
    )
    op.execute(
        f"CREATE POLICY credential_write ON auth_credentials FOR INSERT TO aka_app "
        f"WITH CHECK (user_id = {ACTOR})"
    )
    op.execute(
        f"CREATE POLICY session_read ON auth_sessions FOR SELECT TO aka_app "
        f"USING (token_hash = {SESSION} OR user_id = {ACTOR})"
    )
    op.execute(
        f"CREATE POLICY session_issue ON auth_sessions FOR INSERT TO aka_app "
        f"WITH CHECK (user_id = {ACTOR})"
    )
    op.execute(
        f"CREATE POLICY session_remove ON auth_sessions FOR DELETE TO aka_app USING ("
        f"token_hash = {SESSION} OR (user_id = {ACTOR} AND expires_at <= clock_timestamp()))"
    )


def _membership_policies() -> None:
    recipient = f"token_hash = {INVITE} AND email = {AUTH_EMAIL}"
    op.execute(
        f"CREATE POLICY invite_read ON team_invites FOR SELECT TO aka_app USING ("
        f"({recipient}) OR (team_id = {TEAM} AND {ROLE} IN ('owner', 'admin')))"
    )
    op.execute(
        f"CREATE POLICY invite_issue ON team_invites FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND created_by = {ACTOR} AND used_at IS NULL AND "
        f"({ROLE} = 'owner' OR ({ROLE} = 'admin' AND role = 'member')))"
    )
    op.execute(
        f"CREATE POLICY invite_consume ON team_invites FOR UPDATE TO aka_app "
        f"USING ({recipient} AND team_id = {TEAM} AND used_at IS NULL "
        f"AND expires_at > clock_timestamp()) WITH CHECK ({recipient} AND team_id = {TEAM} "
        "AND used_at IS NOT NULL)"
    )
    can_revoke = (
        f"team_id = {TEAM} AND ({ROLE} = 'owner' OR ({ROLE} = 'admin' AND EXISTS ("
        "SELECT 1 FROM team_memberships m JOIN auth_users u ON u.id = m.user_id "
        "WHERE m.team_id = team_invites.team_id AND m.role = 'member' "
        "AND u.email = team_invites.email)))"
    )
    op.execute(
        f"CREATE POLICY invite_revoke ON team_invites FOR UPDATE TO aka_app "
        f"USING ({can_revoke} AND used_at IS NULL) "
        f"WITH CHECK ({can_revoke} AND used_at IS NOT NULL)"
    )
    op.execute(
        f"CREATE POLICY member_read ON team_memberships FOR SELECT TO aka_app "
        f"USING (user_id = {ACTOR} OR team_id = {TEAM})"
    )
    op.execute(
        f"CREATE POLICY member_join ON team_memberships FOR INSERT TO aka_app WITH CHECK ("
        f"team_id = {TEAM} AND user_id = {ACTOR} AND ("
        "(role = 'owner' AND current_setting('app.team_bootstrap', true) = '1') OR "
        "EXISTS (SELECT 1 FROM team_invites i WHERE i.team_id = team_memberships.team_id "
        f"AND i.token_hash = {INVITE} AND i.email = {AUTH_EMAIL} "
        "AND i.role = team_memberships.role AND i.used_at IS NULL "
        "AND i.expires_at > clock_timestamp())))"
    )
    manageable = (
        f"team_id = {TEAM} AND (({ROLE} = 'owner' AND role IN ('admin', 'member')) "
        f"OR ({ROLE} = 'admin' AND role = 'member'))"
    )
    op.execute(
        f"CREATE POLICY member_role ON team_memberships FOR UPDATE TO aka_app "
        f"USING ({manageable}) WITH CHECK ({manageable})"
    )
    op.execute(
        "CREATE POLICY member_remove ON team_memberships FOR DELETE TO aka_app "
        f"USING ({manageable})"
    )


def upgrade() -> None:
    _create_tables()
    for table, grant in (
        ("auth_users", "SELECT, INSERT"),
        ("auth_credentials", "SELECT, INSERT"),
        ("auth_sessions", "SELECT, INSERT, DELETE"),
        ("team_memberships", "SELECT, INSERT, UPDATE (role), DELETE"),
        ("team_invites", "SELECT, INSERT, UPDATE (used_at)"),
    ):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, aka_app")
        op.execute(f"GRANT {grant} ON TABLE public.{table} TO aka_app")
    _account_policies()
    _membership_policies()

    member = (
        "EXISTS (SELECT 1 FROM team_memberships m WHERE m.team_id = teams.id "
        f"AND m.user_id = {ACTOR})"
    )
    op.execute(f"CREATE POLICY teams_for_actor ON teams FOR SELECT TO aka_app USING ({member})")
    for table in ("knowledge_sources", "job_runs"):
        op.execute(
            f"CREATE POLICY live_team_read ON {table} AS RESTRICTIVE FOR SELECT TO aka_app USING ("
            f"{ACTOR} IS NULL OR EXISTS (SELECT 1 FROM team_memberships m "
            f"WHERE m.team_id = {table}.team_id AND m.user_id = {ACTOR}))"
        )
    # Internal seed/probe transactions retain their service-only tenant context.
    for table in ("knowledge_sources", "job_runs"):
        op.execute(
            f"CREATE POLICY internal_insert ON {table} AS RESTRICTIVE FOR INSERT TO aka_app "
            f"WITH CHECK ({ACTOR} IS NULL)"
        )
        op.execute(
            f"CREATE POLICY internal_update ON {table} AS RESTRICTIVE FOR UPDATE TO aka_app "
            f"USING ({ACTOR} IS NULL) WITH CHECK ({ACTOR} IS NULL)"
        )
        op.execute(
            f"CREATE POLICY internal_delete ON {table} AS RESTRICTIVE FOR DELETE TO aka_app "
            f"USING ({ACTOR} IS NULL)"
        )


def downgrade() -> None:
    for table in ("knowledge_sources", "job_runs"):
        for policy in ("internal_insert", "internal_update", "internal_delete", "live_team_read"):
            op.execute(f"DROP POLICY {policy} ON {table}")
    op.execute("DROP POLICY teams_for_actor ON teams")
    op.execute("DROP POLICY member_join ON team_memberships")
    op.execute("DROP POLICY user_lookup ON auth_users")
    for table in (
        "team_invites",
        "team_memberships",
        "auth_sessions",
        "auth_credentials",
        "auth_users",
    ):
        op.drop_table(table)
