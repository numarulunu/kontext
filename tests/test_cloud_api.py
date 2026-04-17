"""Tests for cloud control plane API."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.api import build_app
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    d = KontextDB(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def client(db):
    return TestClient(build_app(db))


def _create_workspace(client: TestClient) -> str:
    response = client.post(
        "/v1/workspaces",
        json={
            "workspace_id": "ws-1",
            "name": "Primary",
            "recovery_key_id": "recovery-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_token"]
    return body["workspace_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_auto_creates_local_workspace_on_empty_db(client, db):
    """On a fresh DB with no cloud workspace, the dashboard should still
    render by creating a synthetic 'local' workspace on first hit."""
    assert db.conn.execute("SELECT count(*) AS n FROM workspaces").fetchone()["n"] == 0

    response = client.get("/dashboard")
    assert response.status_code == 200

    row = db.conn.execute("SELECT id, name FROM workspaces").fetchone()
    assert row["id"] == "local"
    assert row["name"] == "Local"


def test_dashboard_prefers_cloud_workspace_over_local(client, db):
    """If both a cloud workspace and the local one exist, resolve picks cloud."""
    _create_workspace(client)  # creates ws-1
    db.create_workspace(workspace_id="local", name="Local", recovery_key_id="local")

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"ws-1" in response.content or b"Primary" in response.content


def test_create_workspace_endpoint_persists_row(client, db):
    _create_workspace(client)

    row = db.conn.execute("SELECT * FROM workspaces WHERE id = ?", ("ws-1",)).fetchone()

    assert row["name"] == "Primary"
    assert row["api_token_hash"]
    assert row["api_token_salt"]


def test_create_workspace_issues_single_token(client):
    first = _create_workspace(client)

    duplicate = client.post(
        "/v1/workspaces",
        json={
            "workspace_id": "ws-1",
            "name": "Primary",
            "recovery_key_id": "recovery-1",
        },
    )
    assert duplicate.status_code == 409

    rebind = client.post(
        "/v1/workspaces",
        json={
            "workspace_id": "ws-1",
            "name": "Primary",
            "recovery_key_id": "recovery-1",
            "workspace_token": first,
        },
    )
    assert rebind.status_code == 200
    assert "workspace_token" not in rebind.json()


def test_missing_token_is_rejected(client):
    _create_workspace(client)

    response = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Laptop",
            "device_class": "interactive",
            "device_public_key": "pubkey-1",
            "device_id": "dev-1",
        },
    )

    assert response.status_code == 401


def test_wrong_token_is_rejected(client):
    _create_workspace(client)

    response = client.get(
        "/v1/sync/pull",
        params={"workspace_id": "ws-1", "device_id": "dev-1", "lane": "history"},
        headers=_auth("not-the-right-token"),
    )

    assert response.status_code == 401


def test_enroll_third_interactive_device_rejected(client):
    token = _create_workspace(client)

    for idx in range(1, 3):
        response = client.post(
            "/v1/devices/enroll",
            json={
                "workspace_id": "ws-1",
                "enrollment_code": "abc123",
                "label": f"Laptop {idx}",
                "device_class": "interactive",
                "device_public_key": f"pubkey-{idx}",
                "device_id": f"dev-{idx}",
            },
            headers=_auth(token),
        )
        assert response.status_code == 200

    blocked = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Laptop 3",
            "device_class": "interactive",
            "device_public_key": "pubkey-3",
            "device_id": "dev-3",
        },
        headers=_auth(token),
    )

    assert blocked.status_code == 409


def test_enroll_second_server_device_rejected(client):
    token = _create_workspace(client)

    first = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Server 1",
            "device_class": "server",
            "device_public_key": "pubkey-server-1",
            "device_id": "srv-1",
        },
        headers=_auth(token),
    )
    second = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Server 2",
            "device_class": "server",
            "device_public_key": "pubkey-server-2",
            "device_id": "srv-2",
        },
        headers=_auth(token),
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_push_canonical_revision_and_pull_after_cursor(client):
    token = _create_workspace(client)
    enroll = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Laptop",
            "device_class": "interactive",
            "device_public_key": "pubkey-1",
            "device_id": "dev-1",
        },
        headers=_auth(token),
    )
    assert enroll.status_code == 200

    push = client.post(
        "/v1/sync/push",
        json={
            "workspace_id": "ws-1",
            "lane": "canonical",
            "items": [
                {
                    "op_id": "rev-1",
                    "device_id": "dev-1",
                    "op_kind": "canonical.revision",
                    "entity_type": "memory.entry",
                    "entity_id": "obj-1",
                    "created_at": "2026-04-16T10:00:00Z",
                    "payload": {"fact": "Name: Alice"},
                    "parent_revision": None,
                    "accepted": True,
                },
                {
                    "op_id": "rev-2",
                    "device_id": "dev-1",
                    "op_kind": "canonical.revision",
                    "entity_type": "memory.entry",
                    "entity_id": "obj-1",
                    "created_at": "2026-04-16T10:01:00Z",
                    "payload": {"fact": "Name: Bob"},
                    "parent_revision": "rev-1",
                    "accepted": False,
                },
            ],
        },
        headers=_auth(token),
    )

    assert push.status_code == 200

    full = client.get(
        "/v1/sync/pull",
        params={"workspace_id": "ws-1", "device_id": "dev-1", "lane": "canonical", "after": ""},
        headers=_auth(token),
    )
    after = client.get(
        "/v1/sync/pull",
        params={"workspace_id": "ws-1", "device_id": "dev-1", "lane": "canonical", "after": "rev-1"},
        headers=_auth(token),
    )

    assert [item["op_id"] for item in full.json()["items"]] == ["rev-1", "rev-2"]
    assert [item["op_id"] for item in after.json()["items"]] == ["rev-2"]
    assert full.json()["items"][0]["accepted"] is True
    assert full.json()["items"][1]["parent_revision"] == "rev-1"


def test_push_history_op_and_pull_after_cursor(client):
    token = _create_workspace(client)
    enroll = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Laptop",
            "device_class": "interactive",
            "device_public_key": "pubkey-1",
            "device_id": "dev-1",
        },
        headers=_auth(token),
    )
    assert enroll.status_code == 200

    push = client.post(
        "/v1/sync/push",
        json={
            "workspace_id": "ws-1",
            "lane": "history",
            "items": [
                {
                    "op_id": "op-1",
                    "device_id": "dev-1",
                    "op_kind": "prompt.logged",
                    "entity_type": "prompt",
                    "entity_id": "prompt-1",
                    "created_at": "2026-04-16T10:00:00Z",
                    "payload": {"content": "one"},
                },
                {
                    "op_id": "op-2",
                    "device_id": "dev-1",
                    "op_kind": "prompt.logged",
                    "entity_type": "prompt",
                    "entity_id": "prompt-2",
                    "created_at": "2026-04-16T10:01:00Z",
                    "payload": {"content": "two"},
                },
            ],
        },
        headers=_auth(token),
    )

    assert push.status_code == 200

    full = client.get(
        "/v1/sync/pull",
        params={"workspace_id": "ws-1", "device_id": "dev-1", "lane": "history", "after": ""},
        headers=_auth(token),
    )
    after = client.get(
        "/v1/sync/pull",
        params={"workspace_id": "ws-1", "device_id": "dev-1", "lane": "history", "after": "op-1"},
        headers=_auth(token),
    )

    assert [item["op_id"] for item in full.json()["items"]] == ["op-1", "op-2"]
    assert [item["op_id"] for item in after.json()["items"]] == ["op-2"]


def test_create_snapshot_and_pull_latest_snapshot_payload(client):
    token = _create_workspace(client)
    enroll = client.post(
        "/v1/devices/enroll",
        json={
            "workspace_id": "ws-1",
            "enrollment_code": "abc123",
            "label": "Laptop",
            "device_class": "interactive",
            "device_public_key": "pubkey-1",
            "device_id": "dev-1",
        },
        headers=_auth(token),
    )
    assert enroll.status_code == 200

    history_push = client.post(
        "/v1/sync/push",
        json={
            "workspace_id": "ws-1",
            "lane": "history",
            "items": [
                {
                    "op_id": "op-1",
                    "device_id": "dev-1",
                    "op_kind": "entry.written",
                    "entity_type": "entry",
                    "entity_id": "entry-1",
                    "created_at": "2026-04-16T10:00:00Z",
                    "payload": {
                        "file": "user_identity.md",
                        "fact": "Name: Alice",
                        "source": "[test]",
                        "grade": 9,
                        "tier": "active",
                    },
                }
            ],
        },
        headers=_auth(token),
    )
    canonical_push = client.post(
        "/v1/sync/push",
        json={
            "workspace_id": "ws-1",
            "lane": "canonical",
            "items": [
                {
                    "op_id": "rev-1",
                    "device_id": "dev-1",
                    "op_kind": "canonical.revision",
                    "entity_type": "memory.entry",
                    "entity_id": "obj-1",
                    "created_at": "2026-04-16T10:01:00Z",
                    "payload": {"fact": "Name: Alice"},
                    "parent_revision": None,
                    "accepted": True,
                }
            ],
        },
        headers=_auth(token),
    )

    assert history_push.status_code == 200
    assert canonical_push.status_code == 200

    created = client.post(
        "/v1/snapshots/create",
        json={
            "workspace_id": "ws-1",
            "device_id": "dev-1",
        },
        headers=_auth(token),
    )
    latest = client.get(
        "/v1/snapshots/latest",
        params={
            "workspace_id": "ws-1",
            "device_id": "dev-1",
        },
        headers=_auth(token),
    )

    assert created.status_code == 200
    assert latest.status_code == 200

    snapshot = latest.json()["snapshot"]

    assert snapshot["workspace_id"] == "ws-1"
    assert snapshot["history_cursor"] == "op-1"
    assert snapshot["canonical_cursor"] == "rev-1"
    assert any(row["fact"] == "Name: Alice" for row in snapshot["entries"])
    assert snapshot["canonical_objects"][0]["head_revision"] == "rev-1"
