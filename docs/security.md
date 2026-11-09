# Security controls and limitations

This guide describes the repository's security configuration and the checks to
verify it. A configured workflow or updater is not evidence of a successful run.
Only the current `main` branch is supported during this pre-release stage. See
[SECURITY.md](../SECURITY.md) for private reporting and support scope.

## Secrets and pull-request jobs

Gitleaks **8.30.1** is the selected secret scanner. The local pre-commit hook scans
the staged index before linting; pre-push scans all available Git history. CI
uses a full-history checkout and the same fully redacted history scan. The
commands are:

```sh
gitleaks git --staged --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner --log-opts='--all --full-history --no-ext-diff' .
```

Scanning the staged index matters: a clean working file can still have an older
secret-bearing version staged. Local hooks require the tool on `PATH` and are
not a substitute for CI or GitHub's native push protection. A scanner finding
must be investigated; do not suppress it just to make a check green.

Real provider, deployment and business credentials must not enter pull-request
jobs. Build/test/security/audit jobs use read-only repository access. CodeQL has
the separate `security-events: write` permission needed to publish analysis; it
does not receive repository-content write permission. The workflows use ordinary
`pull_request` events, not `pull_request_target`, and checkout does not persist
Git credentials. The local infrastructure uses generated disposable test
credentials rather than production accounts.

The Linux x86_64 CI tool installer downloads pinned Gitleaks **8.30.1** and
actionlint **1.7.12** archives and verifies their recorded SHA256 hashes before
extracting the named binaries. Other platforms require the matching reviewed
upstream releases. See [contributor setup](../CONTRIBUTING.md).

## CodeQL static analysis

[The CodeQL workflow](../.github/workflows/codeql.yml) analyzes
`actions`, `javascript-typescript` and `python` in separate matrix jobs. It pins CodeQL
Action **4.39.3** to an immutable commit, uses its linked **2.27.4** bundle,
selects `security-extended` queries and sets `build-mode: none`. These interpreted
language scans require no application build or application dependency install.
There are no workflow path filters or custom exclusions hiding tests.

The workflow runs on pushes to `main`, pull requests targeting `main`, Mondays
at **06:23 UTC**, and manual dispatch. `analyze` uploads SARIF results to GitHub
code scanning. TypeScript 7.0 is included in the
[language support table](https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/);
[CodeQL 2.27.3 introduced Python 3.15 syntax support](https://github.blog/changelog/2026-11-03-codeql-2-27-3-adds-python-3-15-features-and-json-data-extensions/).

Main requires `Checks`, `Security`, `Dependency audit`, `CodeQL (actions)`,
`CodeQL (javascript-typescript)` and `CodeQL (python)` from the GitHub Actions
app (15368), plus the finding-based `CodeQL` result from GitHub Advanced Security
(57789). These exact contexts were observed passing before being required.
Protection includes administrators, requires an up-to-date PR and resolved
conversations, and prevents force pushes and deletion. Manual-dispatch runs are
diagnostics and do not replace pull-request-triggered checks.

A successful analysis job shows that scanning completed. The separate native
`CodeQL` result applies GitHub's default error/high/critical finding policy.
An intentional critical command-injection fixture failed this required result
while all three analysis jobs passed; see the validation evidence below.

PR results focus on alerts introduced by the change. Inspect the default-branch
baseline after initial setup and scanner upgrades as well. The first `main`
Python baseline found existing group-readable credential creation
(`py/overly-permissive-file`) that the initial PR results had not reported.
The initializer now creates owner-only files for each consuming container UID;
PostgreSQL and migrations use separate identical copies. Existing values are
preserved when legacy permissions are tightened. See the
[credential upgrade instructions](infrastructure.md#connections-and-local-credentials).

## Dependency updates and audits

[Dependabot configuration](../.github/dependabot.yml) covers five ecosystems:

| Ecosystem        | Configured scope                                              |
| ---------------- | ------------------------------------------------------------- |
| `npm`            | pnpm workspace manifests and lockfile at the repository root. |
| `uv`             | Python service manifests and lockfile under `services/ai`.    |
| `github-actions` | Workflow action references.                                   |
| `docker`         | Web and Python service Dockerfiles.                           |
| `docker-compose` | Images in the root Compose configuration.                     |

Version-update checks are scheduled weekly on Monday at **09:00
America/New_York**, including daylight-saving transitions. Minor/patch updates
are grouped for JavaScript and Python; major updates require individual human
review. There is no configured automatic merge. Review release notes, migration
requirements and CI results before merging an update. Security updates can be
triggered by advisories separately from the version-update schedule.

There are known coverage limits:

- GitHub's dependency graph does not currently ingest this pnpm 12 workspace's
  multi-document lockfile graph. Native JavaScript dependency alerts and security
  updates must not be treated as complete for this graph.
- The CI dependency-audit job runs `pnpm audit --audit-level=high` and fails on
  high/critical advisories reported by the registry. This is a separate check: it
  does **not** restore GitHub's JavaScript dependency graph, native alerts or
  security-update coverage. It also depends on registry advisory availability.
- Initial native version checks succeeded for
  [uv](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42654996130),
  [pnpm](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42654990864),
  Docker, Actions and Compose. Logs recognized the current versions and reported
  no update needed. No update PR was opened, so actual lockfile rewriting and
  proposed-update validation remain untested.
- External images used by multi-stage `COPY --from`, including the pinned uv
  image in the Python Dockerfile, need manual review. Do not assume an image
  updater will revise that reference with ordinary `FROM` updates. Review the uv
  image pin alongside changes to the uv tool version.

## Native repository settings

GitHub settings were read back on November 9, 2026 for
`iamhamzakhokhar/ai-knowledge-assistant`: dependency alerts and automated security
updates are enabled, private vulnerability reporting is enabled, and native
secret scanning and push protection are enabled. Default workflow tokens have
read permissions and cannot approve pull requests. Workflow files alone do not
enable these settings.

Required contexts and the CodeQL finding gate were verified through actual PR
runs, including deliberate failures. Enabling dependency alerts does not remove
the pnpm dependency-graph limitation described above.

## Validation and negative controls

After installing the tools, run:

```sh
actionlint .github/workflows/*.yml
python3 scripts/check_secret_scanner.py
python3 scripts/check_security_installer.py
pnpm audit --audit-level=high
python3 scripts/check_dependency_audit.py
```

The secret-scanner control creates an isolated temporary Git repository outside
the project. It generates synthetic, unusable token-shaped content, checks
untracked/staged detection and full redaction, and verifies the project's
pre-push command against clean fixture history. It does not commit the generated
token-shaped value or use a real credential. Reports remain in the temporary
fixture and generated content is not printed.

The installer controls reject changed checksums before archive parsing, unsafe
or duplicate members, and empty binaries. The audit control generates only a
temporary lockfile for a known vulnerable lodash version and requires the real
audit to report its high-severity advisory. It never installs or executes that
package. Both controls passed locally and in the security workflow.

[Issue #3's disposable PR](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/pull/29)
exercised these failures on a separate branch that must never be merged:

| Control                                    | Observed required-check result                                                                                                                                                                                                                                 |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime-generated invalid TypeScript       | [`Checks` failed at Lint and formatting](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42650956102).                                                                                                                                  |
| Runtime-generated failing unit test        | [`Checks` failed at API unit tests](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42651160510).                                                                                                                                       |
| Unexecuted Flask command-injection fixture | [Native `CodeQL` failed](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/runs/129278855198) on critical `py/command-line-injection`; [all analysis jobs passed](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42651423741). |

The fixtures were removed from the validation branch. They never enter the
feature PR or `main`; synthetic token-shaped values are never committed even to
the disposable branch. The clean feature PR's initial
[CI](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42647945876),
[Security](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42647945957)
and [CodeQL](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42647946293)
runs passed, and all three language analyses uploaded with zero findings.

## Exposure response

If a scanner detects a real secret, revoke/rotate it, remove the exposed value,
and coordinate approved history cleanup where necessary. Avoid publishing raw
scanner output or copying the value into an issue. Report privately with the
minimum affected paths, commits and redacted reproduction needed to investigate.
Changing an allowlist or deleting the latest file is not credential revocation.
