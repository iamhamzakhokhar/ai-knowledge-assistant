"""Observe the running Compose services, including Redis authentication enforcement."""

import json
import os
from urllib.request import urlopen

import pytest
from redis import Redis
from redis.exceptions import AuthenticationError

from knowledge_assistant.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("AKA_INTEGRATION_TESTS") != "1",
    reason="Running Compose services require AKA_INTEGRATION_TESTS=1",
)


def test_api_process_and_dependency_readiness_are_healthy() -> None:
    base_url = os.environ.get("API_TEST_URL", "http://api:8000").rstrip("/")
    with urlopen(f"{base_url}/health", timeout=5) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}
    with urlopen(f"{base_url}/ready", timeout=5) as response:
        assert response.status == 200


def test_web_homepage_is_served() -> None:
    base_url = os.environ.get("WEB_TEST_URL", "http://web:3000").rstrip("/")
    with urlopen(base_url, timeout=10) as response:
        assert response.status == 200
        assert "KnowledgeDesk" in response.read().decode("utf-8")


def test_redis_rejects_connections_without_the_mounted_password(
    integration_settings: Settings,
) -> None:
    unauthenticated = Redis(
        host=integration_settings.redis_host,
        port=integration_settings.redis_port,
        protocol=2,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        with pytest.raises(AuthenticationError):
            unauthenticated.ping()
    finally:
        unauthenticated.close()
