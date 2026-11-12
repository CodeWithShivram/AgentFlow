"""Helpers that drive the HTTP API like a browser: one TestClient per user/cookie jar."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx2
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import Settings

PASSWORD = "correct-horse-battery-staple"
ACCESS, REFRESH, CSRF = "agentflow_access", "agentflow_refresh", "agentflow_csrf"
API = "/api/v1"

ClientFactory = Callable[..., TestClient]


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


def register(
    client: TestClient, email: str, password: str = PASSWORD, **extra: Any
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/auth/register", json={"email": email, "password": password, **extra}
    )
    return response


def login(client: TestClient, email: str, password: str = PASSWORD) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/auth/login", json={"email": email, "password": password}
    )
    return response


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF]}


def refresh(client: TestClient, **headers: str) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/auth/refresh", headers={**csrf(client), **headers}
    )
    return response


def my_workspace(client: TestClient) -> str:
    workspace_id: str = client.get(f"{API}/auth/me").json()["memberships"][0]["workspace_id"]
    return workspace_id


def invite(client: TestClient, workspace_id: str, email: str, role: str) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/workspaces/{workspace_id}/invitations",
        json={"email": email, "role": role},
        headers=csrf(client),
    )
    return response


def accept(client: TestClient, token: str) -> httpx2.Response:
    response: httpx2.Response = client.post(
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


def set_cookie_headers(response: httpx2.Response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def member(new_client: ClientFactory, owner: TestClient, role: str) -> TestClient:
    """A second user who joined the owner's workspace with `role`."""
    other, email = new_client(), new_email()
    register(other, email)
    token = invite(owner, my_workspace(owner), email, role).json()["invitation_token"]
    assert accept(other, token).status_code == 200
    return other
