"""Two-device parity and recovery tests for cloud sync."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud.daemon as cloud_daemon
from cloud.api import build_app
from cloud.codec import pack_payload
from db import KontextDB


class _LocalCloudClient:
    def __init__(self, client: TestClient):
        self.client = client
        self._token: str | None = None

    def set_token(self, token: str | None) -> None:
        self._token = token.strip() if token else None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _json(self, response):
        if response.status_code >= 400:
            raise RuntimeError(f"cloud request failed ({response.status_code}): {response.text}")
        return response.json()

    def create_workspace(self, workspace_id: str, name: str, recovery_key_id: str,
                         existing_token: str | None = None) -> dict:
        payload = {
            "workspace_id": workspace_id,
            "name": name,
            "recovery_key_id": recovery_key_id,
        }
        if existing_token:
            payload["workspace_token"] = existing_token
        response = self.client.post("/v1/workspaces", json=payload)
        return self._json(response)

    def enroll_device(self, workspace_id: str, device_id: str, label: str,
                      device_class: str, device_public_key: str,
                      enrollment_code: str = "link") -> dict:
        response = self.client.post(
            "/v1/devices/enroll",
            json={
                "workspace_id": workspace_id,
                "enrollment_code": enrollment_code,
                "label": label,
                "device_class": device_class,
                "device_public_key": device_public_key,
                "device_id": device_id,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def push_history(self, workspace_id: str, items: list[dict]) -> dict:
        response = self.client.post(
            "/v1/sync/push",
            json={
                "workspace_id": workspace_id,
                "lane": "history",
                "items": items,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def pull_history(self, workspace_id: str, device_id: str,
                     after: str = "", limit: int = 500) -> dict:
        response = self.client.get(
            "/v1/sync/pull",
            params={
                "workspace_id": workspace_id,
                "device_id": device_id,
                "lane": "history",
                "after": after,
                "limit": limit,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def push_canonical(self, workspace_id: str, items: list[dict]) -> dict:
        response = self.client.post(
            "/v1/sync/push",
            json={
                "workspace_id": workspace_id,
                "lane": "canonical",
                "items": items,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def pull_canonical(self, workspace_id: str, device_id: str,
                       after: str = "", limit: int = 500) -> dict:
        response = self.client.get(
            "/v1/sync/pull",
            params={
                "workspace_id": workspace_id,
                "device_id": device_id,
                "lane": "canonical",
                "after": after,
                "limit": limit,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def revoke_device(self, workspace_id: str, device_id: str) -> dict:
        response = self.client.post(
            "/v1/devices/revoke",
            json={
                "workspace_id": workspace_id,
                "device_id": device_id,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def create_snapshot(self, workspace_id: str, device_id: str) -> dict:
        response = self.client.post(
            "/v1/snapshots/create",
            json={
                "workspace_id": workspace_id,
                "device_id": device_id,
            },
            headers=self._headers(),
        )
        return self._json(response)

    def pull_latest_snapshot(self, workspace_id: str, device_id: str) -> dict:
        response = self.client.get(
            "/v1/snapshots/latest",
            params={
                "workspace_id": workspace_id,
                "device_id": device_id,
            },
            headers=self._headers(),
        )
        return self._json(response)


@pytest.fixture
def server_db(tmp_path):
    d = KontextDB(str(tmp_path / "server.db"))
    yield d
    d.close()


@pytest.fixture
def api_client(server_db):
    return TestClient(build_app(server_db))


def _patch_cloud_client(monkeypatch, api_client: TestClient) -> None:
    monkeypatch.setattr(
        cloud_daemon,
        "CloudClient",
        lambda server_url, **_kwargs: _LocalCloudClient(api_client),
    )


def _link_first(device_db, api_client, keystore_root, workspace_id, device_id, label):
    return cloud_daemon.link_workspace(
        device_db,
        server_url="https://cloud.test",
        workspace_id=workspace_id,
        label=label,
        device_id=device_id,
        keystore_root=keystore_root,
    )


def _link_second(device_db, api_client, keystore_root, workspace_id, device_id, label, token):
    return cloud_daemon.link_workspace(
        device_db,
        server_url="https://cloud.test",
        workspace_id=workspace_id,
        label=label,
        device_id=device_id,
        workspace_token=token,
        keystore_root=keystore_root,
    )


def test_canonical_head_replays_across_devices(monkeypatch, api_client, tmp_path):
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    device_b = KontextDB(str(tmp_path / "device-b.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        device_a.append_canonical_revision(
            workspace_id="ws-1",
            object_id="obj-1",
            object_type="memory.entry",
            revision_id="rev-1",
            parent_revision=None,
            device_id="dev-a",
            payload=pack_payload({"fact": "Name: Alice"}),
            created_at="2026-04-16T10:00:00Z",
            accepted=True,
        )
        sync_result = cloud_daemon.sync_once(device_a)

        _link_second(device_b, api_client, tmp_path / "keys-b", "ws-1", "dev-b", "Laptop B", token)
        recover_result = cloud_daemon.recover_workspace(device_b)

        obj = device_b.conn.execute(
            "SELECT * FROM canonical_objects WHERE id = ?",
            ("obj-1",),
        ).fetchone()
        rev = device_b.conn.execute(
            "SELECT * FROM canonical_revisions WHERE id = ?",
            ("rev-1",),
        ).fetchone()
        cursor = device_b.conn.execute(
            "SELECT cursor FROM sync_cursors WHERE workspace_id = ? AND device_id = ? AND lane = ?",
            ("ws-1", "dev-b", "canonical"),
        ).fetchone()

        assert sync_result["canonical_pushed"] == 1
        assert recover_result["canonical_recovered"] == 1
        assert obj["head_revision"] == "rev-1"
        assert rev["device_id"] == "dev-a"
        assert cursor["cursor"] == "rev-1"
    finally:
        device_a.close()
        device_b.close()


def test_two_device_top_k_overlap_stays_above_threshold(monkeypatch, api_client, tmp_path):
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    device_b = KontextDB(str(tmp_path / "device-b.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        device_a.add_entry("project_cloud.md", "Kontext cloud sync status stays green", grade=9)
        device_a.add_entry("project_cloud.md", "Kontext recovery replay stays deterministic", grade=8)
        device_a.add_entry("project_cloud.md", "Kontext prompt history follows the workspace", grade=7)
        device_a.add_entry("project_cloud.md", "Unrelated local memory", grade=4)
        device_a.add_user_prompt("sess-a", "How do I sync Kontext memory?")
        device_a.add_tool_event("sess-a", "Edit", "Edited cloud/api.py", file_path="cloud/api.py", grade=6.0)

        sync_result = cloud_daemon.sync_once(device_a)

        _link_second(device_b, api_client, tmp_path / "keys-b", "ws-1", "dev-b", "Laptop B", token)
        recover_result = cloud_daemon.recover_workspace(device_b)

        top_a = [row["fact"] for row in device_a.search_entries("Kontext", limit=3)]
        top_b = [row["fact"] for row in device_b.search_entries("Kontext", limit=3)]
        overlap = len(set(top_a) & set(top_b)) / max(len(top_a), 1)

        assert sync_result["pushed"] >= 5
        assert recover_result["recovered"] >= 5
        assert overlap >= 1.0
    finally:
        device_a.close()
        device_b.close()


def test_revoked_device_cannot_pull_new_ops(monkeypatch, api_client, server_db, tmp_path):
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        server_db.revoke_device("ws-1", "dev-a")

        response = api_client.get(
            "/v1/sync/pull",
            params={
                "workspace_id": "ws-1",
                "device_id": "dev-a",
                "lane": "history",
                "after": "",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
    finally:
        device_a.close()


def test_revoked_device_cannot_silently_re_enroll(monkeypatch, api_client, server_db, tmp_path):
    """Finding 2: replaying enroll on a revoked device must NOT clear revoked_at."""
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        server_db.revoke_device("ws-1", "dev-a")

        replay = api_client.post(
            "/v1/devices/enroll",
            json={
                "workspace_id": "ws-1",
                "enrollment_code": "link",
                "label": "Laptop A",
                "device_class": "interactive",
                "device_public_key": "aa" * 32,
                "device_id": "dev-a",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert replay.status_code == 403
        row = server_db.conn.execute(
            "SELECT revoked_at FROM devices WHERE id = ?", ("dev-a",),
        ).fetchone()
        assert row["revoked_at"] is not None
    finally:
        device_a.close()


def test_restore_rebuilds_queryable_store(monkeypatch, api_client, tmp_path):
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    device_b = KontextDB(str(tmp_path / "device-b.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        device_a.add_entry("project_cloud.md", "Kontext restore rebuilds the local queryable store", grade=9)
        device_a.add_user_prompt("sess-a", "Can I restore cloud history on a fresh device?")
        device_a.add_tool_event("sess-a", "Edit", "Edited cloud/replay.py", file_path="cloud/replay.py", grade=6.0)
        cloud_daemon.sync_once(device_a)

        _link_second(device_b, api_client, tmp_path / "keys-b", "ws-1", "dev-b", "Laptop B", token)
        result = cloud_daemon.recover_workspace(device_b)

        entries = device_b.search_entries("restore", limit=5)
        prompts = device_b.search_prompts("fresh device", limit=5)
        events = device_b.get_tool_events(session_id="sess-a", limit=5)

        assert result["recovered"] >= 3
        assert any(row["fact"] == "Kontext restore rebuilds the local queryable store" for row in entries)
        assert any(row["content"] == "Can I restore cloud history on a fresh device?" for row in prompts)
        assert any(row["summary"] == "Edited cloud/replay.py" for row in events)
    finally:
        device_a.close()
        device_b.close()


def test_recover_uses_snapshot_bootstrap_before_tail_replay(monkeypatch, api_client, tmp_path):
    _patch_cloud_client(monkeypatch, api_client)

    device_a = KontextDB(str(tmp_path / "device-a.db"))
    device_b = KontextDB(str(tmp_path / "device-b.db"))
    try:
        status_a = _link_first(device_a, api_client, tmp_path / "keys-a", "ws-1", "dev-a", "Laptop A")
        token = status_a["workspace_token"]
        device_a.add_entry("project_cloud.md", "Kontext snapshot bootstrap keeps the base state", grade=9)
        device_a.append_canonical_revision(
            workspace_id="ws-1",
            object_id="obj-1",
            object_type="memory.entry",
            revision_id="rev-1",
            parent_revision=None,
            device_id="dev-a",
            payload=pack_payload({"fact": "Base state"}),
            created_at="2026-04-16T10:00:00Z",
            accepted=True,
        )
        cloud_daemon.sync_once(device_a)
        snapshot = cloud_daemon.create_snapshot(device_a)

        device_a.add_entry("project_cloud.md", "Kontext tail replay lands after the snapshot", grade=8)
        device_a.append_canonical_revision(
            workspace_id="ws-1",
            object_id="obj-1",
            object_type="memory.entry",
            revision_id="rev-2",
            parent_revision="rev-1",
            device_id="dev-a",
            payload=pack_payload({"fact": "Tail state"}),
            created_at="2026-04-16T10:01:00Z",
            accepted=True,
        )
        cloud_daemon.sync_once(device_a)

        _link_second(device_b, api_client, tmp_path / "keys-b", "ws-1", "dev-b", "Laptop B", token)
        result = cloud_daemon.recover_workspace(device_b)

        entry_facts = [row["fact"] for row in device_b.search_entries("Kontext", limit=10)]
        canonical_ids = [
            row["id"]
            for row in device_b.conn.execute(
                "SELECT id FROM canonical_revisions ORDER BY created_at ASC, id ASC"
            ).fetchall()
        ]

        assert snapshot["history_cursor"].startswith("op-")
        assert snapshot["canonical_cursor"] == "rev-1"
        assert result["snapshot_restored"] is True
        assert result["snapshot_id"] == snapshot["snapshot_id"]
        assert result["history_recovered"] == 1
        assert result["canonical_recovered"] == 1
        assert "Kontext snapshot bootstrap keeps the base state" in entry_facts
        assert "Kontext tail replay lands after the snapshot" in entry_facts
        assert canonical_ids == ["rev-1", "rev-2"]
    finally:
        device_a.close()
        device_b.close()
