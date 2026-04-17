"""Tests for cloud replay helpers."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.codec import unpack_payload
from cloud.models import HistoryEnvelope
from cloud.replay import apply_canonical_revision, apply_history_op
from db import KontextDB


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
    db.register_device("dev-1", "ws-1", "Laptop", "interactive", b"pubkey-1")


def test_apply_history_op_is_idempotent(db):
    _seed_workspace(db)
    envelope = HistoryEnvelope(
        op_id="op-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="prompt.logged",
        entity_type="prompt",
        entity_id="prompt-1",
        created_at="2026-04-16T10:00:00Z",
        payload={"content": "hello"},
    )

    apply_history_op(db, envelope)
    apply_history_op(db, envelope)

    row = db.conn.execute("SELECT * FROM history_ops WHERE id = ?", ("op-1",)).fetchone()
    count = db.conn.execute("SELECT COUNT(*) FROM history_ops WHERE id = ?", ("op-1",)).fetchone()[0]

    assert count == 1
    assert unpack_payload(row["payload"]) == {"content": "hello"}
    assert row["applied_at"] is not None


def test_apply_history_op_replays_entry_writes_into_entries(db):
    _seed_workspace(db)
    envelope = HistoryEnvelope(
        op_id="op-entry-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="entry.written",
        entity_type="entry",
        entity_id="entry-1",
        created_at="2026-04-16T10:02:00Z",
        payload={
            "file": "user_identity.md",
            "fact": "Name: Alice",
            "source": "[remote]",
            "grade": 9,
            "tier": "active",
        },
    )

    apply_history_op(db, envelope)
    apply_history_op(db, envelope)

    entries = db.get_entries(file="user_identity.md")
    assert len(entries) == 1
    assert entries[0]["fact"] == "Name: Alice"
    assert entries[0]["source"] == "[remote]"


def test_apply_history_op_replays_session_saves_into_sessions(db):
    _seed_workspace(db)
    envelope = HistoryEnvelope(
        op_id="op-session-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="session.saved",
        entity_type="session",
        entity_id="session-1",
        created_at="2026-04-16T10:03:00Z",
        payload={
            "project": "Kontext",
            "status": "syncing",
            "next_step": "verify pull",
            "key_decisions": "keep sqlite local",
            "summary": "cloud session",
            "files_touched": "sync.py",
            "workspace": "C:/repos/kontext",
        },
    )

    apply_history_op(db, envelope)
    apply_history_op(db, envelope)

    session = db.get_latest_session(workspace="C:/repos/kontext")
    assert session is not None
    assert session["project"] == "Kontext"
    assert session["status"] == "syncing"
    assert session["summary"] == "cloud session"


def test_apply_history_op_replays_prompt_logs_into_user_prompts(db):
    _seed_workspace(db)
    envelope = HistoryEnvelope(
        op_id="op-prompt-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="prompt.logged",
        entity_type="prompt",
        entity_id="prompt-1",
        created_at="2026-04-16T10:04:00Z",
        payload={
            "session_id": "hook-1",
            "content": "What changed?",
        },
    )

    apply_history_op(db, envelope)
    apply_history_op(db, envelope)

    prompts = db.search_prompts(query="What changed?", limit=10)
    assert len(prompts) == 1
    assert prompts[0]["session_id"] == "hook-1"
    assert prompts[0]["content"] == "What changed?"


def test_apply_history_op_replays_tool_logs_into_tool_events(db):
    _seed_workspace(db)
    envelope = HistoryEnvelope(
        op_id="op-tool-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="tool.logged",
        entity_type="tool_event",
        entity_id="tool-1",
        created_at="2026-04-16T10:05:00Z",
        payload={
            "session_id": "hook-1",
            "tool_name": "Edit",
            "summary": "Edited db.py",
            "file_path": "db.py",
            "grade": 6.0,
        },
    )

    apply_history_op(db, envelope)
    apply_history_op(db, envelope)

    events = db.get_tool_events(session_id="hook-1", limit=10)
    assert len(events) == 1
    assert events[0]["tool_name"] == "Edit"
    assert events[0]["summary"] == "Edited db.py"
    assert events[0]["file_path"] == "db.py"


def test_apply_canonical_revision_updates_head_when_accepted(db):
    _seed_workspace(db)

    apply_canonical_revision(
        db,
        workspace_id="ws-1",
        object_id="obj-1",
        object_type="memory.entry",
        revision_id="rev-1",
        parent_revision=None,
        device_id="dev-1",
        payload={"fact": "Name: Alice"},
        created_at="2026-04-16T10:00:00Z",
        accepted=True,
    )

    obj = db.conn.execute("SELECT * FROM canonical_objects WHERE id = ?", ("obj-1",)).fetchone()
    rev = db.conn.execute("SELECT * FROM canonical_revisions WHERE id = ?", ("rev-1",)).fetchone()

    assert obj["head_revision"] == "rev-1"
    assert unpack_payload(rev["payload"]) == {"fact": "Name: Alice"}


def test_apply_canonical_revision_keeps_head_when_unaccepted(db):
    _seed_workspace(db)

    apply_canonical_revision(db, "ws-1", "obj-1", "memory.entry", "rev-1", None, "dev-1", {"fact": "Name: Alice"}, "2026-04-16T10:00:00Z", True)
    apply_canonical_revision(db, "ws-1", "obj-1", "memory.entry", "rev-2", "rev-1", "dev-1", {"fact": "Name: Bob"}, "2026-04-16T10:01:00Z", False)

    obj = db.conn.execute("SELECT * FROM canonical_objects WHERE id = ?", ("obj-1",)).fetchone()
    rev = db.conn.execute("SELECT * FROM canonical_revisions WHERE id = ?", ("rev-2",)).fetchone()

    assert obj["head_revision"] == "rev-1"
    assert rev["accepted"] == 0