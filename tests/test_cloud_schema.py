"""Tests for cloud sync schema and DB contracts."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.codec import unpack_payload
from db import KontextDB, LATEST_SCHEMA_VERSION


@pytest.fixture
def db(tmp_path):
    d = KontextDB(str(tmp_path / "test.db"))
    yield d
    d.close()


def _seed_workspace(db: KontextDB) -> None:
    db.create_workspace("ws-1", "Primary", "recovery-1")
    db.upsert_sync_manifest(
        workspace_id="ws-1",
        schema_version=12,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        ranking_version="v1",
        prompt_routing_version="v1",
    )
    db.register_device(
        device_id="dev-1",
        workspace_id="ws-1",
        label="Laptop",
        device_class="interactive",
        public_key=b"pubkey-1",
    )


def _link_db(db: KontextDB) -> None:
    state_path = Path(db.db_path).with_suffix(".cloud.json")
    state_path.write_text(
        json.dumps(
            {
                "server_url": "http://cloud.test",
                "workspace_id": "ws-1",
                "device_id": "dev-1",
                "label": "Laptop",
                "device_class": "interactive",
            }
        ),
        encoding="utf-8",
    )


class TestCloudSchema:
    def test_latest_schema_version_includes_cloud_migrations(self):
        assert LATEST_SCHEMA_VERSION >= 12

    def test_cloud_tables_exist(self, db):
        names = set(db.list_tables())
        expected = {
            "workspaces",
            "devices",
            "sync_manifests",
            "history_ops",
            "sync_cursors",
            "canonical_objects",
            "canonical_revisions",
            "snapshots",
        }
        assert expected <= names


class TestWorkspaceContracts:
    def test_create_workspace_and_manifest(self, db):
        _seed_workspace(db)

        workspace = db.conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", ("ws-1",)
        ).fetchone()
        manifest = db.conn.execute(
            "SELECT * FROM sync_manifests WHERE workspace_id = ?", ("ws-1",)
        ).fetchone()

        assert workspace["name"] == "Primary"
        assert manifest["schema_version"] == 12
        assert manifest["ranking_version"] == "v1"

    def test_register_device_persists_metadata(self, db):
        _seed_workspace(db)

        row = db.conn.execute(
            "SELECT * FROM devices WHERE id = ?", ("dev-1",)
        ).fetchone()

        assert row["workspace_id"] == "ws-1"
        assert row["label"] == "Laptop"
        assert row["device_class"] == "interactive"
        assert row["public_key"] == b"pubkey-1"


class TestHistoryOps:
    def test_append_history_op_is_idempotent(self, db):
        _seed_workspace(db)

        db.append_history_op(
            op_id="op-1",
            workspace_id="ws-1",
            device_id="dev-1",
            op_kind="session.closed",
            entity_type="session",
            entity_id="sess-1",
            payload=b"payload-1",
            created_at="2026-04-16T10:00:00Z",
        )
        db.append_history_op(
            op_id="op-1",
            workspace_id="ws-1",
            device_id="dev-1",
            op_kind="session.closed",
            entity_type="session",
            entity_id="sess-1",
            payload=b"payload-1",
            created_at="2026-04-16T10:00:00Z",
        )

        count = db.conn.execute(
            "SELECT COUNT(*) FROM history_ops WHERE id = ?", ("op-1",)
        ).fetchone()[0]
        assert count == 1

    def test_list_history_ops_since_cursor(self, db):
        _seed_workspace(db)

        db.append_history_op("op-1", "ws-1", "dev-1", "prompt.logged", "prompt", "prompt-1", b"one", "2026-04-16T10:00:00Z")
        db.append_history_op("op-2", "ws-1", "dev-1", "prompt.logged", "prompt", "prompt-2", b"two", "2026-04-16T10:01:00Z")

        all_rows = db.list_history_ops_since("ws-1", "")
        later_rows = db.list_history_ops_since("ws-1", "op-1")

        assert [row["id"] for row in all_rows] == ["op-1", "op-2"]
        assert [row["id"] for row in later_rows] == ["op-2"]

    def test_advance_sync_cursor_upserts(self, db):
        _seed_workspace(db)

        db.advance_sync_cursor("ws-1", "dev-1", "history", "op-1")
        db.advance_sync_cursor("ws-1", "dev-1", "history", "op-2")

        row = db.conn.execute(
            "SELECT cursor FROM sync_cursors WHERE workspace_id = ? AND device_id = ? AND lane = ?",
            ("ws-1", "dev-1", "history"),
        ).fetchone()

        assert row["cursor"] == "op-2"

    def test_add_entry_emits_history_op_when_linked(self, db):
        _seed_workspace(db)
        _link_db(db)

        entry_id = db.add_entry(
            file="user_identity.md",
            fact="Name: Alice",
            source="[test]",
            grade=9,
            tier="active",
        )

        row = db.conn.execute(
            "SELECT * FROM history_ops WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            ("ws-1",),
        ).fetchone()

        assert entry_id > 0
        assert row["op_kind"] == "entry.written"
        assert row["entity_type"] == "entry"
        assert row["entity_id"] == str(entry_id)
        assert unpack_payload(row["payload"]) == {
            "file": "user_identity.md",
            "fact": "Name: Alice",
            "source": "[test]",
            "grade": 9,
            "tier": "active",
        }

    def test_save_session_emits_history_op_when_linked(self, db):
        _seed_workspace(db)
        _link_db(db)

        db.save_session(
            project="Test Project",
            status="in progress",
            next_step="ship sync",
            key_decisions="keep sqlite local",
            summary="session summary",
            files_touched="db.py",
            workspace="C:/repos/kontext",
        )

        row = db.conn.execute(
            "SELECT * FROM history_ops WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            ("ws-1",),
        ).fetchone()

        assert row["op_kind"] == "session.saved"
        assert row["entity_type"] == "session"
        assert unpack_payload(row["payload"]) == {
            "project": "Test Project",
            "status": "in progress",
            "next_step": "ship sync",
            "key_decisions": "keep sqlite local",
            "summary": "session summary",
            "files_touched": "db.py",
            "workspace": "c:/repos/kontext",
        }

    def test_add_user_prompt_emits_history_op_when_linked(self, db):
        _seed_workspace(db)
        _link_db(db)

        prompt_id = db.add_user_prompt(session_id="hook-1", content="What changed?")

        row = db.conn.execute(
            "SELECT * FROM history_ops WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            ("ws-1",),
        ).fetchone()

        assert prompt_id > 0
        assert row["op_kind"] == "prompt.logged"
        assert row["entity_type"] == "prompt"
        assert row["entity_id"] == str(prompt_id)
        assert unpack_payload(row["payload"]) == {
            "session_id": "hook-1",
            "content": "What changed?",
        }

    def test_add_tool_event_emits_history_op_when_linked(self, db):
        _seed_workspace(db)
        _link_db(db)

        event_id = db.add_tool_event(
            session_id="hook-1",
            tool_name="Edit",
            summary="Edited db.py",
            file_path="db.py",
            grade=6.0,
        )

        row = db.conn.execute(
            "SELECT * FROM history_ops WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            ("ws-1",),
        ).fetchone()

        assert event_id > 0
        assert row["op_kind"] == "tool.logged"
        assert row["entity_type"] == "tool_event"
        assert row["entity_id"] == str(event_id)
        assert unpack_payload(row["payload"]) == {
            "session_id": "hook-1",
            "tool_name": "Edit",
            "summary": "Edited db.py",
            "file_path": "db.py",
            "grade": 6.0,
        }


class TestCanonicalRevisions:
    def test_append_canonical_revision_creates_object_and_head(self, db):
        _seed_workspace(db)

        db.append_canonical_revision(
            workspace_id="ws-1",
            object_id="obj-1",
            object_type="memory.entry",
            revision_id="rev-1",
            parent_revision=None,
            device_id="dev-1",
            payload=b"canonical-1",
            created_at="2026-04-16T10:00:00Z",
            accepted=True,
        )

        obj = db.conn.execute(
            "SELECT * FROM canonical_objects WHERE id = ?", ("obj-1",)
        ).fetchone()
        rev = db.conn.execute(
            "SELECT * FROM canonical_revisions WHERE id = ?", ("rev-1",)
        ).fetchone()

        assert obj["workspace_id"] == "ws-1"
        assert obj["object_type"] == "memory.entry"
        assert obj["head_revision"] == "rev-1"
        assert rev["accepted"] == 1

    def test_unaccepted_revision_does_not_move_head(self, db):
        _seed_workspace(db)

        db.append_canonical_revision("ws-1", "obj-1", "memory.entry", "rev-1", None, "dev-1", b"canonical-1", "2026-04-16T10:00:00Z", True)
        db.append_canonical_revision("ws-1", "obj-1", "memory.entry", "rev-2", "rev-1", "dev-1", b"canonical-2", "2026-04-16T10:01:00Z", False)

        obj = db.conn.execute(
            "SELECT * FROM canonical_objects WHERE id = ?", ("obj-1",)
        ).fetchone()
        rev = db.conn.execute(
            "SELECT * FROM canonical_revisions WHERE id = ?", ("rev-2",)
        ).fetchone()

        assert obj["head_revision"] == "rev-1"
        assert rev["accepted"] == 0