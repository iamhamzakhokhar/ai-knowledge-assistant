"""Prove the advisory gate detects a known vulnerable lockfile without installing it.

The fixture exists only in a private temporary directory outside this checkout.
Its sole application dependency is never imported, downloaded as executable code,
or materialized in node_modules. Advisory reference:
https://github.com/advisories/GHSA-35jh-r3h4-6jhm
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ADVISORY_ID = "GHSA-35jh-r3h4-6jhm"
ADVISORY_URL = f"https://github.com/advisories/{ADVISORY_ID}"
REGISTRY = "https://registry.npmjs.org/"
PNPM_VERSION = "12.18.0"


class AuditControlError(RuntimeError):
    """A failed control or unavailable registry, with no raw process output."""


def run_pnpm(
    directory: Path, environment: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["pnpm", *arguments, "--userconfig", os.devnull, "--registry", REGISTRY],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        raise AuditControlError("The audit control could not run to completion") from None


def check_control() -> None:
    project = Path(__file__).resolve().parents[1]
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    if temporary_parent == project or project in temporary_parent.parents:
        raise AuditControlError(
            "The audit control requires a temporary location outside the project"
        )

    with tempfile.TemporaryDirectory(prefix="aka-audit-control-") as temporary:
        directory = Path(temporary)
        # Discard npm/pnpm overrides and token variables before selecting the
        # public registry and empty user/global authentication configuration.
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.lower().startswith(("npm_config_", "pnpm_config_"))
            and key not in {"NODE_AUTH_TOKEN", "NPM_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}
        }
        global_config = directory / "empty-global.npmrc"
        global_config.touch(mode=0o600)
        environment.update(
            {
                "NPM_CONFIG_USERCONFIG": os.devnull,
                "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                "NPM_CONFIG_REGISTRY": REGISTRY,
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "XDG_STATE_HOME": str(directory / "state"),
            }
        )
        manifest = directory / "package.json"
        manifest.write_text(
            json.dumps(
                {
                    "name": "aka-dependency-audit-negative-control",
                    "version": "0.0.0",
                    "private": True,
                    "packageManager": f"pnpm@{PNPM_VERSION}",
                    "dependencies": {"lodash": "4.17.20"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        prepared = run_pnpm(
            directory,
            environment,
            "install",
            "--lockfile-only",
            "--ignore-scripts",
            "--ignore-pnpmfile",
            "--ignore-workspace",
            "--no-frozen-lockfile",
        )
        if prepared.returncode != 0 or not (directory / "pnpm-lock.yaml").is_file():
            raise AuditControlError("Unable to prepare the isolated audit lockfile")
        if (directory / "node_modules").exists():
            raise AuditControlError("The audit control unexpectedly installed packages")

        audited = run_pnpm(
            directory, environment, "audit", "--json", "--audit-level=high", "--ignore-workspace"
        )
        try:
            report = json.loads(audited.stdout)
            counts = report["metadata"]["vulnerabilities"]
            high, critical = counts["high"], counts["critical"]
            advisories = report["advisories"]
        except json.JSONDecodeError, KeyError, TypeError:
            raise AuditControlError("The registry did not return a valid audit report") from None
        if (
            audited.returncode != 1
            or type(high) is not int
            or type(critical) is not int
            or high + critical < 1
            or not isinstance(advisories, dict)
            or not any(
                isinstance(advisory, dict)
                and advisory.get("url") == ADVISORY_URL
                and advisory.get("severity") in {"high", "critical"}
                for advisory in advisories.values()
            )
            or (directory / "node_modules").exists()
        ):
            raise AuditControlError("The audit gate did not identify the expected high advisory")


def main() -> int:
    try:
        check_control()
    except AuditControlError as error:
        print(f"Dependency audit negative control failed: {error}.", file=sys.stderr)
        return 1
    except Exception:
        print("Dependency audit negative control could not complete.", file=sys.stderr)
        return 1
    print(f"{ADVISORY_ID}: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
