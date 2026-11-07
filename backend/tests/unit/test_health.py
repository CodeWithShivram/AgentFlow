from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_returns_404() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
