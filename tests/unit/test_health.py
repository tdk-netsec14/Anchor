"""Phase 1 smoke tests: the app boots and the health contract holds."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.main import create_app


def test_health_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "anchor"
    assert "version" in body
    assert "uptime_seconds" in body


def test_health_requires_no_authentication() -> None:
    """The only unauthenticated route; orchestrators depend on this."""
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Authorization": ""})

    assert response.status_code == 200


def test_response_carries_request_id() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) == 36  # uuid4 string


def test_supplied_request_id_is_echoed_back() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_swagger_schema_documents_health() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()

    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_docs_endpoint_is_served() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 200
