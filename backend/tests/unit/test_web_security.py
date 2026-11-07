"""Cookies, Origin validation, CORS, password hashing and role rules (no database needed)."""

from collections.abc import Iterator
from http.cookies import SimpleCookie

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

from app.core.cookies import clear_session_cookies, cookie_names, set_session_cookies
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app
from app.models import WorkspaceRole
from app.services.authorization import can_grant, role_at_least
from tests.conftest import ALLOWED_ORIGIN, make_client, make_settings

ROLES = [WorkspaceRole.VIEWER, WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER]


def _set_cookies(response: Response) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    for header in response.headers.getlist("set-cookie"):
        cookie: SimpleCookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            attributes = {k: str(v) for k, v in morsel.items() if v}
            parsed[name] = {"value": morsel.value, **attributes}
            parsed[name]["raw"] = header
    return parsed


def _session_cookies(secure: bool) -> dict[str, dict[str, str]]:
    response = Response()
    set_session_cookies(
        response,
        make_settings(auth_cookie_secure=secure),
        access_token="a",
        refresh_token="r",
        refresh_max_age=100,
        csrf_token="c",
        csrf_max_age=200,
    )
    return _set_cookies(response)


def test_development_cookie_attributes() -> None:
    cookies = _session_cookies(secure=False)

    access, refresh, csrf = (
        cookies["agentflow_access"],
        cookies["agentflow_refresh"],
        cookies["agentflow_csrf"],
    )
    assert access["path"] == "/" and access["samesite"] == "lax" and access["httponly"]
    assert refresh["path"] == "/api/v1/auth" and refresh["samesite"] == "strict"
    assert refresh["httponly"] and refresh["max-age"] == "100"
    assert csrf["path"] == "/" and "httponly" not in csrf and csrf["max-age"] == "200"
    for cookie in cookies.values():
        assert "domain" not in cookie  # host-only
        assert "secure" not in cookie


def test_production_cookies_are_secure_and_prefixed() -> None:
    cookies = _session_cookies(secure=True)

    assert set(cookies) == {
        "__Host-agentflow_access",
        "__Secure-agentflow_refresh",
        "__Host-agentflow_csrf",
    }
    for name, cookie in cookies.items():
        assert cookie["secure"], name
        assert "domain" not in cookie, name
        if name.startswith("__Host-"):
            assert cookie["path"] == "/", name


def test_clearing_cookies_expires_all_three() -> None:
    response = Response()
    clear_session_cookies(response, make_settings())

    cookies = _set_cookies(response)
    assert set(cookies) == set(vars(cookie_names(make_settings())).values())
    assert all(cookie["max-age"] == "0" for cookie in cookies.values())
    assert cookies["agentflow_refresh"]["path"] == "/api/v1/auth"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(make_settings())

    @app.post("/test/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    @app.websocket("/test/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    with make_client(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://localhost:3000.evil.example"},
        {"Origin": ""},
    ],
)
def test_unsafe_request_from_disallowed_origin_is_rejected(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.post("/test/echo", headers={**headers, REQUEST_ID_HEADER: "origin-1"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert response.json()["error"]["request_id"] == "origin-1"


def test_unsafe_request_without_origin_or_referer_is_rejected(client: TestClient) -> None:
    client.headers.pop("Origin")

    assert client.post("/test/echo").status_code == 403


def test_referer_is_used_when_origin_is_absent(client: TestClient) -> None:
    client.headers.pop("Origin")

    allowed = client.post("/test/echo", headers={"Referer": f"{ALLOWED_ORIGIN}/workflows/new"})
    denied = client.post("/test/echo", headers={"Referer": "https://evil.example/page"})

    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_safe_methods_are_not_origin_checked(client: TestClient) -> None:
    assert (
        client.get("/api/v1/health/live", headers={"Origin": "https://evil.example"}).status_code
        == 200
    )


def test_cors_allows_only_configured_origins_with_credentials(client: TestClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-csrf-token",
    }
    allowed = client.options("/api/v1/auth/login", headers={**preflight, "Origin": ALLOWED_ORIGIN})
    denied = client.options(
        "/api/v1/auth/login", headers={**preflight, "Origin": "https://evil.example"}
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-origin" not in denied.headers


def test_websocket_handshake_requires_allowed_origin(client: TestClient) -> None:
    with client.websocket_connect("/test/ws", headers={"Origin": ALLOWED_ORIGIN}) as ws:
        assert ws.receive_text() == "hello"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/test/ws", headers={"Origin": "https://evil.example"}):
            pass
    assert exc_info.value.code == 1008


@pytest.mark.anyio
async def test_passwords_use_argon2id_and_verify() -> None:
    hashed = await hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2id$")
    assert "correct horse" not in hashed
    assert await verify_password(hashed, "correct horse battery staple")
    assert not await verify_password(hashed, "wrong password!!")
    assert not await verify_password("not-a-hash", "anything")
    assert not needs_rehash(hashed)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("minimum", ROLES)
def test_role_hierarchy(role: WorkspaceRole, minimum: WorkspaceRole) -> None:
    assert role_at_least(role, minimum) == (ROLES.index(role) >= ROLES.index(minimum))


@pytest.mark.parametrize(
    ("granter", "grantable"),
    [
        (WorkspaceRole.OWNER, {WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}),
        (WorkspaceRole.ADMIN, {WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}),
        (WorkspaceRole.MEMBER, {WorkspaceRole.VIEWER}),
        (WorkspaceRole.VIEWER, set()),
    ],
)
def test_who_can_grant_which_role(granter: WorkspaceRole, grantable: set[WorkspaceRole]) -> None:
    assert {role for role in ROLES if can_grant(granter, role)} == grantable
