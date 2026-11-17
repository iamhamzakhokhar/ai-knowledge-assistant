"""Fence replacement extraction with a document revision."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
TEAM = "NULLIF(current_setting('app.team_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1")
    )
    op.create_check_constraint("ck_documents_revision", "documents", "revision >= 1")
    op.execute("GRANT UPDATE (name, format, sha256, revision) ON public.documents TO aka_app")
    op.execute("GRANT UPDATE (data) ON public.document_originals TO aka_app")
    op.execute(
        "CREATE POLICY original_replace ON public.document_originals FOR UPDATE TO aka_app "
        f"USING (team_id = {TEAM} AND {ACTOR} IS NULL) "
        f"WITH CHECK (team_id = {TEAM} AND {ACTOR} IS NULL AND EXISTS ("
        "SELECT 1 FROM documents d WHERE d.team_id = document_originals.team_id "
        "AND d.id = document_originals.id AND d.revision > 1 "
        "AND d.status = 'queued' AND d.attempt = 1))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY original_replace ON public.document_originals")
    op.execute("REVOKE UPDATE (data) ON public.document_originals FROM aka_app")
    op.execute("REVOKE UPDATE (name, format, sha256, revision) ON public.documents FROM aka_app")
    op.drop_constraint("ck_documents_revision", "documents", type_="check")
    op.drop_column("documents", "revision")
