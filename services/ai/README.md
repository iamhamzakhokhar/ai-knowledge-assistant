# AI service

FastAPI foundation for the AI Knowledge Assistant portfolio demo. This stage provides liveness, authenticated dependency readiness, tenant-scoped PostgreSQL access, Alembic migrations, and a private JSON RQ worker. Ingestion, retrieval, chat, agents, and MCP are not implemented yet.

Requires Python **3.15.2** and uv **0.13.7**. From this directory:

```sh
uv sync --frozen
uv run uvicorn knowledge_assistant.app:app --reload --port 8000
```

- `GET /health`: process liveness, without external dependency checks.
- `GET /ready`: PostgreSQL and Redis readiness; returns a generic 503 until both are configured and reachable.
- `GET /api/v1/status`: service version, implementation stage, and capabilities.
- `GET /docs`: generated OpenAPI documentation.

The default dashboard origin is `http://localhost:3000`. Export `CORS_ORIGINS` as a comma-separated list of exact origins for another environment, for example `https://dashboard.example.com,https://preview.example.com`. Paths, wildcard origins, and embedded credentials are rejected. CORS does not authenticate a client; user authentication remains an upcoming feature. Database RLS accepts only a trusted internal tenant context. Export `APP_ENV` as `development`, `test`, or `production` (default: `development`). The service reads exported environment variables and does not automatically load `.env` files. No model keys are required or read at this stage.

Validate the package:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=knowledge_assistant --cov-report=term-missing
uv build
```

For the complete local stack, secret-file settings, migration and worker commands, and recovery procedures, follow [the infrastructure guide](../../docs/infrastructure.md). Runtime application containers receive only the non-owner database and Redis credentials. Owner-only migrations use a separate credential mount.
