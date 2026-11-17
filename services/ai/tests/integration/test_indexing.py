"""Exercise version publication and citation visibility against real PostgreSQL."""

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from knowledge_assistant import (
    document_jobs,
    document_routes,
    indexing_jobs,
    website_jobs,
    website_routes,
)
from knowledge_assistant.app import create_app
from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.embeddings import EmbeddingConfig, embedding_config
from knowledge_assistant.indexing_store import active_chunk_statement
from knowledge_assistant.migrate import run_migrations

pytestmark = pytest.mark.integration
ORIGIN = "https://index.knowledge.example"
API = "https://api.knowledge.example"


@pytest.fixture(scope="module")
def index_settings(integration_settings: Settings, auth_disposable_database: str) -> Settings:
    assert auth_disposable_database != integration_settings.database_name
    settings = integration_settings.model_copy(
        update={
            "database_name": auth_disposable_database,
            "app_env": "test",
            "allowed_origins": (ORIGIN,),
        }
    )
    owner = settings.model_copy(
        update={
            "database_user": "aka_owner",
            "database_password_file": Path(os.environ["ADMIN_DATABASE_PASSWORD_FILE"]),
        }
    )
    run_migrations(owner, "upgrade", "head", app_password_file=settings.database_password_file)
    return settings


def _team(client: TestClient) -> UUID:
    payload = {
        "email": f"index-{uuid4().hex}@knowledge.example",
        "password": f"Fixture {uuid4().hex}",
        "team_name": "Index fixture",
    }
    result = client.post("/api/v1/auth/signup", json=payload, headers={"Origin": ORIGIN})
    assert result.status_code == 201
    return UUID(result.json()["teams"][0]["id"])


def _upload(client: TestClient, path: str, content: bytes):
    return client.post(
        path,
        content=content,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/octet-stream",
            "X-Document-Name": "Guide.md",
        },
    )


def test_active_index_reuses_versions_and_rejects_stale_citations(index_settings, monkeypatch):
    extraction, indexing = [], []

    def queue_document(_settings, team, document, attempt, revision=1):
        extraction.append((str(team), str(document), attempt, revision))

    def queue_index(_settings, team, kind, source, attempt):
        indexing.append((str(team), kind, str(source), attempt))

    monkeypatch.setattr(document_routes, "enqueue_document", queue_document)
    monkeypatch.setattr(indexing_jobs, "enqueue_index", queue_index)
    engine = create_engine_from_settings(index_settings)
    try:
        with (
            TestClient(create_app(index_settings), base_url=API) as client,
            TestClient(create_app(index_settings), base_url=API) as other,
        ):
            team, outside = _team(client), _team(other)
            documents = f"/api/v1/teams/{team}/documents"
            posted = _upload(client, documents, b"# Hours\n\nThe original guide opens at nine.")
            assert posted.status_code == 201
            source = posted.json()["id"]
            detail = f"/api/v1/teams/{team}/index/document/{source}"

            def active():
                with tenant_connection(engine, team) as connection:
                    query = active_chunk_statement(team, embedding_config(index_settings))
                    return connection.execute(query).mappings().all()

            def reindex():
                return client.post(detail + "/reindex", json={}, headers={"Origin": ORIGIN})

            def citation(chunk):
                return f"{detail}/versions/{chunk['version_id']}/anchors/{chunk['anchor']}"

            document_jobs.process_document(*extraction.pop(), settings=index_settings)
            first_delivery = indexing.pop()
            assert active() == []
            indexing_jobs.process_index(*first_delivery, settings=index_settings)
            first = active()
            assert first and "opens at nine" in first[0]["text"]
            assert first[0]["heading"] == "Hours"
            assert client.get(citation(first[0])).status_code == 200

            indexing_jobs.process_index(*first_delivery, settings=index_settings)
            assert 200 <= reindex().status_code < 300
            if indexing:
                indexing_jobs.process_index(*indexing.pop(), settings=index_settings)
            assert active() == first

            replacement = _upload(
                client, f"{documents}/{source}/replace", b"# Hours\n\nThe guide now opens at ten."
            )
            assert 200 <= replacement.status_code < 300
            assert replacement.json()["id"] == source
            document_jobs.process_document(*extraction.pop(), settings=index_settings)
            assert active() == first
            with monkeypatch.context() as bad:
                bad.setattr(indexing_jobs, "embed_texts", lambda texts, *_: [[1.0]] * len(texts))
                indexing_jobs.process_index(*indexing.pop(), settings=index_settings)
            assert client.get(detail).json()["error"] == "embedding_dimension_mismatch"
            assert active() == first

            assert 200 <= reindex().status_code < 300
            indexing_jobs.process_index(*indexing.pop(), settings=index_settings)
            current = active()
            assert current and "opens at ten" in current[0]["text"]
            assert current[0]["version_id"] != first[0]["version_id"]
            assert client.get(citation(first[0])).status_code == 404
            assert client.get(citation(current[0])).status_code == 200
            assert other.get(citation(current[0])).status_code == 404
            with tenant_connection(engine, outside) as connection:
                assert (
                    connection.execute(
                        active_chunk_statement(outside, embedding_config(index_settings))
                    ).first()
                    is None
                )
            with tenant_connection(engine, team) as connection:
                incompatible = EmbeddingConfig("openai", "text-embedding-3-small", 128)
                assert (
                    connection.execute(active_chunk_statement(team, incompatible)).first() is None
                )

            removed = client.request("DELETE", detail, json={}, headers={"Origin": ORIGIN})
            assert 200 <= removed.status_code < 300
            assert active() == []
            assert client.get(citation(current[0])).status_code == 404
            assert reindex().status_code == 409
    finally:
        engine.dispose()


def test_website_snapshot_indexes_only_fetched_text_with_url_provenance(
    index_settings, monkeypatch
):
    crawl, indexing = [], []
    monkeypatch.setattr(
        website_routes,
        "enqueue_website",
        lambda _, team, source, attempt: crawl.append((str(team), str(source), attempt)),
    )
    monkeypatch.setattr(
        indexing_jobs,
        "enqueue_index",
        lambda _, team, kind, source, attempt: indexing.append(
            (str(team), kind, str(source), attempt)
        ),
    )
    url = "https://fixture.example/guide"
    final_url = "https://fixture.example/guide-final"

    def fixture(*_args, on_result, **_kwargs):
        records = [
            {
                "url": url,
                "final_url": final_url,
                "title": "Public guide",
                "status": "fetched",
                "reason": None,
                "text": "Use the service entrance for the fixture.",
            },
            {
                "url": "https://fixture.example/unavailable",
                "final_url": None,
                "title": None,
                "status": "failed",
                "reason": "http_error",
                "text": None,
            },
        ]
        for record in records:
            on_result(record)
        return records

    monkeypatch.setattr(website_jobs, "crawl_website", fixture)
    engine = create_engine_from_settings(index_settings)
    try:
        with TestClient(create_app(index_settings), base_url=API) as client:
            team = _team(client)
            root = f"/api/v1/teams/{team}"
            website = client.post(f"{root}/websites", json={"url": url}, headers={"Origin": ORIGIN})
            assert website.status_code == 201
            website_jobs.process_website(*crawl.pop(), settings=index_settings)
            indexing_jobs.process_index(*indexing.pop(), settings=index_settings)
            with tenant_connection(engine, team) as connection:
                query = active_chunk_statement(team, embedding_config(index_settings))
                chunk = connection.execute(query).mappings().one()
            assert chunk["url"] == final_url and chunk["title"] == "Public guide"
            assert "service entrance" in chunk["text"] and chunk["page"] is None
    finally:
        engine.dispose()
