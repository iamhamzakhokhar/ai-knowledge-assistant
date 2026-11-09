"""Use the owner connection passed in memory by knowledge_assistant.migrate."""

from alembic import context
from sqlalchemy import text

from knowledge_assistant.models import metadata

config = context.config

if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported by this infrastructure bootstrap")

connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations with python -m knowledge_assistant.migrate")

context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
    # The runtime role must never mutate or inspect migration bookkeeping.
    if connection.scalar(text("SELECT to_regclass('public.alembic_version')")) is not None:
        connection.execute(text("REVOKE ALL ON TABLE public.alembic_version FROM PUBLIC, aka_app"))
