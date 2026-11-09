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

Stable check names are planned as `Checks`, `Security`, `Dependency audit`,
`CodeQL (actions)`, `CodeQL (javascript-typescript)` and `CodeQL (python)`. Required-check settings
must use the actual observed contexts after successful runs. Manual-dispatch
runs are useful diagnostics and do not replace pull-request-triggered checks.

A successful analysis job shows that scanning completed; it does not mean every
finding blocks a merge. Alert-based enforcement requires GitHub's native
code-scanning merge-protection rule and an explicitly chosen severity policy.
Its live configuration and result are a separate verification step.

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
- The native `uv` updater is configured. Compatibility and successful lockfile
  updates are not established until its first native run and proposed update are
  inspected; configuration alone is not an end-to-end verification.
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

Code-scanning alert-gate verification and the new required check contexts are
verified through actual workflow runs before merge. Enabling dependency alerts
does not remove the pnpm dependency-graph limitation described above.

## Validation and negative controls

After installing the tools, the intended validation commands include:

```sh
actionlint .github/workflows/*.yml
python3 scripts/check_secret_scanner.py
pnpm audit --audit-level=high
```

The secret-scanner control creates an isolated temporary Git repository outside
the project. It generates synthetic, unusable token-shaped content, checks
untracked/staged detection and full redaction, and verifies the project's
pre-push command against clean fixture history. It does not commit the generated
token-shaped value or use a real credential. Reports remain in the temporary
fixture and generated content is not printed.

Additional negative controls should use isolated fixtures with synthetic data:
an invalid workflow must fail validation, a deliberate high-severity dependency
fixture must fail the audit, and a representative static-analysis finding must
be observed through the chosen native alert gate. Keep these controls out of the
application's real history and document what actually ran. No passed counts,
complete protection claims or response guarantees are implied by this plan.

## Exposure response

If a scanner detects a real secret, revoke/rotate it, remove the exposed value,
and coordinate approved history cleanup where necessary. Avoid publishing raw
scanner output or copying the value into an issue. Report privately with the
minimum affected paths, commits and redacted reproduction needed to investigate.
Changing an allowlist or deleting the latest file is not credential revocation.
