"""Owner-only migration entry point, with idempotent runtime-role provisioning."""

import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Connection, text

from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings
from knowledge_assistant.secret_files import read_secret_file


class MigrationError(RuntimeError):
    """A safe migration error that never includes driver messages or credentials."""


def alembic_config(connection: Connection | None = None) -> Config:
    """Find migrations in either the source checkout or an installed wheel."""
    package_dir = Path(__file__).resolve().parent
    root = package_dir if (package_dir / "migrations").is_dir() else package_dir.parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def provision_application_role(connection: Connection, password_file: Path | None) -> None:
    """Create aka_app once; existing credentials are deliberately never rotated."""
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('aka.role-bootstrap'))"))
    role = (
        connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
                "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = 'aka_app'"
            )
        )
        .mappings()
        .first()
    )
    if role is not None:
        forbidden = (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolinherit",
            "rolreplication",
            "rolbypassrls",
        )
        if not role["rolcanlogin"] or any(role[flag] for flag in forbidden):
            raise MigrationError("The application role has unexpected privileges")
        if connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = 'aka_app'::regrole)")
        ):
            raise MigrationError("The application role must not inherit other roles")
        if connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relowner = 'aka_app'::regrole) "
                "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = 'aka_app'::regrole) "
                "OR EXISTS (SELECT 1 FROM pg_database WHERE datname = current_database() "
                "AND datdba = 'aka_app'::regrole)"
            )
        ):
            raise MigrationError("The application role must not own database objects")
        return

    password = read_secret_file(password_file)
    # libpq creates a SCRAM verifier locally: plaintext never enters a SQL string.
    raw_connection = connection.connection.driver_connection
    verifier = raw_connection.pgconn.encrypt_password(
        password.encode("utf-8"), b"aka_app", b"scram-sha-256"
    ).decode("ascii")
    statement = sql.SQL(
        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
        "NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Identifier("aka_app"), sql.Literal(verifier))
    with raw_connection.cursor() as cursor:
        cursor.execute(statement)


def run_migrations(
    settings: Settings,
    operation: str,
    revision: str | None = None,
    *,
    app_password_file: Path | None = None,
) -> None:
    """Run an explicit revision operation without putting credentials in Alembic config."""
    if settings.database_user != "aka_owner":
        raise MigrationError("Migrations require the dedicated database owner role")
    if operation not in {"upgrade", "downgrade", "current"}:
        raise MigrationError("Unsupported migration operation")
    if operation != "current" and revision is None:
        raise MigrationError("A migration revision is required")
    engine = None
    try:
        engine = create_engine_from_settings(settings)
        with engine.begin() as connection:
            if operation == "upgrade":
                provision_application_role(connection, app_password_file)
            config = alembic_config(connection)
            if operation == "current":
                command.current(config)
            elif operation == "upgrade":
                command.upgrade(config, revision)
            else:
                command.downgrade(config, revision)
    except Exception:
        raise MigrationError(
            "Migration failed; check database availability and configuration"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run owner-only database migrations")
    parser.add_argument("operation", choices=("upgrade", "downgrade", "current"))
    parser.add_argument("revision", nargs="?")
    args = parser.parse_args()
    if args.operation != "current" and args.revision is None:
        parser.error("upgrade and downgrade require an explicit revision")
    try:
        configured_file = os.environ.get("APP_DATABASE_PASSWORD_FILE")
        run_migrations(
            Settings(),
            args.operation,
            args.revision,
            app_password_file=Path(configured_file) if configured_file else None,
        )
    except Exception:
        print("Migration failed; check database availability and configuration.", file=sys.stderr)
        return 1
    print("Migration command completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
