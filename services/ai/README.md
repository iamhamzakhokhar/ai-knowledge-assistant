# AI service

FastAPI service for the AI Knowledge Assistant portfolio demo. It provides
accounts, team roles, tenant-scoped PostgreSQL access, and a private JSON RQ
worker that extracts text from PDF, DOCX, and Markdown. Website ingestion,
retrieval, chat, agents, and MCP are planned.

Requires Python **3.15.2** and uv **0.13.7**. From this directory:

```sh
uv sync --frozen
uv run uvicorn knowledge_assistant.app:app --reload --port 8000 --no-proxy-headers
```

- `GET /health`: process liveness, without external dependency checks.
- `GET /ready`: PostgreSQL and Redis readiness; returns a generic 503 until both are configured and reachable.
- `GET /api/v1/status`: service version, implementation stage, and capabilities.
- `GET /docs`: generated OpenAPI documentation.

The default dashboard origin is `http://localhost:3000`. Export `CORS_ORIGINS`
as a comma-separated list of exact origins. A configured origin does not
authenticate a client: the service verifies the session and current membership
before using a tenant context. Export `APP_ENV` as `development`, `test`, or
`production` (default: `development`). The service reads exported variables and
does not load `.env` files. No model keys are required.

See [accounts and teams](../../docs/authentication.md) and
[document ingestion](../../docs/document-ingestion.md) for the API boundary.

Validate the package:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=knowledge_assistant --cov-report=term-missing
uv build
```

For the complete local stack, secret-file settings, migration and worker commands, and recovery procedures, follow [the infrastructure guide](../../docs/infrastructure.md). Runtime application containers receive only the non-owner database and Redis credentials. Owner-only migrations use a separate credential mount.
