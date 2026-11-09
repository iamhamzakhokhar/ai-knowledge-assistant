"""HTTP behavior and the origin boundary of the initial service."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from knowledge_assistant import __version__
from knowledge_assistant.app import create_app
from knowledge_assistant.config import Settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(allowed_origins=("https://dashboard.example",))))


def test_health_is_liveness_only(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("available, expected_status", [(False, 503), (True, 200)])
def test_readiness_has_a_generic_dependency_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, available: bool, expected_status: int
) -> None:
    monkeypatch.setattr("knowledge_assistant.app.check_dependencies", lambda _settings: available)
    response = client.get("/ready")
    assert response.status_code == expected_status
    assert response.json() == {"status": "ready" if available else "unavailable"}


def test_status_reports_only_implemented_capabilities(client: TestClient) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {
        "service": "ai-knowledge-assistant",
        "version": __version__,
        "stage": "setup",
        "capabilities": {
            "health": True,
            "ingestion": False,
            "retrieval": False,
            "chat": False,
            "agents": False,
            "mcp": False,
        },
    }


def test_allowed_origin_receives_cors_header_without_credentials(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://dashboard.example"})
    assert response.headers["access-control-allow-origin"] == "https://dashboard.example"
    assert "access-control-allow-credentials" not in response.headers


def test_unlisted_origin_does_not_receive_cors_permission(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "https://dashboard.example.attacker.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example.com",
        "ftp://example.com",
        "https://example.com/path",
        "https://example.com/",
        "https://user:password@example.com",
        "https://example.com?api_key=secret",
        "https://example.com#fragment",
        "https://example.com:invalid",
        "https://example.com:0",
    ],
)
def test_invalid_origin_configuration_fails_closed(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(allowed_origins=(origin,))


def test_allowed_origins_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com, https://example.com")
    assert Settings().allowed_origins == ("https://example.com",)
