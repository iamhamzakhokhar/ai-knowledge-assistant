"""Document API and executor against a uniquely created PostgreSQL database."""

import os
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from io import BytesIO
from ipaddress import IPv6Address
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from knowledge_assistant import document_jobs, document_routes
from knowledge_assistant.app import create_app
from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.migrate import run_migrations
from knowledge_assistant.models import document_originals, documents
from knowledge_assistant.workspace_store import set_context

pytestmark = pytest.mark.integration
ORIGIN = "https://documents.knowledge.example"
TEAMS = "/api/v1/teams"


def _owner_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "database_user": "aka_owner",
            "database_password_file": Path(os.environ["ADMIN_DATABASE_PASSWORD_FILE"]),
        }
    )


@pytest.fixture(scope="module")
def document_settings(integration_settings: Settings, auth_disposable_database: str) -> Settings:
    assert auth_disposable_database != integration_settings.database_name
    settings = integration_settings.model_copy(
        update={
            "database_name": auth_disposable_database,
            "app_env": "test",
            "allowed_origins": (ORIGIN,),
        }
    )
    run_migrations(
        _owner_settings(settings),
        "upgrade",
        "head",
        app_password_file=settings.database_password_file,
    )
    return settings


@pytest.fixture
def document_owner_engine(document_settings: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(_owner_settings(document_settings))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def deliveries(monkeypatch) -> list[tuple[UUID, UUID, int]]:
    # The executor below consumes these exact arguments. The browser test uses
    # the actual Redis broker and Compose worker without this substitution.
    queued: list[tuple[UUID, UUID, int]] = []

    def capture(_settings: Settings, team_id: UUID, document_id: UUID, attempt: int) -> None:
        queued.append((team_id, document_id, attempt))

    monkeypatch.setattr(document_routes, "enqueue_document", capture)
    return queued


@pytest.fixture
def client_factory(document_settings: Settings, deliveries):
    with ExitStack() as stack:

        def create() -> TestClient:
            address = IPv6Address(int(IPv6Address("2001:db8::")) | (uuid4().int % 2**96))
            return stack.enter_context(
                TestClient(
                    create_app(document_settings),
                    base_url="https://api.knowledge.example",
                    client=(str(address), 50000),
                )
            )

        yield create


def _signup(client: TestClient, team_name: str | None = None) -> dict:
    email = f"documents-{uuid4().hex}@knowledge.example"
    payload = {"email": email, "password": f"Fixture password {uuid4().hex}"}
    if team_name is not None:
        payload["team_name"] = team_name
    response = client.post("/api/v1/auth/signup", json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 201
    return response.json()


def _join_team(owner: TestClient, member: TestClient, team_id: str, role: str) -> str:
    account = _signup(member)["user"]
    invitation = owner.post(
        f"{TEAMS}/{team_id}/invites",
        headers={"Origin": ORIGIN},
        json={"email": account["email"], "role": role},
    )
    assert invitation.status_code == 201
    response = member.post(
        f"{TEAMS}/join", json={"token": invitation.json()["token"]}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200
    return account["id"]


def _upload(
    client: TestClient, collection: str, name: str, content: bytes, *, origin: str = ORIGIN
):
    return client.post(
        collection,
        content=content,
        headers={
            "Origin": origin,
            "Content-Type": "application/octet-stream",
            "X-Document-Name": quote(name, safe=""),
        },
    )


def _retry(client: TestClient, resource: str):
    return client.post(f"{resource}/retry", json={}, headers={"Origin": ORIGIN})


def _deliver(settings: Settings, delivery: tuple[UUID, UUID, int]) -> None:
    team_id, document_id, attempt = delivery
    document_jobs.process_document(str(team_id), str(document_id), attempt, settings=settings)


def _pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        *(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents " + ref + b" 0 R >>"
            for ref in (b"6", b"7")
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for phrase in (b"First fixture page", b"Second fixture page"):
        stream = b"BT /F1 12 Tf 72 700 Td (" + phrase + b") Tj ET\n"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream")
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + content + b"\nendobj\n")
    xref = len(data)
    count = len(objects) + 1
    data.extend(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def _docx() -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            "<w:r><w:t>Fixture heading</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Fixture paragraph</w:t></w:r></w:p></w:body></w:document>",
        )
    return data.getvalue()


@pytest.mark.parametrize(
    ("name", "payload", "format_name", "text", "pages", "heading"),
    [
        ("Manual.pdf", _pdf(), "pdf", "Second fixture page", {1, 2}, None),
        ("Manual.docx", _docx(), "docx", "Fixture paragraph", {None}, "Fixture heading"),
        (
            "Manual.md",
            b"# Fixture title\n\nMarkdown body.\n",
            "markdown",
            "Markdown body.",
            {None},
            "Fixture title",
        ),
    ],
)
def test_document_extraction_publishes_metadata_once(
    client_factory, document_settings, deliveries, name, payload, format_name, text, pages, heading
) -> None:
    client = client_factory()
    team_id = _signup(client, "Document fixture team")["teams"][0]["id"]
    collection = f"{TEAMS}/{team_id}/documents"
    response = _upload(client, collection, name, payload)
    assert response.status_code == 201
    summary = response.json()
    assert (summary["format"], summary["status"], summary["attempt"]) == (format_name, "queued", 1)
    resource = f"{collection}/{summary['id']}"
    assert client.get(resource).json()["sections"] == []
    assert len(deliveries) == 1

    _deliver(document_settings, deliveries[0])
    detail = client.get(resource).json()
    assert detail["status"] == "completed" and detail["error"] is None
    assert text in "\n".join(section["text"] for section in detail["sections"])
    assert {section["page"] for section in detail["sections"]} == pages
    if heading is not None:
        assert heading in {section["heading"] for section in detail["sections"]}
    _deliver(document_settings, deliveries[0])
    duplicate = _upload(client, collection, name, payload)
    assert duplicate.status_code == 200 and duplicate.json()["id"] == summary["id"]
    assert len(deliveries) == 1
    assert client.get(resource).json() == detail
    assert client.get(collection).json()["documents"] == [duplicate.json()]


def test_document_validation_and_tenant_boundaries(client_factory, document_settings) -> None:
    owner, other, member, signed_out = (client_factory() for _ in range(4))
    owner_session = _signup(owner, "First document team")
    team = owner_session["teams"][0]["id"]
    outside = _signup(other, "Second document team")["teams"][0]["id"]
    _join_team(owner, member, team, "member")
    collection = f"{TEAMS}/{team}/documents"
    other_collection = f"{TEAMS}/{outside}/documents"
    data = b"# Private fixture\n\nTeam-only text."
    assert signed_out.get(collection).status_code == 401
    assert (
        _upload(
            owner, collection, "sample.md", data, origin="https://elsewhere.example"
        ).status_code
        == 403
    )
    assert _upload(owner, collection, "sample.txt", data).status_code == 415
    assert _upload(owner, collection, "../sample.md", data).status_code == 422
    assert _upload(owner, collection, "large.md", b"x" * (5 * 1024**2 + 1)).status_code == 413
    assert _upload(member, collection, "sample.md", data).status_code == 403

    first = _upload(owner, collection, "sample.md", data)
    second = _upload(other, other_collection, "sample.md", data)
    assert first.status_code == second.status_code == 201
    document_id = first.json()["id"]
    assert document_id != second.json()["id"]
    resource = f"{collection}/{document_id}"
    assert member.get(resource).status_code == 200
    assert _retry(member, resource).status_code == 403
    assert other.get(resource).status_code == 404
    assert other.get(f"{other_collection}/{document_id}").status_code == 404
    assert _retry(other, resource).status_code == 404
    assert _upload(other, collection, "sample.md", data).status_code == 404

    engine = create_engine_from_settings(document_settings)
    try:
        original = select(document_originals.c.id).where(
            document_originals.c.id == UUID(document_id)
        )
        with tenant_connection(engine, UUID(team)) as connection:
            assert connection.scalar(original) == UUID(document_id)
            set_context(connection, "app.user_id", owner_session["user"]["id"])
            assert connection.scalar(original) is None
        with tenant_connection(engine, UUID(outside)) as connection:
            assert connection.scalar(original) is None
        with engine.connect() as connection:
            assert connection.scalars(select(document_originals.c.id)).all() == []
    finally:
        engine.dispose()


def test_interrupted_attempt_cannot_publish_after_retry(
    client_factory, document_settings, document_owner_engine, deliveries, monkeypatch
) -> None:
    client = client_factory()
    team_id = _signup(client, "Interruption fixture")["teams"][0]["id"]
    collection = f"{TEAMS}/{team_id}/documents"
    uploaded = _upload(client, collection, "retry.md", b"# Retry\n\nComplete text after retry.")
    assert uploaded.status_code == 201
    doc_id = uploaded.json()["id"]
    resource = f"{collection}/{doc_id}"

    def interrupt(*_args, **_kwargs):
        detail = client.get(resource).json()
        assert detail["status"] == "processing" and detail["sections"] == []
        raise SystemExit("Simulated interruption after the durable claim")

    with monkeypatch.context() as patch:
        patch.setattr(document_jobs, "parse_document", interrupt)
        with pytest.raises(SystemExit, match="Simulated interruption"):
            _deliver(document_settings, deliveries[0])

    assert _retry(client, resource).status_code == 409
    with document_owner_engine.begin() as connection:
        assert (
            connection.execute(
                update(documents)
                .where(documents.c.team_id == UUID(team_id), documents.c.id == UUID(doc_id))
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            ).rowcount
            == 1
        )
    stale = client.get(resource).json()
    assert stale["status"] == "failed" and stale["error"] == "attempt_expired"
    assert stale["sections"] == []
    retry = _retry(client, resource)
    assert retry.status_code == 200 and retry.json()["attempt"] == 2
    assert len(deliveries) == 2

    _deliver(document_settings, deliveries[0])
    assert client.get(resource).json()["status"] == "queued"
    _deliver(document_settings, deliveries[1])
    final = client.get(resource).json()
    assert (final["status"], final["attempt"]) == ("completed", 2)
    assert "Complete text after retry." in final["sections"][0]["text"]


def test_unreadable_pdf_fails_atomically_and_retry_is_bounded(
    client_factory, document_settings, deliveries
) -> None:
    client = client_factory()
    team = _signup(client, "Invalid file fixture")["teams"][0]["id"]
    collection = f"{TEAMS}/{team}/documents"
    broken_pdf = b"%PDF-1.7\n1 0 obj\nendobj\n%%EOF\n"
    uploaded = _upload(client, collection, "unreadable.pdf", broken_pdf)
    assert uploaded.status_code == 201
    resource = f"{collection}/{uploaded.json()['id']}"
    for attempt in (1, 2, 3):
        if attempt > 1:
            assert _retry(client, resource).status_code == 200
        _deliver(document_settings, deliveries[-1])
        detail = client.get(resource).json()
        assert (detail["status"], detail["attempt"]) == ("failed", attempt)
        assert detail["error"] and detail["sections"] == []
    assert _retry(client, resource).status_code == 409
    assert len(deliveries) == 3


def test_revoked_uploader_cannot_publish_text(
    client_factory, document_settings, deliveries, monkeypatch
):
    owner, admin = client_factory(), client_factory()
    team_id = _signup(owner, "Revocation fixture")["teams"][0]["id"]
    admin_id = _join_team(owner, admin, team_id, "admin")
    collection = f"{TEAMS}/{team_id}/documents"
    uploaded = _upload(admin, collection, "revoked.md", b"# Fixture\n\nPrivate body.")
    assert uploaded.status_code == 201
    resource = f"{collection}/{uploaded.json()['id']}"
    extract = document_jobs.parse_document
    removal_statuses = []

    def revoke_before_publish(*args, **kwargs):
        removed = owner.request(
            "DELETE", f"{TEAMS}/{team_id}/members/{admin_id}", headers={"Origin": ORIGIN}, json={}
        )
        removal_statuses.append(removed.status_code)
        return extract(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(document_jobs, "parse_document", revoke_before_publish)
        _deliver(document_settings, deliveries[0])
    assert removal_statuses == [204]
    assert admin.get(resource).status_code == 404
    detail = owner.get(resource).json()
    assert (detail["status"], detail["error"], detail["sections"]) == (
        "failed",
        "authorization_revoked",
        [],
    )
    assert _retry(owner, resource).status_code == 200
    _deliver(document_settings, deliveries[1])
    assert owner.get(resource).json()["status"] == "completed"


def test_broker_failure_leaves_a_retryable_record(client_factory, deliveries, monkeypatch):
    client = client_factory()
    team_id = _signup(client, "Broker fixture")["teams"][0]["id"]
    collection = f"{TEAMS}/{team_id}/documents"

    def offline(*_args, **_kwargs):
        raise document_jobs.DocumentQueueError("Synthetic broker outage")

    with monkeypatch.context() as patch:
        patch.setattr(document_routes, "enqueue_document", offline)
        response = _upload(client, collection, "broker.md", b"# Broker\n\nSafe retry.")
    assert response.status_code == 201
    resource = f"{collection}/{response.json()['id']}"
    failed = client.get(resource).json()
    assert (failed["status"], failed["error"], failed["sections"]) == (
        "failed",
        "queue_unavailable",
        [],
    )
    assert deliveries == []
    retry = _retry(client, resource)
    assert retry.status_code == 200 and retry.json()["attempt"] == 2
    assert len(deliveries) == 1
