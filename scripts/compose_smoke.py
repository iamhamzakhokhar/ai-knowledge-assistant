"""Validate only a disposable CI Compose project, including persistence/recovery."""

import os
import re
import subprocess
from pathlib import Path

from check_compose import main as check_compose


def main() -> None:
    project = os.environ.get("COMPOSE_PROJECT_NAME", "")
    if re.fullmatch(r"aka-ci-[a-z0-9-]+", project) is None:
        raise SystemExit("Use a disposable COMPOSE_PROJECT_NAME starting with aka-ci-")
    compose = ["docker", "compose", "--project-name", project, "--profile", "test"]

    def run(*arguments: str, **options: object) -> subprocess.CompletedProcess:
        return subprocess.run([*compose, *arguments], check=True, **options)

    def count_sources(database: str = "knowledge_assistant") -> str:
        return run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "aka_owner",
            "-d",
            database,
            "-Atqc",
            "SELECT count(*) FROM knowledge_sources",
            capture_output=True,
            text=True,
        ).stdout.strip()

    check_compose()
    run("config", "--quiet")
    run("up", "--build", "--wait", "--wait-timeout", "240", "web", "worker")
    run("run", "--rm", "--no-deps", "seed")
    run("run", "--rm", "--build", "--no-deps", "integration-tests")

    expected = count_sources()
    if int(expected) < 2:
        raise RuntimeError("Fictional demo seed data is missing")
    marker = (
        "from knowledge_assistant.config import Settings; "
        "from knowledge_assistant.queueing import create_redis; c=create_redis(Settings()); "
    )
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        marker + "c.set('aka:ci:persistence', 'survives-restart'); c.close()",
    )
    run("restart", "postgres", "redis")
    run("up", "--wait", "--wait-timeout", "180", "web", "worker")
    if count_sources() != expected:
        raise RuntimeError("PostgreSQL data did not survive restart")
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        marker
        + (
            "assert c.get('aka:ci:persistence') in "
            "(b'survives-restart', 'survives-restart'); "
            "c.delete('aka:ci:persistence'); c.close()"
        ),
    )

    backup = Path(".local/backups/ci.dump")
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            run(
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "aka_owner",
                "-d",
                "knowledge_assistant",
                "--format=custom",
                stdout=output,
            )
        run("exec", "-T", "postgres", "createdb", "-U", "aka_owner", "aka_restore_check")
        try:
            with backup.open("rb") as source:
                run(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "-U",
                    "aka_owner",
                    "-d",
                    "aka_restore_check",
                    "--exit-on-error",
                    stdin=source,
                )
            if count_sources("aka_restore_check") != expected:
                raise RuntimeError("Backup restore did not recover the seeded records")
        finally:
            run("exec", "-T", "postgres", "dropdb", "-U", "aka_owner", "aka_restore_check")
    finally:
        backup.unlink(missing_ok=True)
    print("Compose, RLS, queue jobs, restart persistence, and backup/restore checks passed.")


if __name__ == "__main__":
    main()
