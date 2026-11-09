# AI service

FastAPI foundation for the AI Knowledge Assistant portfolio demo. This stage provides process liveness and an honest capability status; ingestion, retrieval, chat, agents, and MCP are not implemented yet.

Requires Python **3.15.2** and uv **0.13.7**. From this directory:

```sh
uv sync --frozen
uv run uvicorn knowledge_assistant.app:app --reload --port 8000
```

- `GET /health`: process liveness, without external dependency checks.
- `GET /api/v1/status`: service version, implementation stage, and capabilities.
- `GET /docs`: generated OpenAPI documentation.

The default dashboard origin is `http://localhost:3000`. Export `CORS_ORIGINS` as a comma-separated list of exact origins for another environment, for example `https://dashboard.example.com,https://preview.example.com`. Paths, wildcard origins, and embedded credentials are rejected. CORS does not authenticate a client; authentication and tenant isolation are separate upcoming features. Export `APP_ENV` as `development`, `test`, or `production` (default: `development`). The service reads exported environment variables and does not automatically load `.env` files. No model keys are required or read at this stage.

Validate the package:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=knowledge_assistant --cov-report=term-missing
uv build
```
