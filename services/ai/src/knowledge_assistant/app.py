"""The service foundation. AI features are added in subsequent feature issues."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from knowledge_assistant import __version__
from knowledge_assistant.config import Settings


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class Capabilities(BaseModel):
    health: bool = True
    ingestion: bool = False
    retrieval: bool = False
    chat: bool = False
    agents: bool = False
    mcp: bool = False


class ServiceStatus(BaseModel):
    service: Literal["ai-knowledge-assistant"] = "ai-knowledge-assistant"
    version: str = __version__
    stage: Literal["setup"] = "setup"
    capabilities: Capabilities


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app with explicit configuration so tests need no network or keys."""
    config = settings if settings is not None else Settings()
    application = FastAPI(title="AI Knowledge Assistant", version=__version__)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Process liveness only; this does not assert external dependency readiness."""
        return HealthResponse()

    @application.get("/api/v1/status", response_model=ServiceStatus, tags=["operations"])
    def status() -> ServiceStatus:
        """Expose the current implementation stage without configuration or secrets."""
        return ServiceStatus(capabilities=Capabilities())

    return application


app = create_app()
