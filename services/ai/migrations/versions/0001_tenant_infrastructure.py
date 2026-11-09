"""Initial tenant tables, enforced row security, and runtime grants.

Revision ID: 0001
Revises: none
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_sources_team_id", "knowledge_sources", ["team_id"])
    op.create_table(
        "job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), server_default="infrastructure_probe", nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="ck_job_runs_status"
        ),
    )
    op.create_index("ix_job_runs_team_id", "job_runs", ["team_id"])

    # Identifiers here are fixed migration constants, never user-provided text.
    for table, tenant_column in (
        ("teams", "id"),
        ("knowledge_sources", "team_id"),
        ("job_runs", "team_id"),
    ):
        predicate = f"{tenant_column} = NULLIF(current_setting('app.team_id', true), '')::uuid"
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} TO aka_app "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, aka_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} TO aka_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA public FROM aka_app")
    op.execute("GRANT USAGE ON SCHEMA public TO aka_app")
    # PostgreSQL otherwise grants temporary-table creation through PUBLIC.
    connection = op.get_bind()
    database_name = connection.scalar(sa.text("SELECT current_database()"))
    database_identifier = connection.dialect.identifier_preparer.quote(database_name)
    op.execute(f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM PUBLIC, aka_app")
    op.execute(f"GRANT CONNECT ON DATABASE {database_identifier} TO aka_app")


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("knowledge_sources")
    op.drop_table("teams")
    # Keep vector: another future schema may depend on the shared extension.
