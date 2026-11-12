"""Health, session, and tenant-scoped workspace routes."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from knowledge_assistant import __version__
from knowledge_assistant.auth_routes import router as auth_router
from knowledge_assistant.config import Settings
from knowledge_assistant.document_routes import router as documents_router
from knowledge_assistant.readiness import check_dependencies
from knowledge_assistant.team_routes import router as team_router
from knowledge_assistant.workspace_middleware import WorkspaceRequestMiddleware
from knowledge_assistant.workspace_store import WorkspaceStore


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class Capabilities(BaseModel):
    health: bool = True
    authentication: bool = True
    documents: bool = True
    ingestion: bool = False
    retrieval: bool = False
    chat: bool = False
    agents: bool = False
    mcp: bool = False


class ServiceStatus(BaseModel):
    service: Literal["ai-knowledge-assistant"] = "ai-knowledge-assistant"
    version: str = __version__
    stage: Literal["core"] = "core"
    capabilities: Capabilities


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app with explicit configuration so tests need no network or keys."""
    config = settings if settings is not None else Settings()
    store = WorkspaceStore(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            store.close()

    application = FastAPI(title="AI Knowledge Assistant", version=__version__, lifespan=lifespan)
    application.state.workspace_settings = config
    application.state.workspace_store = store
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )
    application.add_middleware(WorkspaceRequestMiddleware, allowed_origins=config.allowed_origins)
    application.include_router(auth_router)
    application.include_router(team_router)
    application.include_router(documents_router)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation payload can echo passwords and invite tokens.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Process liveness only; this does not assert external dependency readiness."""
        return HealthResponse()

    @application.get("/api/v1/status", response_model=ServiceStatus, tags=["operations"])
    def status() -> ServiceStatus:
        """Expose the current implementation stage without configuration or secrets."""
        return ServiceStatus(capabilities=Capabilities())

    @application.get("/ready", tags=["operations"], response_model=None)
    def ready() -> JSONResponse:
        """Readiness requires authenticated database and Redis connections."""
        available = check_dependencies(config)
        return JSONResponse(
            {"status": "ready" if available else "unavailable"},
            status_code=200 if available else 503,
        )

    return application


app = create_app()
