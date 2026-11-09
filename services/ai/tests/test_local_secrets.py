import os
from pathlib import Path

import pytest

from knowledge_assistant.local_secrets import initialize


def prepare(root: Path) -> tuple[Path, Path, Path]:
    paths = (root / "postgres", root / "application", root / "redis")
    initialize(
        *paths,
        postgres_user=os.geteuid(),
        postgres_group=os.getgid(),
        application_user=os.geteuid(),
        application_group=os.getgid(),
        redis_user=os.geteuid(),
        redis_group=os.getgid(),
    )
    return paths


def test_credentials_are_private_separate_and_stable(tmp_path: Path) -> None:
    postgres, application, redis = prepare(tmp_path)
    owner_password = (postgres / "postgres_password").read_text().strip()
    app_password = (application / "database_password").read_text().strip()
    redis_password = (application / "redis_password").read_text().strip()
    assert len({owner_password, app_password, redis_password}) == 3
    assert all(len(value) == 64 for value in (owner_password, app_password, redis_password))
    assert (redis / "redis_password").read_text().strip() == redis_password
    assert "requirepass " + redis_password in (redis / "redis.conf").read_text()
    assert (postgres / "migration_password").read_text().strip() == owner_password
    assert not (application / "postgres_password").exists()
    assert not (application / "migration_password").exists()
    assert not (redis / "database_password").exists()
    for directory in (postgres, application, redis):
        assert directory.stat().st_mode & 0o777 == 0o750
        for file in directory.iterdir():
            assert file.stat().st_mode & 0o777 == 0o400
            assert file.stat().st_uid == os.geteuid()
    prepare(tmp_path)
    assert (postgres / "postgres_password").read_text().strip() == owner_password
    assert (application / "database_password").read_text().strip() == app_password


def test_legacy_group_readable_files_are_tightened_without_rotation(tmp_path: Path) -> None:
    postgres, application, redis = prepare(tmp_path)
    snapshots = {
        file: file.read_bytes()
        for directory in (postgres, application, redis)
        for file in directory.iterdir()
    }
    migration_password = postgres / "migration_password"
    migration_password.unlink()
    for path in snapshots:
        if path != migration_password:
            path.chmod(0o440)
    prepare(tmp_path)
    for path, value in snapshots.items():
        assert path.read_bytes() == value
        assert path.stat().st_mode & 0o777 == 0o400


def test_conflicting_migration_copy_is_never_overwritten(tmp_path: Path) -> None:
    postgres, _, _ = prepare(tmp_path)
    migration_password = postgres / "migration_password"
    migration_password.chmod(0o600)
    migration_password.write_text("0" * 64 + "\n")
    with pytest.raises(ValueError, match="credentials were preserved"):
        prepare(tmp_path)
    assert migration_password.read_text() == "0" * 64 + "\n"


def test_missing_canonical_password_never_generates_a_conflicting_copy(tmp_path: Path) -> None:
    postgres, _, _ = prepare(tmp_path)
    migration_password = (postgres / "migration_password").read_bytes()
    (postgres / "postgres_password").unlink()
    with pytest.raises(ValueError, match="copies are incomplete"):
        prepare(tmp_path)
    assert not (postgres / "postgres_password").exists()
    assert (postgres / "migration_password").read_bytes() == migration_password


def test_invalid_existing_secret_is_never_rotated(tmp_path: Path) -> None:
    postgres, _, _ = prepare(tmp_path)
    password = postgres / "postgres_password"
    password.chmod(0o600)
    password.write_text("incomplete")
    with pytest.raises(ValueError, match="was not changed"):
        prepare(tmp_path)
    assert password.read_text() == "incomplete"


def test_symlink_is_rejected_without_modifying_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("untouched")
    postgres = tmp_path / "postgres"
    postgres.mkdir()
    (postgres / "postgres_password").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic links"):
        prepare(tmp_path)
    assert target.read_text() == "untouched"
