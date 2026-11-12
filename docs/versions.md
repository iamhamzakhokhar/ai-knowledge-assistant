# Version choices

The setup code pins the following versions, verified against official
release pages or publisher-maintained package metadata on **November 9, 2026**.
These are the choices for this implementation, not a promise that they will
remain the newest releases. Application manifests and committed lockfiles are
the source of truth for installation.

## Runtimes and package managers

| Tool    | Pinned version | Official source                                                       |
| ------- | -------------- | --------------------------------------------------------------------- |
| Node.js | 26.13.0 LTS    | [Release notes](https://nodejs.org/en/blog/release/v26.13.0)          |
| Python  | 3.15.2         | [Release page](https://www.python.org/downloads/release/python-3152/) |
| pnpm    | 12.18.0        | [Release notes](https://github.com/pnpm/pnpm/releases/tag/v12.18.0)   |
| uv      | 0.13.7         | [Release notes](https://github.com/astral-sh/uv/releases/tag/0.13.7)  |

Node.js 26 is the selected LTS line. The Python package declares
`>=3.15,<3.16`, and the development runtime is pinned to 3.15.2. pnpm manages the
JavaScript workspace; uv manages the Python service and its development tools.

## Web, widget and JavaScript tooling

| Dependency                           | Pinned version | Official source                                                                       |
| ------------------------------------ | -------------- | ------------------------------------------------------------------------------------- |
| Next.js                              | 16.4.5         | [Release notes](https://github.com/vercel/next.js/releases/tag/v16.4.5)               |
| React and React DOM                  | 19.3.1         | [Release notes](https://github.com/react/react/releases/tag/v19.3.1)                  |
| TypeScript                           | 7.0.2          | [Release notes](https://github.com/microsoft/TypeScript/releases/tag/v7.0.2)          |
| Tailwind CSS and its PostCSS adapter | 4.3.5          | [Release notes](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.5)      |
| Biome                                | 2.6.6          | [Release notes](https://github.com/biomejs/biome/releases/tag/@biomejs%2Fbiome@2.6.6) |
| Playwright Test                      | 1.65.0         | [Release notes](https://github.com/microsoft/playwright/releases/tag/v1.65.0)         |

The widget compiles a single IIFE with TypeScript into one classic script and has no runtime
dependencies. The web foundation uses App Router, React and Tailwind; shadcn/ui
components have not been added. The type declarations are pinned in the manifests:
`@types/node` 26.6.6, `@types/react` 19.3.0 and `@types/react-dom` 19.3.0.

## Python service and development tools

| Dependency        | Pinned version | Published release metadata                                          |
| ----------------- | -------------- | ------------------------------------------------------------------- |
| FastAPI           | 0.162.2        | [PyPI release](https://pypi.org/project/fastapi/0.162.2/)           |
| pypdf             | 7.0.1          | [Release notes](https://github.com/py-pdf/pypdf/releases/tag/7.0.1) |
| Pydantic          | 2.14.3         | [PyPI release](https://pypi.org/project/pydantic/2.14.3/)           |
| pydantic-settings | 2.16.2         | [PyPI release](https://pypi.org/project/pydantic-settings/2.16.2/)  |
| Uvicorn           | 0.54.0         | [PyPI release](https://pypi.org/project/uvicorn/0.54.0/)            |
| httpx2            | 2.16.0         | [PyPI release](https://pypi.org/project/httpx2/2.16.0/)             |
| pytest            | 9.2.0          | [PyPI release](https://pypi.org/project/pytest/9.2.0/)              |
| pytest-cov        | 7.1.0          | [PyPI release](https://pypi.org/project/pytest-cov/7.1.0/)          |
| Ruff              | 0.17.4         | [PyPI release](https://pypi.org/project/ruff/0.17.4/)               |
| Hatchling         | 1.33.0         | [PyPI release](https://pypi.org/project/hatchling/1.33.0/)          |

FastAPI, Pydantic, settings and Uvicorn provide the HTTP service. The HTTP client
and pytest tools support tests; Ruff checks/formats Python. Hatchling builds the
service package, including its Alembic files. Version selection is separate from
measured compatibility: recorded check results establish which flows have passed.

pypdf 7.0.1 includes the fix for [GHSA-fhff-j4m8-439h](https://github.com/py-pdf/pypdf/security/advisories/GHSA-fhff-j4m8-439h).
The worker uses the bounded parser configuration described in
[document ingestion](document-ingestion.md). DOCX and Markdown parsing use
the Python standard library.

## Local database and queue infrastructure

| Dependency                | Selected version or image tag  | Official source                                                           |
| ------------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| PostgreSQL                | 18, through the pgvector image | [PostgreSQL 18 notes](https://www.postgresql.org/docs/18/release-18.html) |
| pgvector                  | `pgvector/pgvector:0.8.7-pg18` | [Release notes](https://github.com/pgvector/pgvector/releases/tag/v0.8.7) |
| Redis server              | `redis:8.10.3`                 | [Release notes](https://github.com/redis/redis/releases/tag/8.10.3)       |
| SQLAlchemy                | 2.1.8                          | [Publisher release](https://pypi.org/project/SQLAlchemy/2.1.8/)           |
| Alembic                   | 1.20.1                         | [Publisher release](https://pypi.org/project/alembic/1.20.1/)             |
| psycopg with binary extra | 3.3.8                          | [Publisher release](https://pypi.org/project/psycopg/3.3.8/)              |
| redis-py                  | 8.1.0                          | [Publisher release](https://pypi.org/project/redis/8.1.0/)                |
| RQ                        | 3.0.0                          | [Publisher release](https://pypi.org/project/rq/3.0.0/)                   |

The Python service uses SQLAlchemy Core with psycopg, Alembic migrations and
JSON RQ jobs. Redis is password-protected in Compose. The vector extension is
enabled; embeddings, vector indexes and knowledge retrieval remain future work.
The PostgreSQL image tag selects the 18 release line; its data volume is mounted
at `/var/lib/postgresql`. See [local infrastructure](infrastructure.md).

## Planned dependencies

OpenAI/Anthropic/Gemini SDKs, reranking, Langfuse, Stripe, MCP, evaluation tools
and additional UI/testing libraries are not installed as product capabilities.
Their versions will be verified against current official documentation and
release notes, tested together, then pinned when the owning feature is built.
No provider/model version or deployed infrastructure is implied by this roadmap.

## Updating versions

Before an upgrade, read the official release notes and migration guidance,
update manifests and lockfiles together, then run lint, type checking, tests and
builds. Record actual results and any deployment constraints. Do not replace a
pin with an unverified `latest` range or present an installation as an integration
test.

The production web build uses Next.js’s supported Webpack mode. Turbopack’s CSS subprocess requires local socket access unavailable in the initial restricted development environment; no application feature depends on a particular bundler.

## Security tooling

CodeQL Action **4.39.3** is pinned to commit
`33d7b85da1a04171e211befe2c739303b9a4c066`, using its linked CodeQL **2.27.4**
bundle for Actions, JavaScript/TypeScript and Python. See the
[official action release](https://github.com/github/codeql-action/releases/tag/v4.39.3).

CI installs [gitleaks **8.30.1**](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
and [actionlint **1.7.12**](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)
from reviewed Linux archives. Their expected SHA256 hashes are committed in
`scripts/install_security_tools.py` and checked before extraction/execution.
Tool updates must change the reviewed version and expected hash together.

Dependabot's configured ecosystem versions follow GitHub's service support;
version-update configuration is separate from dependency-graph/advisory support.
The pnpm 12 and image-update limitations are documented in [the security guide](security.md).
