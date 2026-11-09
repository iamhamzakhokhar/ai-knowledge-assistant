# Coding-agent instructions

## Repository and identity

- This project is `iamhamzakhokhar/ai-knowledge-assistant` on GitHub. Scope every
  project GitHub command to that exact repository.
- Before a GitHub write, verify the authenticated actor is `iamhamzakhokhar` and
  the destination is this repository. If either differs, stop that write and
  report the mismatch; do not fall back to another available account.
- Use repository-local Git author and hook settings. Do not change global Git or
  GitHub defaults, global identities or unrelated account configuration.
- Follow the current user's authorization. Repository creation, pushes and
  GitHub setting changes require explicit approval; an existing approval for the
  same scoped work remains valid and does not need to be requested again.

## Current scope and architecture

- The current foundation is a Next.js marketing/dashboard shell, a FastAPI
  health/status service and a disconnected Web Component preview.
- Keep implemented behavior distinct from planned behavior. Do not fabricate
  model replies, connected workflows, customers, revenue, traffic, coverage,
  evaluation results or performance scores.
- Read the relevant package and `docs/architecture.md` before changing a boundary.
  Each team must remain isolated when tenancy is implemented, including jobs,
  retrieval, citations, files, caches, analytics and MCP tools.
- Future model-proposed writes require human approval of the exact payload,
  authorization at execution time, idempotency and an audit trail.

## Development workflow

- Work one issue at a time on a feature branch. Use small Conventional Commits
  and a pull request that explains resulting behavior, validation and
  `Closes #NUMBER` when the acceptance criteria are complete.
- Check official documentation and release notes before adding or updating a
  framework, API or tool. Pin versions and update committed lockfiles together.
- Keep the widget dependency-free at runtime and independently embeddable. Treat
  configured greetings and retrieved source material as untrusted text.
- Keep code readable and explainable. Add focused tests for important behavior
  and security boundaries; avoid implementation-mirroring tests.

## Validation and secrets

- Use `pnpm lint`, `pnpm typecheck`, `pnpm build` and the relevant test commands.
  `pnpm test` runs API and browser checks. Browser tests require
  `pnpm exec playwright install chromium` first.
- Report which checks actually ran and passed. Do not claim unrun integrations,
  live-model behavior, accessibility conformance or deployment verification.
- Run gitleaks before every push. Never bypass a secret finding or commit keys,
  tokens, credentials, private account notes, local access paths or real user data.
- Keep provider keys server-side. `.env.example` contains names and safe example
  values only; `NEXT_PUBLIC_*` values are public browser configuration.
- Never backdate commits, fake contribution activity, or copy incompatible
  licensed code. Preserve attribution and the MIT license.
