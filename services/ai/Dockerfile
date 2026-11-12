FROM python:3.15.2-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY services/ai/pyproject.toml services/ai/uv.lock services/ai/README.md ./
COPY services/ai/src ./src
COPY services/ai/alembic.ini ./alembic.ini
COPY services/ai/migrations ./migrations
RUN uv sync --locked --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"
RUN groupadd --gid 10001 application && useradd --uid 10001 --gid 10001 --no-create-home application

FROM base AS integration
RUN uv sync --locked --no-editable
COPY services/ai/tests ./tests
RUN mkdir /test-run && chown 10001:10001 /test-run
WORKDIR /test-run
USER 10001:10001
CMD ["pytest", "/app/tests", "-o", "cache_dir=/test-run/pytest-cache", "--cov=knowledge_assistant", "--cov-config=/app/pyproject.toml", "--cov-report=term-missing", "--cov-fail-under=85"]

FROM base AS runtime
USER 10001:10001
CMD ["uvicorn", "knowledge_assistant.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
