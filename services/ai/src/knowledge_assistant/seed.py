"""Idempotent fictional fixtures, inserted through the same RLS boundary as runtime work."""

import sys
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.models import knowledge_sources, teams

DEMO_TEAM_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TEAM_ID = UUID("22222222-2222-4222-8222-222222222222")

_FIXTURES = (
    (
        DEMO_TEAM_ID,
        "Fictional Northstar Workshop",
        UUID("11111111-1111-4111-8111-111111111112"),
        "Workshop pickup policy (fictional)",
        "This fictional workshop holds completed orders for seven days after notification.",
    ),
    (
        OTHER_TEAM_ID,
        "Fictional Cedar Studio",
        UUID("22222222-2222-4222-8222-222222222223"),
        "Studio appointment policy (fictional)",
        "This fictional studio offers appointments on weekdays from 10:00 to 16:00.",
    ),
)


def seed_demo(engine: Engine) -> None:
    """Repeated seeding changes neither row counts nor existing demo content."""
    for team_id, team_name, source_id, source_name, content in _FIXTURES:
        with tenant_connection(engine, team_id) as connection:
            connection.execute(
                insert(teams)
                .values(id=team_id, name=team_name)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            connection.execute(
                insert(knowledge_sources)
                .values(id=source_id, team_id=team_id, name=source_name, content=content)
                .on_conflict_do_nothing(index_elements=["id"])
            )


def main() -> int:
    engine = None
    try:
        settings = Settings()
        if settings.database_user != "aka_app":
            raise RuntimeError("Seeding requires the runtime role")
        engine = create_engine_from_settings(settings)
        seed_demo(engine)
    except Exception:
        print("Demo seed failed; check database availability and configuration.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print("Fictional demo fixtures are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
