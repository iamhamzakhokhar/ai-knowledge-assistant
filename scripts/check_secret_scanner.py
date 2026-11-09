"""Check real scanner detection, staged-index coverage and redaction outside the project."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_VERSION = "8.30.1"


class ScannerCheckError(RuntimeError):
    """A control failure that never includes generated fixture content or tool output."""


def run(
    command: list[str], fixture: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=fixture,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def assert_redacted(result: subprocess.CompletedProcess, report: str, synthetic: str) -> None:
    if any(synthetic in output for output in (result.stdout, result.stderr, report)):
        raise ScannerCheckError(
            "Scanner control failed: generated fixture content was not redacted"
        )


def scan(
    executable: str,
    arguments: list[str],
    fixture: Path,
    report: Path,
    environment: dict[str, str],
    synthetic: str,
    *,
    expect_finding: bool,
) -> None:
    report.unlink(missing_ok=True)
    result = run(
        [
            executable,
            *arguments,
            "--redact=100",
            "--no-banner",
            "--report-format=json",
            "--report-path",
            str(report),
            ".",
        ],
        fixture,
        environment,
    )
    content = report.read_text() if report.exists() else ""
    assert_redacted(result, content, synthetic)
    expected_code = 1 if expect_finding else 0
    if result.returncode != expected_code:
        raise ScannerCheckError("Scanner control returned an unexpected status")
    try:
        findings = json.loads(content)
    except ValueError:
        raise ScannerCheckError("Scanner control did not create a valid JSON report") from None
    if not isinstance(findings, list):
        raise ScannerCheckError("Scanner control report has an unexpected shape")
    if expect_finding:
        matching = [finding for finding in findings if finding.get("RuleID") == "github-pat"]
        if not matching or any(finding.get("Secret") != "REDACTED" for finding in matching):
            raise ScannerCheckError("Scanner control did not detect and redact its synthetic rule")
    elif findings:
        raise ScannerCheckError("The clean scanner control unexpectedly reported findings")


def check_scanner(executable: str, project: Path) -> None:
    environment = os.environ.copy()
    # Fixture Git commands never use the person's identity, signing or global hooks.
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="aka-secret-scanner-") as temporary:
        scratch = Path(temporary).resolve()
        if scratch == project or project in scratch.parents:
            raise ScannerCheckError(
                "Scanner controls require a temporary directory outside the repo"
            )
        fixture = scratch / "fixture"
        fixture.mkdir()
        version = run([executable, "version"], fixture, environment)
        if version.returncode != 0 or version.stdout.strip() != EXPECTED_VERSION:
            raise ScannerCheckError(f"Scanner controls require gitleaks {EXPECTED_VERSION}")
        commands = (
            ["git", "init", "--quiet"],
            ["git", "config", "user.name", "Scanner control"],
            ["git", "config", "user.email", "scanner-control@example.invalid"],
            ["git", "config", "commit.gpgsign", "false"],
        )
        for command in commands:
            if run(command, fixture, environment).returncode != 0:
                raise ScannerCheckError("Could not initialize the isolated scanner fixture")
        (fixture / "README.md").write_text("Clean, temporary scanner control.\n")
        if run(["git", "add", "README.md"], fixture, environment).returncode != 0:
            raise ScannerCheckError("Could not stage the clean fixture baseline")
        if (
            run(
                ["git", "commit", "--quiet", "-m", "test: clean scanner control"],
                fixture,
                environment,
            ).returncode
            != 0
        ):
            raise ScannerCheckError("Could not commit the clean fixture baseline")

        # Construct after the clean commit; never commit or print the generated value.
        alphabet = string.ascii_letters + string.digits
        synthetic = "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))
        candidate = fixture / "scanner-control.txt"
        candidate.write_text("token = " + synthetic + "\n")
        candidate.chmod(0o600)
        report = scratch / "redacted-report.json"
        scan(
            executable,
            ["dir"],
            fixture,
            report,
            environment,
            synthetic,
            expect_finding=True,
        )
        if run(["git", "add", candidate.name], fixture, environment).returncode != 0:
            raise ScannerCheckError("Could not stage the synthetic scanner control")
        candidate.write_text("Clean working copy; the index still requires its own scan.\n")
        scan(
            executable,
            ["dir"],
            fixture,
            report,
            environment,
            synthetic,
            expect_finding=False,
        )
        scan(
            executable,
            ["git", "--staged"],
            fixture,
            report,
            environment,
            synthetic,
            expect_finding=True,
        )
        history = run(["git", "log", "-p", "--all"], fixture, environment)
        assert_redacted(history, "", synthetic)
        if history.returncode != 0:
            raise ScannerCheckError("Could not inspect the clean fixture history")
        pre_push = run([str(project / ".githooks/pre-push")], fixture, environment)
        assert_redacted(pre_push, "", synthetic)
        if pre_push.returncode != 0:
            raise ScannerCheckError("The pre-push history command failed on clean fixture history")


def main() -> int:
    executable = shutil.which("gitleaks")
    if executable is None:
        print("Install gitleaks before running scanner controls.", file=sys.stderr)
        return 1
    project = Path(__file__).resolve().parents[1]
    try:
        check_scanner(executable, project)
    except ScannerCheckError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError, subprocess.TimeoutExpired:
        print(
            "Scanner controls could not complete; generated content was not logged.",
            file=sys.stderr,
        )
        return 1
    print(
        "Scanner controls passed: untracked detection, staged index, redaction and clean history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
