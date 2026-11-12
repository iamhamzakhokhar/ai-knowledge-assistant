"""Real tenant storage and API around a controlled crawler transport."""

import os
from collections.abc import Iterator
from contextlib import ExitStack
from ipaddress import IPv6Address
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledge_assistant import website_jobs, website_routes
from knowledge_assistant.app import create_app
from knowledge_assistant.config import Settings
from knowledge_assistant.migrate import run_migrations
from knowledge_assistant.website_crawler import HTTPResult, crawl_website

pytestmark = pytest.mark.integration
ORIGIN = "https://dashboard.knowledge.example"
SITE = "https://fixture.example"
SITEMAP = f"{SITE}/sitemap.xml"
XMLNS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@pytest.fixture(scope="module")
def website_settings(integration_settings: Settings, auth_disposable_database: str) -> Settings:
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


@pytest.fixture
def website_harness(website_settings: Settings, monkeypatch) -> Iterator[tuple]:
    deliveries = []

    def dispatch(_settings, team_id, website_id, attempt):
        deliveries.append((str(team_id), str(website_id), attempt))

    monkeypatch.setattr(website_routes, "enqueue_website", dispatch)
    with ExitStack() as stack:

        def client():
            address = IPv6Address(int(IPv6Address("2001:db8::")) | (uuid4().int % 2**96))
            return stack.enter_context(
                TestClient(
                    create_app(website_settings),
                    base_url="https://api.knowledge.example",
                    client=(str(address), 50000),
                )
            )

        yield client, deliveries


def _post(client: TestClient, path: str, body: dict):
    return client.post(path, json=body, headers={"Origin": ORIGIN})


def _signup(client: TestClient, team: str | None = None):
    account = {
        "email": f"site-{uuid4().hex}@knowledge.example",
        "password": f"Fixture {uuid4().hex}",
    }
    if team is not None:
        account["team_name"] = team
    response = _post(client, "/api/v1/auth/signup", account)
    assert response.status_code == 201
    return response.json()


def _fixture_crawl(client, resource, requested, title="Fixture article", *, fail=False):
    def response(content, mime="text/html", status=200):
        return HTTPResult(status, {"content-type": mime}, content.encode())

    responses = {
        "/robots.txt": response("User-agent: *\nDisallow: /blocked\n", "text/plain"),
        "/sitemap.xml": response(
            f"<sitemapindex {XMLNS}><sitemap><loc>{SITE}/pages.xml</loc></sitemap></sitemapindex>",
            "application/xml",
        ),
        "/pages.xml": response(
            f"<urlset {XMLNS}>"
            + "".join(
                f"<url><loc>{SITE}{path}</loc></url>"
                for path in ("/jump", "/jump#duplicate", "/missing", "/blocked")
            )
            + "</urlset>",
            "application/xml",
        ),
        "/jump": HTTPResult(302, {"location": "/article"}, b""),
        "/article": response(f"<title>{title}</title><main><p>Public fixture text.</p></main>")
        if not fail
        else response("Unavailable", status=503),
        "/missing": response("Missing", status=404),
    }

    def transport(url, address, _deadline, _maximum_bytes):
        assert str(address) == "93.184.216.34"
        requested.append(url.path)
        return responses[url.path]

    def run(url, *, sitemap=False, on_result=None):
        def observe(page):
            on_result(page)
            current = client.get(resource).json()
            assert current["status"] == "processing"
            assert current["processed"] >= 1

        return crawl_website(
            url,
            sitemap=sitemap,
            on_result=observe,
            resolver=lambda *_args: ["93.184.216.34"],
            transport=transport,
        )

    return run


def test_sitemap_progress_provenance_and_atomic_recrawl(
    website_harness, website_settings, monkeypatch
):
    make_client, deliveries = website_harness
    owner = make_client()
    team = _signup(owner, "Website fixture")["teams"][0]["id"]
    collection = f"/api/v1/teams/{team}/websites"
    submitted = _post(owner, collection, {"url": SITEMAP, "sitemap": True})
    assert submitted.status_code == 201
    summary = submitted.json()
    assert (summary["status"], summary["attempt"], summary["processed"]) == ("queued", 1, 0)
    resource = f"{collection}/{summary['id']}"
    assert owner.get(resource).json()["pages"] == []
    duplicate = _post(owner, collection, {"url": SITEMAP + "#same", "sitemap": True})
    assert duplicate.status_code == 200 and duplicate.json()["id"] == summary["id"]
    assert len(deliveries) == 1
    assert _post(owner, f"{resource}/recrawl", {}).status_code == 409

    requested = []
    monkeypatch.setattr(website_jobs, "crawl_website", _fixture_crawl(owner, resource, requested))
    website_jobs.process_website(*deliveries[0], settings=website_settings)
    first = owner.get(resource).json()
    assert first["status"] == "completed" and first["published_attempt"] == 1
    pages = {page["url"]: page for page in first["pages"]}
    assert pages[f"{SITE}/jump"]["final_url"] == f"{SITE}/article"
    assert pages[f"{SITE}/jump"]["title"] == "Fixture article"
    assert "Public fixture text." in pages[f"{SITE}/jump"]["text"]
    assert pages[f"{SITE}/missing"]["status"] == "failed"
    assert pages[f"{SITE}/blocked"]["status"] == "skipped"
    assert requested.count("/jump") == 1 and "/blocked" not in requested

    recrawl = _post(owner, f"{resource}/recrawl", {})
    assert recrawl.status_code == 200 and recrawl.json()["attempt"] == 2
    assert owner.get(resource).json()["pages"] == first["pages"]
    website_jobs.process_website(*deliveries[0], settings=website_settings)
    assert owner.get(resource).json()["status"] == "queued"
    monkeypatch.setattr(
        website_jobs, "crawl_website", _fixture_crawl(owner, resource, [], "New title")
    )
    website_jobs.process_website(*deliveries[1], settings=website_settings)
    second = owner.get(resource).json()
    assert second["published_attempt"] == 2
    assert any(page["title"] == "New title" for page in second["pages"])

    assert _post(owner, f"{resource}/recrawl", {}).json()["attempt"] == 3
    monkeypatch.setattr(
        website_jobs, "crawl_website", _fixture_crawl(owner, resource, [], fail=True)
    )
    website_jobs.process_website(*deliveries[2], settings=website_settings)
    failed = owner.get(resource).json()
    assert failed["status"] == "failed" and failed["published_attempt"] == 2
    assert failed["pages"] == second["pages"]
    assert failed["last_attempt_pages"]
    assert all(page["text"] is None for page in failed["last_attempt_pages"])
    assert _post(owner, f"{resource}/recrawl", {}).status_code == 409


def test_website_reads_and_writes_require_team_roles(website_harness):
    make_client, deliveries = website_harness
    owner, other, member, guest = (make_client() for _ in range(4))
    team = _signup(owner, "First site team")["teams"][0]["id"]
    outside = _signup(other, "Second site team")["teams"][0]["id"]
    account = _signup(member)["user"]
    prefix = f"/api/v1/teams/{team}"
    invite = _post(owner, f"{prefix}/invites", {"email": account["email"], "role": "member"})
    assert invite.status_code == 201
    assert _post(member, "/api/v1/teams/join", {"token": invite.json()["token"]}).status_code == 200
    collection = f"{prefix}/websites"
    data = {"url": SITE}
    assert guest.get(collection).status_code == 401
    assert owner.post(collection, json=data).status_code == 403
    assert _post(owner, collection, {"url": "http://127.0.0.1/"}).status_code == 422
    assert _post(member, collection, data).status_code == 403
    assert _post(other, collection, data).status_code == 404
    own = _post(owner, collection, data)
    independent = _post(other, f"/api/v1/teams/{outside}/websites", data)
    assert own.status_code == independent.status_code == 201
    assert own.json()["id"] != independent.json()["id"] and len(deliveries) == 2
    resource = f"{collection}/{own.json()['id']}"
    assert member.get(resource).status_code == 200
    assert _post(member, f"{resource}/recrawl", {}).status_code == 403
    assert other.get(resource).status_code == 404
    assert other.get(f"/api/v1/teams/{outside}/websites/{own.json()['id']}").status_code == 404
