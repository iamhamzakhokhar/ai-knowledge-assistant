"""Exercise the real auth API and tenant boundary in a disposable database."""

import hashlib
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from ipaddress import IPv6Address
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from knowledge_assistant.app import create_app
from knowledge_assistant.config import Settings
from knowledge_assistant.database import create_engine_from_settings, tenant_connection
from knowledge_assistant.migrate import run_migrations

pytestmark = pytest.mark.integration

ORIGIN = "https://dashboard.knowledge.example"
API_HOST = "api.knowledge.example"
AUTH = "/api/v1/auth"
TEAMS = "/api/v1/teams"
COOKIE = "aka_session"


@dataclass(frozen=True)
class Account:
    id: UUID
    email: str
    password: str = field(repr=False)
    team_id: UUID | None = None


def _owner_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "database_user": "aka_owner",
            "database_password_file": Path(os.environ["ADMIN_DATABASE_PASSWORD_FILE"]),
        }
    )


@pytest.fixture(scope="module")
def auth_settings(integration_settings: Settings, auth_disposable_database: str) -> Settings:
    settings = integration_settings.model_copy(
        update={
            "database_name": auth_disposable_database,
            "app_env": "test",
            "allowed_origins": (ORIGIN,),
        }
    )
    assert settings.database_name != integration_settings.database_name
    run_migrations(
        _owner_settings(settings),
        "upgrade",
        "head",
        app_password_file=settings.database_password_file,
    )
    return settings


@pytest.fixture(scope="module")
def auth_engine(auth_settings: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(auth_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def auth_owner_engine(auth_settings: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(_owner_settings(auth_settings))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def new_client(auth_settings: Settings) -> Iterator[Callable[[], TestClient]]:
    with ExitStack() as stack:

        def open_client() -> TestClient:
            # Separate synthetic client IPs avoid shared Redis throttle buckets.
            network = int(IPv6Address("2001:db8::"))
            address = IPv6Address(network | (uuid4().int % (2**96)))
            return stack.enter_context(
                TestClient(
                    create_app(auth_settings),
                    base_url=f"https://{API_HOST}",
                    client=(str(address), 50000),
                )
            )

        yield open_client


def _mutate(client: TestClient, method: str, path: str, body: dict | None = None):
    return client.request(
        method, path, json={} if body is None else body, headers={"Origin": ORIGIN}
    )


def _signup(client: TestClient, team_name: str | None = None) -> Account:
    email = f"fixture-{uuid4().hex}@knowledge.example"
    password = f"Fixture account {uuid4().hex}!"
    body = {"email": email, "password": password}
    if team_name is not None:
        body["team_name"] = team_name
    response = _mutate(client, "POST", f"{AUTH}/signup", body)
    assert response.status_code == 201
    session = response.json()
    assert session["user"]["email"] == email
    teams = session["teams"]
    if team_name is None:
        assert teams == []
    else:
        assert len(teams) == 1
        assert teams[0]["name"] == team_name and teams[0]["role"] == "owner"
    return Account(
        UUID(session["user"]["id"]), email, password, UUID(teams[0]["id"]) if teams else None
    )


def _cookie(client: TestClient) -> str:
    token = client.cookies.get(COOKIE)
    if not token:
        pytest.fail("A successful authentication response must establish a session")
    return token


def _invite(client: TestClient, team_id: UUID, email: str, role: str = "member") -> str:
    response = _mutate(client, "POST", f"{TEAMS}/{team_id}/invites", {"email": email, "role": role})
    assert response.status_code == 201
    payload = response.json()
    token = payload.pop("token", None)
    if not isinstance(token, str) or not token:
        pytest.fail("Invitation creation must return a single-use token")
    assert payload["email"] == email.lower()
    assert payload["role"] == role
    return token


def _join(client: TestClient, token: str):
    return _mutate(client, "POST", f"{TEAMS}/join", {"token": token})


def _team_roles(client: TestClient) -> dict[str, str]:
    response = client.get(TEAMS)
    assert response.status_code == 200
    return {team["id"]: team["role"] for team in response.json()["teams"]}


def test_sign_in_sets_private_cookie_and_sign_out_revokes_its_copy(new_client) -> None:
    client, copied = new_client(), new_client()
    assert client.get(f"{AUTH}/session").status_code == 401
    assert client.get(TEAMS).status_code == 401
    account = _signup(client, "Session fixture workspace")
    signup_token = _cookie(client)

    credentials = {"email": account.email, "password": f"Wrong fixture {uuid4().hex}!"}
    wrong_password = _mutate(copied, "POST", f"{AUTH}/login", credentials)
    credentials["email"] = f"unknown-{uuid4().hex}@knowledge.example"
    unknown_user = _mutate(copied, "POST", f"{AUTH}/login", credentials)
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    if copied.cookies.get(COOKIE) is not None:
        pytest.fail("Failed login established a session")
    duplicate = _mutate(
        copied,
        "POST",
        f"{AUTH}/signup",
        {"email": account.email.upper(), "password": account.password, "team_name": "Duplicate"},
    )
    assert duplicate.status_code == 409
    assert _team_roles(client) == {str(account.team_id): "owner"}

    login = _mutate(
        client,
        "POST",
        f"{AUTH}/login",
        {"email": account.email.upper(), "password": account.password},
    )
    assert login.status_code == 200
    cookie = SimpleCookie(login.headers["set-cookie"])[COOKIE]
    flags = {
        key: cookie[key] for key in ("httponly", "secure", "samesite", "path", "domain", "max-age")
    }
    assert flags["httponly"] and flags["secure"]
    assert flags["samesite"].lower() == "lax" and flags["path"] == "/" and not flags["domain"]
    assert int(flags["max-age"]) == 8 * 60 * 60
    assert "no-store" in login.headers["cache-control"]
    current = client.get(f"{AUTH}/session")
    assert current.status_code == 200
    assert current.json() == login.json()
    token = _cookie(client)
    if token == signup_token:
        pytest.fail("Sign-in reused the preexisting session token")
    if token in login.text or token in current.text:
        pytest.fail("Authentication exposed the session token in a response body")

    copied.cookies.set(COOKIE, signup_token, domain=API_HOST, path="/")
    assert copied.get(f"{AUTH}/session").status_code == 401
    copied.cookies.set(COOKIE, token, domain=API_HOST, path="/")
    assert copied.get(f"{AUTH}/session").status_code == 200
    assert _mutate(client, "POST", f"{AUTH}/logout").status_code == 204
    if client.cookies.get(COOKIE) is not None:
        pytest.fail("Sign-out did not clear the client session cookie")
    assert copied.get(f"{AUTH}/session").status_code == 401
    assert _mutate(copied, "POST", TEAMS, {"name": "Revoked session write"}).status_code == 401
    assert _mutate(client, "POST", f"{AUTH}/logout").status_code == 204


def test_exact_origin_enforced_on_writes_and_validation_redacts_input(new_client) -> None:
    client = new_client()
    account = _signup(client, "Origin fixture workspace")
    for headers in (
        {},
        {"Origin": "null"},
        {"Origin": f"{ORIGIN}:444"},
        {"Origin": f"{ORIGIN}.other.example"},
        [("Origin", ORIGIN), ("Origin", "https://other.example")],
    ):
        response = client.post(TEAMS, json={"name": "Disallowed write"}, headers=headers)
        assert response.status_code == 403
    assert client.post(f"{AUTH}/logout", json={}).status_code == 403
    assert client.post(f"{AUTH}/login", json={"email": account.email}).status_code == 403
    assert client.post(f"{AUTH}/signup", json={}).status_code == 403
    assert _team_roles(client) == {str(account.team_id): "owner"}

    redaction_marker = f"Private validation marker {uuid4().hex}"
    invalid = _mutate(
        client, "POST", f"{AUTH}/signup", {"email": account.email, "password": [redaction_marker]}
    )
    assert invalid.status_code == 422
    if redaction_marker in invalid.text:
        pytest.fail("Validation response reflected confidential input")

    wrong_media = client.post(TEAMS, content=b"{}", headers={"Origin": ORIGIN})
    assert wrong_media.status_code == 415
    oversized = client.post(
        TEAMS,
        content=b" " * 4097,
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    assert oversized.status_code == 413

    preflight = client.options(
        TEAMS,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    # Browser writes use the dashboard's same-origin proxy rather than credentialed CORS.
    assert preflight.status_code == 400
    assert "access-control-allow-credentials" not in preflight.headers
    created = _mutate(client, "POST", TEAMS, {"name": "Second owner workspace"})
    assert created.status_code == 201
    assert _team_roles(client) == {str(account.team_id): "owner", created.json()["id"]: "owner"}


def test_two_owners_cannot_cross_team_resource_or_membership_boundaries(
    new_client, auth_engine: Engine
) -> None:
    left, right = new_client(), new_client()
    owner_a, owner_b = (
        _signup(left, "First tenant fixture"),
        _signup(right, "Second tenant fixture"),
    )
    assert owner_a.team_id and owner_b.team_id
    resource_ids = []
    for owner in (owner_a, owner_b):
        source_id, job_id = uuid4(), uuid4()
        with tenant_connection(auth_engine, owner.team_id) as connection:
            connection.execute(
                text(
                    "INSERT INTO knowledge_sources (id, team_id, name, content) "
                    "VALUES (:id, :team, 'Fictional source', 'Fixture content')"
                ),
                {"id": source_id, "team": owner.team_id},
            )
            connection.execute(
                text(
                    "INSERT INTO job_runs (id, team_id, kind, status) "
                    "VALUES (:id, :team, 'integration_fixture', 'queued')"
                ),
                {"id": job_id, "team": owner.team_id},
            )
        resource_ids.append((source_id, job_id))

    for client, own, other, own_ids, other_ids in (
        (left, owner_a, owner_b, resource_ids[0], resource_ids[1]),
        (right, owner_b, owner_a, resource_ids[1], resource_ids[0]),
    ):
        assert _team_roles(client) == {str(own.team_id): "owner"}
        for resource, own_id, other_id in zip(("sources", "jobs"), own_ids, other_ids, strict=True):
            own_path = f"{TEAMS}/{own.team_id}/{resource}"
            external_path = f"{TEAMS}/{other.team_id}/{resource}"
            listing = client.get(own_path)
            assert listing.status_code == 200
            assert [entry["id"] for entry in listing.json()[resource]] == [str(own_id)]
            detail = client.get(f"{own_path}/{own_id}")
            assert detail.status_code == 200 and detail.json()["id"] == str(own_id)
            assert client.get(f"{own_path}/{other_id}").status_code == 404
            assert client.get(external_path).status_code == 404
            assert client.get(f"{external_path}/{other_id}").status_code == 404
        other_members = f"{TEAMS}/{other.team_id}/members"
        assert client.get(other_members).status_code == 404
        assert (
            _mutate(
                client,
                "POST",
                f"{TEAMS}/{other.team_id}/invites",
                {"email": own.email, "role": "member"},
            ).status_code
            == 404
        )
        assert (
            _mutate(client, "PATCH", f"{other_members}/{other.id}", {"role": "member"}).status_code
            == 404
        )
        assert (
            _mutate(client, "DELETE", f"{TEAMS}/{own.team_id}/members/{other.id}").status_code
            == 404
        )


def test_invite_binds_recipient_rejects_replay_and_preserves_other_membership(new_client) -> None:
    owner, invited, wrong = new_client(), new_client(), new_client()
    team = _signup(owner, "Invitation workspace").team_id
    recipient = _signup(invited, "Recipient's existing workspace")
    _signup(wrong)
    assert team is not None
    token = _invite(owner, team, recipient.email.upper())
    rejected = _join(wrong, token)
    assert rejected.status_code == 404
    assert _team_roles(wrong) == {}
    joined = _join(invited, token)
    assert joined.status_code == 200
    assert joined.json()["id"] == str(team) and joined.json()["role"] == "member"
    replay = _join(invited, token)
    assert replay.status_code == 404 and replay.json() == rejected.json()
    assert _team_roles(invited) == {str(recipient.team_id): "owner", str(team): "member"}
    roster = owner.get(f"{TEAMS}/{team}/members")
    assert roster.status_code == 200
    assert [row["role"] for row in roster.json()["members"] if row["id"] == str(recipient.id)] == [
        "member"
    ]


def test_concurrent_acceptance_creates_only_one_membership(new_client) -> None:
    owner, first, second = new_client(), new_client(), new_client()
    team = _signup(owner, "Concurrent invitation workspace").team_id
    recipient = _signup(first)
    assert team is not None
    assert (
        _mutate(
            second,
            "POST",
            f"{AUTH}/login",
            {"email": recipient.email, "password": recipient.password},
        ).status_code
        == 200
    )
    token = _invite(owner, team, recipient.email)
    start = Barrier(2, timeout=5)

    def accept(client: TestClient) -> int:
        start.wait()
        return _join(client, token).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept, client) for client in (first, second)]
        statuses = sorted(future.result(timeout=15) for future in futures)
    assert statuses == [200, 404]
    assert _team_roles(first) == _team_roles(second) == {str(team): "member"}


def test_existing_member_cannot_accept_a_second_invite_as_a_role_upgrade(
    new_client, auth_owner_engine: Engine
) -> None:
    owner, recipient = new_client(), new_client()
    team = _signup(owner, "Duplicate membership workspace").team_id
    account = _signup(recipient)
    assert team is not None
    member_invite = _invite(owner, team, account.email)
    admin_invite = _invite(owner, team, account.email, "admin")

    assert _join(recipient, member_invite).status_code == 200
    assert _join(recipient, admin_invite).status_code == 409
    assert _team_roles(recipient) == {str(team): "member"}
    with auth_owner_engine.connect() as connection:
        used_at = connection.execute(
            text("SELECT used_at FROM team_invites WHERE token_hash = :digest"),
            {"digest": hashlib.sha256(admin_invite.encode()).digest()},
        ).scalar_one()
        assert used_at is None

    assert _mutate(owner, "DELETE", f"{TEAMS}/{team}/members/{account.id}").status_code == 204
    assert _join(recipient, admin_invite).status_code == 404
    assert _join(recipient, _invite(owner, team, account.email, "admin")).status_code == 200
    assert _team_roles(recipient) == {str(team): "admin"}


def test_roles_change_live_and_only_nonowners_can_be_removed(new_client) -> None:
    owner, admin, member, guest = (new_client() for _ in range(4))
    primary = _signup(owner, "Role administration workspace")
    administrator = _signup(admin)
    participant = _signup(member, "Participant's independent workspace")
    guest_account = _signup(guest)
    team = primary.team_id
    assert team is not None
    assert _join(admin, _invite(owner, team, administrator.email, "admin")).status_code == 200
    assert _join(member, _invite(owner, team, participant.email)).status_code == 200
    members = f"{TEAMS}/{team}/members"
    invites = f"{TEAMS}/{team}/invites"

    assert member.get(members).status_code == 200
    assert (
        _mutate(
            member, "POST", invites, {"email": guest_account.email, "role": "member"}
        ).status_code
        == 403
    )
    assert _mutate(member, "DELETE", f"{members}/{administrator.id}").status_code == 403
    assert (
        _mutate(admin, "POST", invites, {"email": guest_account.email, "role": "admin"}).status_code
        == 403
    )
    assert (
        _mutate(admin, "PATCH", f"{members}/{participant.id}", {"role": "admin"}).status_code == 403
    )
    assert _mutate(admin, "DELETE", f"{members}/{administrator.id}").status_code == 403
    assert _mutate(admin, "DELETE", f"{members}/{primary.id}").status_code == 403
    assert _mutate(owner, "PATCH", f"{members}/{primary.id}", {"role": "member"}).status_code == 403
    assert _mutate(owner, "DELETE", f"{members}/{primary.id}").status_code == 403

    promoted = _mutate(owner, "PATCH", f"{members}/{participant.id}", {"role": "admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    assert _team_roles(member)[str(team)] == "admin"
    assert _join(guest, _invite(member, team, guest_account.email)).status_code == 200
    assert _mutate(admin, "DELETE", f"{members}/{guest_account.id}").status_code == 204
    assert guest.get(members).status_code == 404

    pending = _invite(member, team, guest_account.email)
    demoted = _mutate(owner, "PATCH", f"{members}/{participant.id}", {"role": "member"})
    assert demoted.status_code == 200 and demoted.json()["role"] == "member"
    assert _join(guest, pending).status_code == 404
    assert (
        _mutate(
            member, "POST", invites, {"email": guest_account.email, "role": "member"}
        ).status_code
        == 403
    )
    assert _mutate(admin, "DELETE", f"{members}/{participant.id}").status_code == 204
    assert member.get(f"{TEAMS}/{team}/sources").status_code == 404
    assert member.get(f"{TEAMS}/{team}/jobs").status_code == 404
    assert _team_roles(member) == {str(participant.team_id): "owner"}
    assert _team_roles(owner) == {str(team): "owner"}


def test_session_and_invitation_expiry_are_enforced_and_tokens_are_hashed(
    new_client, auth_owner_engine: Engine
) -> None:
    owner, recipient = new_client(), new_client()
    team = _signup(owner, "Expiring capability workspace").team_id
    account = _signup(recipient)
    assert team is not None
    token = _invite(owner, team, account.email)
    with auth_owner_engine.begin() as connection:
        session_hash = connection.scalar(
            text("SELECT token_hash FROM auth_sessions WHERE user_id = :user"), {"user": account.id}
        )
        invite_hash = connection.scalar(
            text("SELECT token_hash FROM team_invites WHERE team_id = :team AND email = :email"),
            {"team": team, "email": account.email},
        )
        if session_hash != hashlib.sha256(_cookie(recipient).encode()).digest():
            pytest.fail("Session storage must contain the bearer digest")
        if invite_hash != hashlib.sha256(token.encode()).digest():
            pytest.fail("Invitation storage must contain the bearer digest")
        connection.execute(
            text(
                "UPDATE team_invites SET expires_at = now() - interval '1 minute' "
                "WHERE team_id = :team"
            ),
            {"team": team},
        )
    assert _join(recipient, token).status_code == 404
    with auth_owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE auth_sessions SET expires_at = now() - interval '1 minute' "
                "WHERE user_id = :user"
            ),
            {"user": account.id},
        )
    assert recipient.get(f"{AUTH}/session").status_code == 401
    assert _mutate(recipient, "POST", TEAMS, {"name": "Expired session write"}).status_code == 401


def test_real_redis_throttles_an_account_across_client_addresses(new_client) -> None:
    first, second = new_client(), new_client()
    email = f"throttle-{uuid4().hex}@knowledge.example"
    body = {"email": email, "password": f"Unknown fixture {uuid4().hex}!"}
    for _ in range(12):
        assert _mutate(first, "POST", f"{AUTH}/login", body).status_code == 401
    body["email"] = email.upper()
    blocked = _mutate(second, "POST", f"{AUTH}/login", body)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_auth_unavailable_without_throttle_does_not_create_account(
    auth_settings: Settings, new_client
) -> None:
    settings = auth_settings.model_copy(update={"redis_password_file": None})
    body = {
        "email": f"unavailable-{uuid4().hex}@knowledge.example",
        "password": f"Fixture account {uuid4().hex}!",
    }
    with TestClient(create_app(settings), base_url=f"https://{API_HOST}") as client:
        assert client.get("/health").status_code == 200
        denied = _mutate(client, "POST", f"{AUTH}/signup", body)
        assert denied.status_code == 503
        assert denied.json() == {"detail": "Authentication is unavailable"}
        assert client.get(f"{AUTH}/session").status_code == 401
    assert _mutate(new_client(), "POST", f"{AUTH}/signup", body).status_code == 201
