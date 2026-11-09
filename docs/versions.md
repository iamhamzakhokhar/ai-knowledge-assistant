# Version choices

The setup foundation pins the following versions, verified against official
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

| Dependency        | Pinned version | Published release metadata                                         |
| ----------------- | -------------- | ------------------------------------------------------------------ |
| FastAPI           | 0.162.2        | [PyPI release](https://pypi.org/project/fastapi/0.162.2/)          |
| Pydantic          | 2.14.3         | [PyPI release](https://pypi.org/project/pydantic/2.14.3/)          |
| pydantic-settings | 2.16.2         | [PyPI release](https://pypi.org/project/pydantic-settings/2.16.2/) |
| Uvicorn           | 0.54.0         | [PyPI release](https://pypi.org/project/uvicorn/0.54.0/)           |
| httpx2            | 2.16.0         | [PyPI release](https://pypi.org/project/httpx2/2.16.0/)            |
| pytest            | 9.2.0          | [PyPI release](https://pypi.org/project/pytest/9.2.0/)             |
| pytest-cov        | 7.1.0          | [PyPI release](https://pypi.org/project/pytest-cov/7.1.0/)         |
| Ruff              | 0.17.4         | [PyPI release](https://pypi.org/project/ruff/0.17.4/)              |
| Hatchling         | 1.33.0         | [PyPI release](https://pypi.org/project/hatchling/1.33.0/)         |

FastAPI, Pydantic, settings and Uvicorn are the service dependencies. The HTTP
client and pytest tools support tests; Ruff checks/formats Python. Hatchling is
the build backend. Version selection is separate from measured compatibility:
the repository checks and their recorded results establish which flows have
actually passed.

## Planned dependencies

PostgreSQL/pgvector, Redis, a background-job library, database migrations,
OpenAI/Anthropic/Gemini SDKs, reranking, Langfuse, Stripe, MCP, evaluation tools and
additional UI/testing libraries are not installed as foundation capabilities.
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
