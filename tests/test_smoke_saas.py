"""Smoke check for the SaaS test fixtures."""
from tests.conftest import login, register


def test_register_and_me(registered):
    assert registered["email"] == "owner@example.com"
    assert registered["workspace_role"] == "owner"


def test_me_endpoint(registered, app_client):
    r = app_client.get("/auth/me", headers=registered["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "owner@example.com"


def test_second_workspace_is_isolated(registered, app_client):
    other = register(app_client, "other@example.com", workspace_name="Other Co")
    assert other["workspace_id"] != registered["workspace_id"]
    r = app_client.get("/workspaces/current", headers=registered["headers"])
    assert r.json()["name"] == "Acme"


def test_login(registered, app_client):
    s = login(app_client, "owner@example.com")
    assert s["email"] == "owner@example.com"
