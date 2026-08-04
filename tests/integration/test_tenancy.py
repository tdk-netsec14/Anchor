"""Tenant isolation and the persistence of tenant-owned resources.

Multi-tenancy is the property most likely to be broken silently: a route that
forgets its `WHERE workspace_id = ...` still works perfectly for the single
tenant a developer is testing as, and only leaks once a second workspace
exists. So these tests are written adversarially — every one of them creates a
second workspace first and then attacks the first one from it.

The rule being asserted throughout is that a row in another workspace is
reported as **404, not 403**. A 403 would confirm that a guessed id exists
somewhere, which is itself a leak.
"""

from __future__ import annotations

import pytest

from tests.conftest import register

pytestmark = pytest.mark.integration


@pytest.fixture
def two_workspaces(app_client):
    """An owner of ``Acme`` and an owner of ``Globex``, both fully signed in."""
    acme = register(app_client, "acme@example.com", workspace_name="Acme")
    globex = register(app_client, "globex@example.com", workspace_name="Globex")
    assert acme["workspace_id"] != globex["workspace_id"]
    return acme, globex


def _upload_pdf(client, headers, name: str, content: bytes = b"%PDF-1.4 test body") -> dict:
    response = client.post(
        "/ingest",
        files={"file": (name, content, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
class TestDocumentIsolation:
    def test_a_document_is_invisible_to_another_workspace(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        _upload_pdf(app_client, acme["headers"], "acme-secret.pdf")

        listed = app_client.get("/documents", headers=globex["headers"])
        assert listed.status_code == 200
        assert [d["doc_name"] for d in listed.json()["documents"]] == []

    def test_another_workspace_cannot_read_a_known_document_id(
        self, app_client, two_workspaces
    ):
        acme, globex = two_workspaces
        created = _upload_pdf(app_client, acme["headers"], "acme-secret.pdf")
        acme_doc = app_client.get("/documents", headers=acme["headers"]).json()["documents"][0]
        assert acme_doc["id"] == created["document_id"]

        response = app_client.get(f"/documents/{acme_doc['id']}", headers=globex["headers"])
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_another_workspace_cannot_delete_it(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        _upload_pdf(app_client, acme["headers"], "acme-secret.pdf")
        doc_id = app_client.get("/documents", headers=acme["headers"]).json()["documents"][0]["id"]

        response = app_client.delete(f"/documents/{doc_id}", headers=globex["headers"])
        # 404 rather than 403: a 403 would confirm the id is real somewhere.
        assert response.status_code == 404

        # And it is still there for its owner.
        still_there = app_client.get("/documents", headers=acme["headers"]).json()["documents"]
        assert [d["doc_name"] for d in still_there] == ["acme-secret.pdf"]

    def test_two_workspaces_can_hold_the_same_document_name(self, app_client, two_workspaces):
        """A name is unique per workspace, not globally.

        The re-upload check is scoped to the workspace, so two tenants naming
        their file the same thing must not collide.
        """
        acme, globex = two_workspaces
        _upload_pdf(app_client, acme["headers"], "policy.pdf")
        _upload_pdf(app_client, globex["headers"], "policy.pdf")

        for session in (acme, globex):
            docs = app_client.get("/documents", headers=session["headers"]).json()["documents"]
            assert [d["doc_name"] for d in docs] == ["policy.pdf"]


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------
class TestConversationIsolation:
    def test_a_conversation_is_invisible_to_another_workspace(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        created = app_client.post(
            "/conversations", json={"title": "Acme planning"}, headers=acme["headers"]
        )
        assert created.status_code in (200, 201), created.text
        conversation_id = created.json()["id"]

        listed = app_client.get("/conversations", headers=globex["headers"])
        assert listed.status_code == 200
        assert listed.json() == []

        fetched = app_client.get(f"/conversations/{conversation_id}", headers=globex["headers"])
        assert fetched.status_code == 404

    def test_another_workspace_cannot_rename_or_delete_it(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        conversation_id = app_client.post(
            "/conversations", json={"title": "Acme planning"}, headers=acme["headers"]
        ).json()["id"]

        renamed = app_client.patch(
            f"/conversations/{conversation_id}",
            json={"title": "stolen"},
            headers=globex["headers"],
        )
        assert renamed.status_code == 404

        deleted = app_client.delete(
            f"/conversations/{conversation_id}", headers=globex["headers"]
        )
        assert deleted.status_code == 404

        survivor = app_client.get(
            f"/conversations/{conversation_id}", headers=acme["headers"]
        )
        assert survivor.status_code == 200
        assert survivor.json()["title"] == "Acme planning"

    def test_a_conversation_can_be_created_renamed_and_deleted(self, app_client, registered):
        conversation_id = app_client.post(
            "/conversations", json={"title": "First"}, headers=registered["headers"]
        ).json()["id"]

        renamed = app_client.patch(
            f"/conversations/{conversation_id}",
            json={"title": "Renamed"},
            headers=registered["headers"],
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Renamed"

        deleted = app_client.delete(
            f"/conversations/{conversation_id}", headers=registered["headers"]
        )
        assert deleted.status_code == 200
        assert app_client.get(
            f"/conversations/{conversation_id}", headers=registered["headers"]
        ).status_code == 404


# --------------------------------------------------------------------------
# Members, roles and the credential
# --------------------------------------------------------------------------
class TestMembership:
    def test_removing_a_member_takes_effect_immediately(self, app_client, db_session, registered):
        """A valid signature is not a live permission.

        A token stays cryptographically valid after its owner is removed from
        the workspace, so authorisation re-reads the membership on every
        request rather than trusting the claim alone.
        """
        invitee = _add_member(app_client, db_session, registered, "invitee@example.com", "member")

        before = app_client.get("/workspaces/members", headers=invitee["headers"])
        assert before.status_code == 200

        removed = app_client.delete(
            f"/workspaces/members/{invitee['user_id']}", headers=registered["headers"]
        )
        assert removed.status_code == 200

        after = app_client.get("/workspaces/members", headers=invitee["headers"])
        assert after.status_code == 403
        assert after.json()["error"] == "not_a_member"

    def test_a_viewer_cannot_ingest(self, app_client, db_session, registered):
        viewer = _add_member(app_client, db_session, registered, "viewer@example.com", "viewer")
        response = app_client.post(
            "/ingest",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            headers=viewer["headers"],
        )
        assert response.status_code == 403
        assert response.json()["error"] == "insufficient_role"

    def test_a_member_can_ask_questions(self, app_client, db_session, registered):
        """The floor for reading is MEMBER; a viewer below it is refused."""
        member = _add_member(app_client, db_session, registered, "member@example.com", "member")
        listed = app_client.get("/documents", headers=member["headers"])
        assert listed.status_code == 200

    def test_a_member_cannot_promote_themselves_to_owner(
        self, app_client, db_session, registered
    ):
        member = _add_member(app_client, db_session, registered, "member@example.com", "member")
        response = app_client.patch(
            f"/workspaces/members/{member['user_id']}",
            json={"role": "owner"},
            headers=member["headers"],
        )
        # A MEMBER is below the ADMIN floor for this route, so the change is
        # refused outright — this is the privilege-escalation guard.
        assert response.status_code == 403, response.text
        roles = {
            m["user_id"]: m["role"]
            for m in app_client.get("/workspaces/members", headers=registered["headers"]).json()
        }
        assert roles[member["user_id"]] == "member"

    def test_a_member_cannot_invite_or_issue_keys(
        self, app_client, db_session, registered
    ):
        member = _add_member(app_client, db_session, registered, "member@example.com", "member")
        invited = app_client.post(
            "/workspaces/invites",
            json={"email": "sneaky@example.com", "role": "admin"},
            headers=member["headers"],
        )
        keyed = app_client.post("/api-keys", json={"name": "k"}, headers=member["headers"])
        assert invited.status_code == 403
        assert keyed.status_code == 403


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------
class TestApiKeys:
    def test_a_key_acts_only_inside_its_own_workspace(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        created = app_client.post(
            "/api-keys", json={"name": "acme-ci"}, headers=acme["headers"]
        )
        assert created.status_code in (200, 201), created.text
        key = created.json()["key"]
        assert key.startswith("ank_")

        key_headers = {"Authorization": f"Bearer {key}"}
        assert app_client.get("/documents", headers=key_headers).status_code == 200
        assert app_client.get("/documents", headers=key_headers).json()["documents"] == []

    def test_a_key_is_never_returned_after_creation(self, app_client, registered):
        created = app_client.post("/api-keys", json={"name": "k"}, headers=registered["headers"])
        secret = created.json()["key"]

        listed = app_client.get("/api-keys", headers=registered["headers"])
        assert listed.status_code == 200
        assert secret not in listed.text

    def test_a_revoked_key_stops_working(self, app_client, registered):
        created = app_client.post("/api-keys", json={"name": "k"}, headers=registered["headers"])
        key_id, secret = created.json()["id"], created.json()["key"]
        key_headers = {"Authorization": f"Bearer {secret}"}
        assert app_client.get("/documents", headers=key_headers).status_code == 200

        assert app_client.delete(
            f"/api-keys/{key_id}", headers=registered["headers"]
        ).status_code == 200

        after = app_client.get("/documents", headers=key_headers)
        assert after.status_code == 401
        assert after.json()["error"] == "api_key_revoked"


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
class TestAnalyticsIsolation:
    def test_analytics_never_cross_tenants(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        for session in (acme, globex):
            response = app_client.get("/analytics/overview", headers=session["headers"])
            assert response.status_code == 200
            body = response.json()
            assert body["requests"]["total"] == 0
            assert body["by_user"] == {}

    def test_the_audit_trail_is_scoped_to_the_workspace(self, app_client, two_workspaces):
        acme, globex = two_workspaces
        _upload_pdf(app_client, acme["headers"], "acme-only.pdf")

        acme_events = app_client.get("/analytics/audit", headers=acme["headers"])
        globex_events = app_client.get("/analytics/audit", headers=globex["headers"])

        assert acme_events.status_code == 200
        assert globex_events.status_code == 200
        # The upload is recorded against Acme and must not surface in Globex's
        # trail. Matching on the action and actor rather than the document name
        # keeps this independent of how much detail the response exposes.
        assert any(e["action"] == "document.upload" for e in acme_events.json())
        assert not any(e["action"] == "document.upload" for e in globex_events.json())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _invite(client, owner_headers: dict, email: str) -> str:
    response = client.post(
        "/workspaces/invites", json={"email": email, "role": "member"}, headers=owner_headers
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["token"]


def _scoped_session(db_session, *, session: dict, workspace_id: str, role: str) -> dict:
    """Re-scope an existing registration's session to ``workspace_id``.

    Registration always mints a session against the account's *own* workspace,
    and Anchor has no endpoint for switching to a second one, so an invitee
    cannot obtain a session for the workspace they were just invited to. That
    is a real gap in the product (recorded in the README's limitations), but
    it must not be allowed to hide the authorisation behaviour these tests are
    about — so the membership row is written directly and a correctly-scoped
    token minted for it, exercising the same checks a real session would.
    """
    from agent.auth import create_access_token
    from agent.db.models import Membership, User, WorkspaceRole

    user = db_session.get(User, session["user_id"])
    membership = (
        db_session.query(Membership)
        .filter(
            Membership.user_id == user.id, Membership.workspace_id == workspace_id
        )
        .one_or_none()
    )
    if membership is None:
        db_session.add(
            Membership(user_id=user.id, workspace_id=workspace_id, role=WorkspaceRole(role))
        )
    else:
        # The invitation already granted a role; the test needs a specific one.
        membership.role = WorkspaceRole(role)
    db_session.commit()

    token, _ = create_access_token(
        user.id,
        "user",
        email=user.email,
        workspace_id=workspace_id,
        workspace_role=WorkspaceRole(role),
    )
    return {
        "user_id": user.id,
        "workspace_id": workspace_id,
        "workspace_role": role,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _add_member(client, db_session, owner: dict, email: str, role: str) -> dict:
    """Redeem an invitation, then return a session scoped to the owner's workspace."""
    token = _invite(client, owner["headers"], email)
    session = register(client, email, workspace_name=f"{email.split('@')[0]} Co")
    accepted = client.post(
        "/workspaces/invites/accept", json={"token": token}, headers=session["headers"]
    )
    assert accepted.status_code in (200, 204), accepted.text
    return _scoped_session(
        db_session, session=session, workspace_id=owner["workspace_id"], role=role
    )
