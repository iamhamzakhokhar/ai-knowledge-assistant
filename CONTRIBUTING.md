# Contributing

Use the issue tracker to discuss one feature at a time. Branch from `main`, use a descriptive feature branch, and make small Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:` or `ci:`).

Install Node from `.node-version`, Python from `.python-version`, pnpm from `package.json`, [uv](https://docs.astral.sh/uv/getting-started/installation/), and [gitleaks](https://github.com/gitleaks/gitleaks#installing).

```sh
pnpm install --frozen-lockfile
uv sync --directory services/ai --locked
pnpm setup:hooks
pnpm exec playwright install chromium
pnpm lint
pnpm typecheck
pnpm build
pnpm test
```

The pre-commit hook runs lint and formatting checks. The pre-push hook requires gitleaks and scans Git history with redacted output. Never bypass a secret finding: remove the secret and rotate it if it was exposed. Use `.env.example` for variable names; real keys stay outside version control.

Every pull request should describe the resulting behavior, link its issue using `Closes #NUMBER`, and list the checks actually run. Keep planned capabilities distinct from working features. Do not invent evaluation, coverage, Lighthouse, customer, or revenue numbers.

Repository identity is a local Git setting. Use your own identity when contributing; never change global Git/GitHub settings for this project.
