"""Dependency readiness without exposing configuration or connection errors."""

from contextlib import suppress

from redis import Redis
from sqlalchemy import Engine, text

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings
from knowledge_assistant.queueing import create_redis


def check_dependencies(settings: Settings) -> bool:
    """Check the configured application database connection and authenticated Redis."""
    if settings.database_password_file is None or settings.redis_password_file is None:
        return False
    engine: Engine | None = None
    client: Redis | None = None
    try:
        engine = create_engine_from_settings(settings)
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1")) != 1:
                return False
        client = create_redis(settings)
        return client.ping() is True
    except Exception:
        return False
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
        if engine is not None:
            with suppress(Exception):
                engine.dispose()
