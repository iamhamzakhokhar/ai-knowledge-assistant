"""Initialize persistent, role-separated development secrets inside Docker volumes."""

import os
import re
import secrets
from pathlib import Path


def secure_directory(path: Path, group: int) -> None:
    if path.is_symlink():
        raise ValueError("Secret directories must not be symbolic links")
    path.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        os.chown(path, 0, group)
    path.chmod(0o750)


def write_private(path: Path, value: str, group: int) -> None:
    """Atomically replace a generated file without exposing its value in arguments."""
    if path.is_symlink():
        raise ValueError("Secret files must not be symbolic links")
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), 0, group)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def existing_or_new(path: Path, group: int) -> str:
    if path.is_symlink():
        raise ValueError("Secret files must not be symbolic links")
    if path.exists():
        value = path.read_text().strip()
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("An existing development secret is invalid; it was not changed")
        return value
    value = secrets.token_hex(32)
    write_private(path, value + "\n", group)
    return value


def initialize(
    postgres: Path,
    application: Path,
    redis: Path,
    *,
    postgres_group: int = 999,
    application_group: int = 10001,
    redis_group: int = 999,
) -> None:
    for directory, group in (
        (postgres, postgres_group),
        (application, application_group),
        (redis, redis_group),
    ):
        secure_directory(directory, group)
    existing_or_new(postgres / "postgres_password", postgres_group)
    existing_or_new(application / "database_password", application_group)
    redis_password = existing_or_new(application / "redis_password", application_group)
    redis_file = redis / "redis_password"
    if redis_file.exists():
        if not secrets.compare_digest(existing_or_new(redis_file, redis_group), redis_password):
            raise ValueError("Redis secret volumes disagree; existing credentials were preserved")
    else:
        write_private(redis_file, redis_password + "\n", redis_group)
    write_private(
        redis / "redis.conf",
        "\n".join(
            [
                "bind 0.0.0.0",
                "protected-mode yes",
                "port 6379",
                "dir /data",
                "appendonly yes",
                "appendfsync everysec",
                "save 60 1",
                "requirepass " + redis_password,
                "",
            ]
        ),
        redis_group,
    )


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run this initializer through the Compose secret-init service")
    initialize(Path("/run/postgres-secrets"), Path("/run/app-secrets"), Path("/run/redis-secrets"))
    print("Development secrets are ready; existing passwords were preserved.")


if __name__ == "__main__":
    main()
