"""Exercise installer security properties with synthetic archives and no downloads."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from install_security_tools import (
    TOOLS,
    InstallationError,
    verified_binary,
    write_executable,
)


def package(entries: list[tuple[str, bytes | None]]):
    """Give synthetic archive bytes their own fixture digest; None makes a symlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            if content is None:
                member.type, member.linkname = tarfile.SYMTYPE, "/outside"
                archive.addfile(member)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
    content = buffer.getvalue()
    return replace(TOOLS[0], sha256=hashlib.sha256(content).hexdigest()), content


class InstallerSecurityProperties(unittest.TestCase):
    def test_wrong_digest_is_rejected_before_tar_is_parsed(self):
        with patch("install_security_tools.tarfile.open") as parse_archive:
            with self.assertRaisesRegex(InstallationError, "Checksum verification failed"):
                verified_binary(TOOLS[0], b"unverified archive")
            parse_archive.assert_not_called()

    def test_verified_archive_requires_one_nonempty_regular_named_binary(self):
        cases = {
            "symlink": [("gitleaks", None)],
            "duplicate": [("gitleaks", b"first"), ("gitleaks", b"second")],
            "missing": [("nested/gitleaks", b"wrong location")],
            "empty": [("gitleaks", b"")],
        }
        for label, entries in cases.items():
            with self.subTest(label=label):
                tool, archive = package(entries)
                with self.assertRaises(InstallationError):
                    verified_binary(tool, archive)

    def test_only_named_binary_is_published_atomically_with_mode_0755(self):
        payload = b"synthetic binary; never executed"
        tool, archive = package([("gitleaks", payload), ("../../unwanted-file", b"ignored")])
        binary = verified_binary(tool, archive)
        self.assertEqual(binary, payload)
        with tempfile.TemporaryDirectory(prefix="aka-installer-control-") as temporary:
            directory = Path(temporary).resolve()
            project = Path(__file__).resolve().parents[1]
            self.assertNotIn(project, (directory, *directory.parents))
            target = directory / tool.name
            target.write_bytes(b"previous binary")
            real_replace = os.replace

            def observe_publish(source, destination):
                self.assertEqual(target.read_bytes(), b"previous binary")
                self.assertEqual(Path(source).read_bytes(), payload)
                self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o755)
                self.assertEqual(destination, target)
                real_replace(source, destination)

            with patch("install_security_tools.os.replace", side_effect=observe_publish):
                write_executable(directory, tool.name, binary)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            self.assertEqual(list(directory.iterdir()), [target])

    def test_publish_failure_preserves_previous_binary_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix="aka-installer-failure-") as temporary:
            directory = Path(temporary).resolve()
            project = Path(__file__).resolve().parents[1]
            self.assertNotIn(project, (directory, *directory.parents))
            target = directory / "gitleaks"
            target.write_bytes(b"previous binary")
            failure = patch("install_security_tools.os.replace", side_effect=OSError("control"))
            with failure, self.assertRaises(OSError):
                write_executable(directory, "gitleaks", b"replacement")
            self.assertEqual(target.read_bytes(), b"previous binary")
            self.assertEqual(list(directory.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
