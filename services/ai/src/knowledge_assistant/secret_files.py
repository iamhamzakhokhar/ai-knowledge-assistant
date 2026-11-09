"""Read one explicitly configured secret file without including its contents in errors."""

from pathlib import Path


class SecretFileError(RuntimeError):
    """A configured secret cannot be used; details deliberately exclude its value."""


def read_secret_file(path: Path | None) -> str:
    """Read a small, single-line UTF-8 secret; allow the usual trailing newline."""
    if path is None:
        raise SecretFileError("A required secret file is not configured")
    try:
        with path.open(encoding="utf-8") as secret_file:
            value = secret_file.read(8193)
    except OSError, UnicodeError:
        raise SecretFileError("Unable to read the configured secret file") from None
    oversized = len(value) > 8192
    value = value.rstrip("\r\n")
    if oversized or not value or any(char in value for char in ("\r", "\n", "\x00")):
        raise SecretFileError("The configured secret file must contain one nonempty line")
    return value
