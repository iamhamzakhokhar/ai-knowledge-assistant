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
