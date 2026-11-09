# Contributing

Discuss one feature at a time in the issue tracker. For a vulnerability, use the
[private reporting process](SECURITY.md), not a public issue containing details.
Branch from `main`, use a descriptive feature branch, and make small Conventional
Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:` or `ci:`).

## Install tools first

Install Node from `.node-version`, Python from `.python-version`, pnpm from
`package.json`, uv **0.13.7**, and Gitleaks **8.30.1** before enabling the hooks.
Use the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)
and the matching platform archive from the
[Gitleaks release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1),
verifying its published checksum. Workflow checks also use actionlint **1.7.12**.

On Linux x86_64, the repository helper installs the reviewed, checksum-pinned
Gitleaks and actionlint archives into a local directory:

```sh
python3 scripts/install_security_tools.py --directory .local/security-tools
export PATH="$PWD/.local/security-tools:$PATH"
```

That helper supports Linux x86_64 CI runners only. On other platforms, install
the corresponding upstream releases; no global Git configuration or shell
profile edit is required by this project.

## Set up the workspace

```sh
pnpm install --frozen-lockfile
uv sync --directory services/ai --locked
pnpm setup:hooks
pnpm exec playwright install chromium
```

The project-local hooks require `gitleaks` on `PATH`. The pre-commit hook scans
the staged index with complete secret redaction, then checks lint/formatting. The
pre-push hook scans all available Git history with complete redaction.

Stage only the files intended for the change, then check the exact staged content:

```sh
gitleaks git --staged --redact=100 --no-banner .
```

## Validate the change

```sh
pnpm lint
pnpm typecheck
pnpm build
pnpm test
actionlint .github/workflows/*.yml
gitleaks git --redact=100 --no-banner --log-opts='--all --full-history --no-ext-diff' .
```

Use `pnpm test:api` or `pnpm test:browser` for a relevant individual suite.
Infrastructure changes also need the scoped Compose integration/recovery checks
described in [docs/infrastructure.md](docs/infrastructure.md). Run
`python3 scripts/check_secret_scanner.py` to exercise detection/redaction in an
isolated synthetic fixture; never use a real key as a scanner test.

Dependency changes require `pnpm audit --audit-level=high` for the workspace plus
review of the owning manifest and lockfile. Do not treat a clean audit as complete
dependency coverage. See [known dependency-graph and updater limits](docs/security.md).
Major updates require human review; do not enable automatic merging or force
unreviewed upgrades to clear an advisory.

Never bypass a secret finding. If the credential is real, revoke/rotate it,
remove it and coordinate approved history cleanup. Do not paste its value into
an issue, pull request, report or chat. `.env.example` contains variable names and
safe examples; real keys and personal data stay outside version control.

## Open a pull request

Describe the resulting behavior, link the issue with `Closes #NUMBER` when its
acceptance criteria are complete, and list the checks actually run and any
remaining limitations. Keep planned capabilities distinct from working features.
Do not invent evaluation, coverage, Lighthouse, customer or revenue numbers.

The intended stable checks are `Checks`, `Security`, `Dependency audit`,
`CodeQL (javascript-typescript)` and `CodeQL (python)`. Follow the actual required
contexts shown by GitHub once verified. Analysis-job success shows scan
completion; alert-based blocking is a separate native code-scanning policy.

Use repository-local identity/hook settings and your own legitimate contributor
identity. Agents must also follow [AGENTS.md](AGENTS.md). Never change global
Git/GitHub defaults for this project, backdate commits or fake contribution
activity. Preserve compatible licensing and attribution.
