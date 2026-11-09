"""Install reviewed Linux security-tool archives after verifying their pinned SHA256 hashes."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class InstallationError(RuntimeError):
    """An installation failure with a message safe to print in CI."""


@dataclass(frozen=True)
class SecurityTool:
    name: str
    version: str
    url: str
    sha256: str


TOOLS = (
    SecurityTool(
        "gitleaks",
        "8.30.1",
        "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
        "gitleaks_8.30.1_linux_x64.tar.gz",
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    ),
    SecurityTool(
        "actionlint",
        "1.7.12",
        "https://github.com/rhysd/actionlint/releases/download/v1.7.12/"
        "actionlint_1.7.12_linux_amd64.tar.gz",
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    ),
)


def download_archive(tool: SecurityTool) -> bytes:
    request = Request(tool.url, headers={"User-Agent": "KnowledgeDesk-security-tools"})
    try:
        with urlopen(request, timeout=30) as response:
            if not response.url.startswith("https://"):
                raise InstallationError("Security tool downloads must remain on HTTPS")
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
    except InstallationError:
        raise
    except HTTPException, OSError:
        raise InstallationError(f"Could not download {tool.name} {tool.version}") from None
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise InstallationError(f"The {tool.name} archive exceeds the download size limit")
    return archive


def verified_binary(tool: SecurityTool, archive: bytes) -> bytes:
    """Verify the complete archive before parsing or extracting any member."""
    if hashlib.sha256(archive).hexdigest() != tool.sha256:
        raise InstallationError(f"Checksum verification failed for {tool.name} {tool.version}")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            matches = [member for member in package.getmembers() if member.name == tool.name]
            if len(matches) != 1 or not matches[0].isfile():
                raise InstallationError(f"The {tool.name} archive lacks one regular named binary")
            stream = package.extractfile(matches[0])
            if stream is None:
                raise InstallationError(f"The {tool.name} binary could not be read")
            with stream:
                binary = stream.read()
            if not binary:
                raise InstallationError(f"The {tool.name} binary is empty")
            return binary
    except InstallationError:
        raise
    except OSError, tarfile.TarError:
        raise InstallationError(f"The verified {tool.name} archive could not be read") from None


def write_executable(directory: Path, name: str, binary: bytes) -> None:
    """Publish only a regular executable, using an atomic replacement in its target directory."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{name}-", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(binary)
            stream.flush()
            os.fchmod(stream.fileno(), 0o755)
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path, help="Job-local binary directory")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        print("This installer supports Linux x86_64 CI runners only.", file=sys.stderr)
        return 1
    try:
        directory = args.directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        for tool in TOOLS:
            binary = verified_binary(tool, download_archive(tool))
            write_executable(directory, tool.name, binary)
            print(f"Installed checksum-verified {tool.name} {tool.version}.")
    except InstallationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Could not write the job-local security tools.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
