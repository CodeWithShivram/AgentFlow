"""Authentication, sessions and workspace RBAC through the real HTTP API.

Runs against a migrated throwaway PostgreSQL database and the local Redis (rate limits).
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from starlette.websockets import WebSocketDisconnect

from app.api.deps import Principal, get_websocket_principal
from app.core.config import Settings
from app.core.tokens import create_access_token, decode_access_token
from app.main import create_app
from app.services.auth import AuthService
from tests.conftest import ALLOWED_ORIGIN, json_log_lines, make_client

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"
ACCESS, REFRESH, CSRF = "agentflow_access", "agentflow_refresh", "agentflow_csrf"
API = "/api/v1"

ClientFactory = Callable[..., TestClient]


@pytest.fixture
def new_client(api_settings: Settings) -> Iterator[ClientFactory]:
    """Each client is a separate browser (its own cookie jar) against its own app instance."""
    with ExitStack() as stack:

        def factory(settings: Settings | None = None, *, raise_errors: bool = True) -> TestClient:
            app = create_app(settings or api_settings)

            @app.websocket("/test/ws")
            async def whoami(
                websocket: WebSocket,
                principal: Annotated[Principal, Depends(get_websocket_principal)],
            ) -> None:
                await websocket.accept()
                await websocket.send_text(str(principal.user_id))
                await websocket.close()

            return stack.enter_context(make_client(app, raise_server_exceptions=raise_errors))

        yield factory


@pytest.fixture
def client(new_client: ClientFactory) -> TestClient:
    return new_client()


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


def register(
    client: TestClient, email: str, password: str = PASSWORD, **extra: Any
) -> httpx.Response:
    response: httpx.Response = client.post(
        f"{API}/auth/register", json={"email": email, "password": password, **extra}
    )
    return response


def login(client: TestClient, email: str, password: str = PASSWORD) -> httpx.Response:
    response: httpx.Response = client.post(
        f"{API}/auth/login", json={"email": email, "password": password}
    )
    return response


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF]}


def refresh(client: TestClient, **headers: str) -> httpx.Response:
    response: httpx.Response = client.post(
        f"{API}/auth/refresh", headers={**csrf(client), **headers}
    )
    return response


def my_workspace(client: TestClient) -> str:
    workspace_id: str = client.get(f"{API}/auth/me").json()["memberships"][0]["workspace_id"]
    return workspace_id


def invite(client: TestClient, workspace_id: str, email: str, role: str) -> httpx.Response:
    response: httpx.Response = client.post(
        f"{API}/workspaces/{workspace_id}/invitations",
        json={"email": email, "role": role},
        headers=csrf(client),
    )
    return response


def accept(client: TestClient, token: str) -> httpx.Response:
    response: httpx.Response = client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=csrf(client)
    )
    return response


def use_only_cookies(client: TestClient, **cookies: str) -> None:
    """Replaces the cookie jar, e.g. to replay an old token."""
    client.cookies.clear()
    for name, value in cookies.items():
        client.cookies.set(name, value)


def sql(settings: Settings, statement: str, **params: Any) -> list[Any]:
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        engine.dispose()


def set_cookie_headers(response: httpx.Response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def member(new_client: ClientFactory, owner: TestClient, role: str) -> TestClient:
    """A second user who joined the owner's workspace with `role`."""
    other, email = new_client(), _email()
    register(other, email)
    token = invite(owner, my_workspace(owner), email, role).json()["invitation_token"]
    assert accept(other, token).status_code == 200
    return other


# --- registration -------------------------------------------------------------------------


def test_register_creates_user_personal_workspace_and_owner_membership(
    client: TestClient, api_settings: Settings
) -> None:
    email = _email()
    response = register(client, email.upper(), workspace_name="Ada's lab")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"user", "memberships", "access_expires_at", "session_expires_at"}
    assert body["user"]["email"] == email  # normalized to lower case
    assert body["memberships"] == [
        {
            "workspace_id": body["memberships"][0]["workspace_id"],
            "workspace_name": "Ada's lab",
            "role": "OWNER",
        }
    ]
    rows = sql(
        api_settings,
        "SELECT w.owner_id = u.id, m.role FROM users u JOIN workspaces w ON w.owner_id = u.id "
        "JOIN workspace_members m ON m.workspace_id = w.id AND m.user_id = u.id WHERE u.email = :e",
        e=email,
    )
    assert rows == [(True, "OWNER")]
    stored_hash = sql(api_settings, "SELECT password_hash FROM users WHERE email = :e", e=email)
    assert stored_hash[0][0].startswith("$argon2id$")


def test_register_sets_session_cookies_and_returns_no_tokens(client: TestClient) -> None:
    response = register(client, _email())

    cookies = set_cookie_headers(response)
    assert set(cookies) == {ACCESS, REFRESH, CSRF}
    assert "HttpOnly" in cookies[ACCESS] and "SameSite=lax" in cookies[ACCESS]
    assert "HttpOnly" in cookies[REFRESH] and "Path=/api/v1/auth" in cookies[REFRESH]
    assert "SameSite=strict" in cookies[REFRESH]
    assert "HttpOnly" not in cookies[CSRF]
    assert all("Domain=" not in header for header in cookies.values())
    for name in (ACCESS, REFRESH, CSRF):
        assert client.cookies[name] not in response.text


def test_duplicate_email_is_rejected_case_insensitively(client: TestClient) -> None:
    email = _email()
    register(client, email)

    response = register(client, email.upper())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@example.com", "password": "short-pw"},
        {"email": "a@example.com", "password": " " * 20},
        {"email": "a@example.com", "password": "x" * 129},
        {"email": "a@example.com", "password": PASSWORD, "role": "OWNER"},
    ],
)
def test_invalid_registration_is_rejected_without_echoing_input(
    client: TestClient, payload: dict[str, str]
) -> None:
    response = client.post(f"{API}/auth/register", json=payload)

    assert response.status_code == 422
    assert payload["password"] not in response.text


def test_failed_signup_rolls_back_everything(
    new_client: ClientFactory, api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("session store down")

    monkeypatch.setattr(AuthService, "_start_session", explode)
    client = new_client(raise_errors=False)

    response = register(client, _email())

    assert response.status_code == 500
    for query in (
        "SELECT count(*) FROM users",
        "SELECT count(*) FROM workspaces",
        "SELECT count(*) FROM workspace_members",
        "SELECT count(*) FROM auth_sessions",
    ):
        assert sql(api_settings, query) == [(0,)], query


# --- login and current user ---------------------------------------------------------------


def test_login_and_me(new_client: ClientFactory) -> None:
    email = _email()
    register(new_client(), email)
    client = new_client()

    assert client.get(f"{API}/auth/me").status_code == 401
    response = login(client, email.upper())

    assert response.status_code == 200
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == email
    assert [m["role"] for m in me.json()["memberships"]] == ["OWNER"]


def test_wrong_password_and_unknown_email_are_indistinguishable(client: TestClient) -> None:
    email = _email()
    register(client, email)
    client.cookies.clear()

    wrong = login(client, email, "wrong-password-123")
    unknown = login(client, _email())

    for response in (wrong, unknown):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert response.json()["error"]["message"] == "Invalid email or password."
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("token", ["garbage", "a.b.c", ""])
def test_invalid_access_cookie_is_rejected(client: TestClient, token: str) -> None:
    use_only_cookies(client, **{ACCESS: token})

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_expired_access_token_is_rejected(client: TestClient, api_settings: Settings) -> None:
    register(client, _email())
    claims = decode_access_token(api_settings, client.cookies[ACCESS])
    expired = create_access_token(
        api_settings,
        user_id=claims.user_id,
        session_id=claims.session_id,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    use_only_cookies(client, **{ACCESS: expired.token})

    assert client.get(f"{API}/auth/me").status_code == 401


# --- CSRF and Origin ----------------------------------------------------------------------


def test_unsafe_authenticated_requests_require_the_session_csrf_token(client: TestClient) -> None:
    register(client, _email())
    workspace_id = my_workspace(client)
    url = f"{API}/workspaces/{workspace_id}/invitations"
    body = {"email": _email(), "role": "MEMBER"}

    missing = client.post(url, json=body)
    wrong = client.post(url, json=body, headers={"X-CSRF-Token": "forged"})
    correct = client.post(url, json=body, headers=csrf(client))

    assert missing.status_code == wrong.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_TOKEN_INVALID"
    assert correct.status_code == 201


def test_csrf_token_from_another_session_is_rejected(new_client: ClientFactory) -> None:
    alice, mallory = new_client(), new_client()
    register(alice, _email())
    register(mallory, _email())

    response = alice.post(
        f"{API}/workspaces/{my_workspace(alice)}/invitations",
        json={"email": _email(), "role": "MEMBER"},
        headers={"X-CSRF-Token": mallory.cookies[CSRF]},
    )

    assert response.status_code == 403


def test_cross_site_login_is_blocked_by_origin_check(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/login",
        json={"email": _email(), "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


# --- refresh rotation, reuse detection and expiry -----------------------------------------


def test_refresh_rotates_the_refresh_token_and_issues_a_new_access_token(
    client: TestClient,
) -> None:
    register(client, _email())
    old_refresh, old_access = client.cookies[REFRESH], client.cookies[ACCESS]

    response = refresh(client)

    assert response.status_code == 200
    assert client.cookies[REFRESH] != old_refresh
    assert client.cookies[ACCESS] != old_access
    assert set(set_cookie_headers(response)) == {ACCESS, REFRESH}  # CSRF cookie unchanged
    assert client.get(f"{API}/auth/me").status_code == 200


def test_reusing_a_rotated_refresh_token_revokes_the_whole_session(
    client: TestClient, api_settings: Settings
) -> None:
    register(client, _email())
    stolen = client.cookies[REFRESH]
    csrf_token = client.cookies[CSRF]
    refresh(client)
    current_refresh, current_access = client.cookies[REFRESH], client.cookies[ACCESS]

    use_only_cookies(client, **{REFRESH: stolen, CSRF: csrf_token})
    reuse = client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": csrf_token})

    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"
    assert all("Max-Age=0" in h for h in set_cookie_headers(reuse).values())
    # The legitimate holder's newer tokens are dead too.
    use_only_cookies(client, **{REFRESH: current_refresh, CSRF: csrf_token})
    assert (
        client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": csrf_token}).status_code == 401
    )
    use_only_cookies(client, **{ACCESS: current_access})
    assert client.get(f"{API}/auth/me").status_code == 401
    assert sql(api_settings, "SELECT revoked_reason FROM auth_sessions") == [
        ("REFRESH_TOKEN_REUSE",)
    ]


def test_refresh_without_csrf_fails_and_does_not_consume_the_token(client: TestClient) -> None:
    register(client, _email())

    rejected = client.post(f"{API}/auth/refresh")
    accepted = refresh(client)

    assert rejected.status_code == 403
    assert accepted.status_code == 200


def test_expired_refresh_token_is_rejected(client: TestClient, api_settings: Settings) -> None:
    register(client, _email())
    sql(api_settings, "UPDATE refresh_tokens SET expires_at = now() - interval '1 second'")

    response = refresh(client)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


def test_session_absolute_expiry_ends_access_and_refresh(
    client: TestClient, api_settings: Settings
) -> None:
    register(client, _email())
    sql(
        api_settings,
        "UPDATE auth_sessions SET expires_at = now() - interval '1 second', "
        "created_at = now() - interval '1 day'",
    )

    assert client.get(f"{API}/auth/me").status_code == 401
    assert refresh(client).status_code == 401


def test_refresh_without_cookie_is_rejected(client: TestClient) -> None:
    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


# --- logout ---------------------------------------------------------------------------------


def test_logout_revokes_the_session_immediately(client: TestClient, api_settings: Settings) -> None:
    register(client, _email())
    access, refresh_token, csrf_token = (
        client.cookies[ACCESS],
        client.cookies[REFRESH],
        client.cookies[CSRF],
    )

    response = client.post(f"{API}/auth/logout", headers=csrf(client))

    assert response.status_code == 204
    assert all("Max-Age=0" in h for h in set_cookie_headers(response).values())
    use_only_cookies(client, **{ACCESS: access})
    assert client.get(f"{API}/auth/me").status_code == 401  # access token dead before expiry
    use_only_cookies(client, **{REFRESH: refresh_token, CSRF: csrf_token})
    assert (
        client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": csrf_token}).status_code == 401
    )
    assert sql(api_settings, "SELECT revoked_reason FROM auth_sessions") == [("LOGOUT",)]


def test_logout_requires_csrf_and_is_idempotent(client: TestClient) -> None:
    register(client, _email())

    assert client.post(f"{API}/auth/logout").status_code == 403
    assert client.get(f"{API}/auth/me").status_code == 200  # not logged out by the forged request
    assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204
    client.cookies.clear()
    assert client.post(f"{API}/auth/logout").status_code == 204


def test_logout_ends_only_the_current_session(new_client: ClientFactory) -> None:
    email = _email()
    laptop, phone = new_client(), new_client()
    register(laptop, email)
    login(phone, email)

    laptop.post(f"{API}/auth/logout", headers=csrf(laptop))

    assert laptop.get(f"{API}/auth/me").status_code == 401
    assert phone.get(f"{API}/auth/me").status_code == 200


# --- workspace RBAC and isolation ---------------------------------------------------------


def test_owner_invites_and_invitee_joins_with_the_granted_role(new_client: ClientFactory) -> None:
    owner, invitee, email = new_client(), new_client(), _email()
    register(owner, _email())
    register(invitee, email)
    workspace_id = my_workspace(owner)

    created = invite(owner, workspace_id, email, "ADMIN")
    accepted = accept(invitee, created.json()["invitation_token"])

    assert created.status_code == 201
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "ADMIN"
    roles = {m["role"] for m in invitee.get(f"{API}/auth/me").json()["memberships"]}
    assert roles == {"OWNER", "ADMIN"}  # own personal workspace plus the new one
    members = owner.get(f"{API}/workspaces/{workspace_id}/members").json()
    assert members["total"] == 2


@pytest.mark.parametrize(
    ("role", "invite_as", "expected_status", "expected_code"),
    [
        ("ADMIN", "MEMBER", 201, None),
        ("ADMIN", "ADMIN", 403, "CANNOT_GRANT_ROLE"),
        ("MEMBER", "VIEWER", 403, "INSUFFICIENT_ROLE"),
        ("VIEWER", "VIEWER", 403, "INSUFFICIENT_ROLE"),
    ],
)
def test_invitation_permissions_follow_the_role_hierarchy(
    new_client: ClientFactory,
    role: str,
    invite_as: str,
    expected_status: int,
    expected_code: str | None,
) -> None:
    owner = new_client()
    register(owner, _email())
    actor = member(new_client, owner, role)

    response = invite(actor, my_workspace(owner), _email(), invite_as)

    assert response.status_code == expected_status
    if expected_code:
        assert response.json()["error"]["code"] == expected_code


def test_viewer_can_read_the_workspace(new_client: ClientFactory) -> None:
    owner = new_client()
    register(owner, _email())
    viewer = member(new_client, owner, "VIEWER")
    workspace_id = my_workspace(owner)

    workspace = viewer.get(f"{API}/workspaces/{workspace_id}")
    members = viewer.get(f"{API}/workspaces/{workspace_id}/members")

    assert workspace.status_code == 200 and workspace.json()["role"] == "VIEWER"
    assert members.status_code == 200 and members.json()["total"] == 2


def test_other_users_cannot_see_or_touch_a_workspace_even_with_its_id(
    new_client: ClientFactory,
) -> None:
    alice, mallory = new_client(), new_client()
    register(alice, _email())
    register(mallory, _email())
    target = my_workspace(alice)

    responses = [
        mallory.get(f"{API}/workspaces/{target}"),
        mallory.get(f"{API}/workspaces/{target}/members"),
        invite(mallory, target, _email(), "VIEWER"),
        mallory.get(f"{API}/workspaces/{uuid.uuid4()}"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    listed = {w["id"] for w in mallory.get(f"{API}/workspaces").json()["items"]}
    assert target not in listed


def test_workspace_lists_are_paginated(client: TestClient) -> None:
    register(client, _email())

    page = client.get(f"{API}/workspaces", params={"limit": 1, "offset": 0}).json()
    too_big = client.get(f"{API}/workspaces", params={"limit": 101})

    assert page["total"] == 1 and page["limit"] == 1 and len(page["items"]) == 1
    assert too_big.status_code == 422


# --- invitations ----------------------------------------------------------------------------


def test_invitation_is_bound_to_the_invited_email(new_client: ClientFactory) -> None:
    owner, stranger = new_client(), new_client()
    register(owner, _email())
    register(stranger, _email())
    token = invite(owner, my_workspace(owner), _email(), "MEMBER").json()["invitation_token"]

    response = accept(stranger, token)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVITATION_NOT_FOUND"


def test_invitation_cannot_be_reused_or_used_after_expiry(
    new_client: ClientFactory, api_settings: Settings
) -> None:
    owner, invitee, email = new_client(), new_client(), _email()
    register(owner, _email())
    register(invitee, email)
    workspace_id = my_workspace(owner)
    first = invite(owner, workspace_id, email, "VIEWER").json()["invitation_token"]
    second = invite(owner, workspace_id, _email(), "VIEWER").json()

    assert accept(invitee, first).status_code == 200
    assert accept(invitee, first).status_code == 404
    sql(
        api_settings,
        "UPDATE workspace_invitations SET expires_at = now() - interval '1 second' WHERE id = :id",
        id=second["id"],
    )
    assert accept(invitee, second["invitation_token"]).status_code == 404
    assert accept(invitee, "unknown-token").status_code == 404


def test_inviting_an_existing_member_conflicts(client: TestClient) -> None:
    email = _email()
    register(client, email)

    response = invite(client, my_workspace(client), email, "MEMBER")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_A_MEMBER"


def test_invitation_token_is_stored_only_as_a_hash(
    client: TestClient, api_settings: Settings
) -> None:
    register(client, _email())

    token = invite(client, my_workspace(client), _email(), "MEMBER").json()["invitation_token"]

    stored = sql(api_settings, "SELECT token_hash FROM workspace_invitations")
    assert len(stored) == 1 and stored[0][0] != token and len(stored[0][0]) == 64


def test_refresh_tokens_are_stored_only_as_hashes(
    client: TestClient, api_settings: Settings
) -> None:
    register(client, _email())
    refresh(client)

    hashes = {row[0] for row in sql(api_settings, "SELECT token_hash FROM refresh_tokens")}
    csrf_hashes = {row[0] for row in sql(api_settings, "SELECT csrf_token_hash FROM auth_sessions")}

    assert len(hashes) == 2
    assert client.cookies[REFRESH] not in hashes
    assert client.cookies[CSRF] not in csrf_hashes


# --- rate limiting --------------------------------------------------------------------------


def test_login_attempts_are_rate_limited(new_client: ClientFactory, api_settings: Settings) -> None:
    client = new_client(api_settings.model_copy(update={"auth_login_attempts_per_minute": 3}))
    email = _email()

    statuses = [login(client, email, "wrong-password-123").status_code for _ in range(3)]
    limited = login(client, email, "wrong-password-123")

    assert statuses == [401, 401, 401]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60


def test_auth_fails_closed_when_the_rate_limiter_is_unavailable(
    new_client: ClientFactory, api_settings: Settings
) -> None:
    from tests.conftest import closed_port

    client = new_client(
        api_settings.model_copy(
            update={"redis_port": closed_port(), "redis_socket_timeout_seconds": 1.0}
        )
    )

    response = login(client, _email())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RATE_LIMITER_UNAVAILABLE"


# --- secrets never leak ---------------------------------------------------------------------


def test_passwords_and_tokens_never_appear_in_logs_or_errors(
    new_client: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    client, email = new_client(), _email()
    register(client, email)
    secrets_seen = [PASSWORD, client.cookies[ACCESS], client.cookies[REFRESH], client.cookies[CSRF]]
    refresh(client)
    secrets_seen += [client.cookies[ACCESS], client.cookies[REFRESH]]
    login(new_client(), email, "wrong-password-xyz")
    token = invite(client, my_workspace(client), _email(), "MEMBER").json()["invitation_token"]
    client.post(f"{API}/auth/logout", headers=csrf(client))

    output = capsys.readouterr().out
    assert json_log_lines(output), "expected structured logs"
    for secret in [*secrets_seen, "wrong-password-xyz", token]:
        assert secret not in output


# --- WebSocket authentication ---------------------------------------------------------------


def test_websocket_uses_cookie_auth_and_origin_validation(client: TestClient) -> None:
    body = register(client, _email()).json()

    with client.websocket_connect("/test/ws", headers={"Origin": ALLOWED_ORIGIN}) as ws:
        assert ws.receive_text() == body["user"]["id"]

    with pytest.raises(WebSocketDisconnect) as foreign:
        with client.websocket_connect("/test/ws", headers={"Origin": "https://evil.example"}):
            pass
    assert foreign.value.code == 1008

    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as anonymous:
        with client.websocket_connect("/test/ws", headers={"Origin": ALLOWED_ORIGIN}):
            pass
    assert anonymous.value.code == 1008
