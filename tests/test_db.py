# tests/test_db.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import struct
import tempfile
from pathlib import Path
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    yield db
    db.close()


def test_create_tables(db):
    """Database should have entries, relations, conflicts, sessions tables."""
    tables = db.list_tables()
    assert "entries" in tables
    assert "relations" in tables
    assert "conflicts" in tables
    assert "sessions" in tables


class TestSchemaVersion:
    def test_fresh_db_at_latest_version(self, db):
        from db import LATEST_SCHEMA_VERSION
        v = db.conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert v == LATEST_SCHEMA_VERSION

    def test_migration_idempotent(self, db):
        from db import LATEST_SCHEMA_VERSION
        # Re-run migrations explicitly — should be a no-op
        db._migrate()
        db._migrate()
        v = db.conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert v == LATEST_SCHEMA_VERSION
        # Existing tables and indexes still intact
        idx = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_entries_unique'"
        ).fetchone()
        assert idx is not None

    def test_migration_runs_only_pending(self, db):
        from db import LATEST_SCHEMA_VERSION, MIGRATIONS
        # Force version back to 0; migrations should run forward to LATEST
        db.conn.execute("UPDATE schema_version SET version = 0")
        db.conn.commit()
        db._migrate()
        v = db.conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert v == LATEST_SCHEMA_VERSION
        assert LATEST_SCHEMA_VERSION == max(m[0] for m in MIGRATIONS)

    def test_fts_substring_match(self, db):
        db.add_entry(file="x.md", fact="Active students count is twenty four", grade=8)
        results = db.search_entries("twenty")
        assert any("twenty" in r["fact"] for r in results)
        # Mid-word trigram match (substring inside a word)
        results = db.search_entries("tuden")
        assert any("students" in r["fact"] for r in results)

    def test_fts_respects_filters(self, db):
        db.add_entry(file="a.md", fact="pricing model alpha", grade=9, tier="active")
        db.add_entry(file="b.md", fact="pricing model beta", grade=4, tier="cold")
        r = db.search_entries("pricing", file="a.md")
        assert len(r) == 1 and r[0]["file"] == "a.md"
        r = db.search_entries("pricing", min_grade=5)
        assert all(e["grade"] >= 5 for e in r) and len(r) == 1

    def test_fts_sync_on_update_and_delete(self, db):
        eid = db.add_entry(file="x.md", fact="original phrase here", grade=7)
        assert db.search_entries("original")
        db.update_entry(eid, fact="rewritten phrase here")
        assert not db.search_entries("original")
        assert db.search_entries("rewritten")
        db.delete_entry(eid)
        assert not db.search_entries("rewritten")

    def test_fts_special_chars_safe(self, db):
        db.add_entry(file="x.md", fact="cost is 50% of revenue", grade=7)
        # None of these should raise
        for q in ['50%', '"quoted"', 'a*b', 'foo:bar', '(paren)', 'a-b', '_underscore_']:
            db.search_entries(q)

    def test_legacy_db_without_schema_version_upgrades(self, tmp_path):
        # Simulate a pre-migration-framework DB by creating one then dropping
        # the schema_version table — re-opening should rebuild and set latest.
        from db import LATEST_SCHEMA_VERSION
        path = tmp_path / "legacy.db"
        d = KontextDB(str(path))
        d.conn.execute("DROP TABLE schema_version")
        d.conn.commit()
        d.close()
        d2 = KontextDB(str(path))
        v = d2.conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert v == LATEST_SCHEMA_VERSION
        d2.close()


def test_add_entry(db):
    entry_id = db.add_entry(
        file="user_identity.md",
        fact="Name: Ionut Rosu. Age: 26.",
        source="[Claude 2026-04]",
        grade=10,
        tier="active",
    )
    assert entry_id > 0


def test_no_duplicate_entry(db):
    db.add_entry(file="user_identity.md", fact="Name: Ionut Rosu.", source="[Claude 2026-04]", grade=10, tier="active")
    db.add_entry(file="user_identity.md", fact="Name: Ionut Rosu.", source="[Claude 2026-04]", grade=10, tier="active")
    entries = db.get_entries(file="user_identity.md")
    assert len([e for e in entries if e["fact"] == "Name: Ionut Rosu."]) == 1


def test_update_entry(db):
    entry_id = db.add_entry(file="user_identity.md", fact="Students: 27", source="[Claude 2026-03]", grade=9, tier="active")
    db.update_entry(entry_id, fact="Students: 24", source="[Claude 2026-04]")
    entry = db.get_entry(entry_id)
    assert entry["fact"] == "Students: 24"
    assert entry["source"] == "[Claude 2026-04]"


def test_get_entries_by_file(db):
    db.add_entry(file="goals.md", fact="Fact A", source="[Claude 2026-04]", grade=8, tier="active")
    db.add_entry(file="goals.md", fact="Fact B", source="[Claude 2026-04]", grade=6, tier="historical")
    db.add_entry(file="identity.md", fact="Fact C", source="[Claude 2026-04]", grade=9, tier="active")
    entries = db.get_entries(file="goals.md")
    assert len(entries) == 2
    assert all(e["file"] == "goals.md" for e in entries)


def test_get_entries_by_tier(db):
    db.add_entry(file="goals.md", fact="Active fact", source="[Claude 2026-04]", grade=8, tier="active")
    db.add_entry(file="goals.md", fact="Historical fact", source="[Claude 2026-03]", grade=6, tier="historical")
    active = db.get_entries(file="goals.md", tier="active")
    assert len(active) == 1
    assert active[0]["fact"] == "Active fact"


def test_search_entries(db):
    db.add_entry(file="identity.md", fact="Gaming PC has GTX 1080 Ti", source="[Claude 2026-04]", grade=8, tier="active")
    db.add_entry(file="goals.md", fact="YouTube channel launching", source="[Claude 2026-04]", grade=7, tier="active")
    results = db.search_entries("GTX 1080")
    assert len(results) >= 1
    assert "GTX 1080" in results[0]["fact"]


def test_delete_entry(db):
    entry_id = db.add_entry(file="test.md", fact="Delete me", source="[test]", grade=3, tier="active")
    db.delete_entry(entry_id)
    entry = db.get_entry(entry_id)
    assert entry is None


def test_decay_scores(db):
    """Entries not accessed in 60+ days should have grade reduced."""
    entry_id = db.add_entry(file="test.md", fact="Old fact", source="[Claude 2026-01]", grade=8, tier="active")
    db._execute("UPDATE entries SET last_accessed = datetime('now', '-90 days') WHERE id = ?", (entry_id,))
    db.decay_scores(days_threshold=60, decay_amount=0.5)
    entry = db.get_entry(entry_id)
    assert entry["grade"] < 8


def test_session_state(db):
    db.save_session(project="Tool Auditor", status="Building Electron apps", next_step="Test AutoPipeline", key_decisions="Shell throttle for memory")
    session = db.get_latest_session()
    assert session["project"] == "Tool Auditor"
    assert "Building" in session["status"]


def test_list_files(db):
    db.add_entry(file="identity.md", fact="Fact 1", source="[test]", grade=8, tier="active")
    db.add_entry(file="goals.md", fact="Fact 2", source="[test]", grade=7, tier="active")
    db.add_entry(file="identity.md", fact="Fact 3", source="[test]", grade=9, tier="active")
    files = db.list_files()
    assert "identity.md" in files
    assert "goals.md" in files
    assert files["identity.md"] == 2
    assert files["goals.md"] == 1


def test_recent_changes(db):
    db.add_entry(file="identity.md", fact="New fact", source="[Claude 2026-04]", grade=8, tier="active")
    changes = db.get_recent_changes(hours=24)
    assert len(changes) >= 1
    assert changes[0]["file"] == "identity.md"


# --- Knowledge graph (relations stored in db) ---

def test_add_and_get_relations(db):
    db.add_relation("Ionut", "uses", "Stripe", confidence=0.9, source="[Claude 2026-04]")
    db.add_relation("Ionut", "teaches_at", "Preply", confidence=0.8, source="[Claude 2026-04]")
    rels = db.get_relations("Ionut")
    assert len(rels) == 2


def test_query_graph(db):
    db.add_relation("Ionut", "uses", "Stripe")
    db.add_relation("Stripe", "processes", "Payments")
    results = db.query_graph("Ionut", depth=2)
    entities_found = set()
    for r in results:
        entities_found.add(r["entity_a"])
        entities_found.add(r["entity_b"])
    assert "Stripe" in entities_found
    assert "Payments" in entities_found



def test_tier_transitions_after_decay(db):
    """Grade 5 decayed by 0.5 should become 'cold', grade 8.3 decayed by 0.5 should become 'historical'."""
    id_cold = db.add_entry(file="test.md", fact="Should go cold", source="[test]", grade=5, tier="active")
    id_hist = db.add_entry(file="test.md", fact="Should go historical", source="[test]", grade=8.3, tier="active")

    # Backdate last_accessed so decay applies
    db._execute("UPDATE entries SET last_accessed = datetime('now', '-90 days') WHERE id IN (?, ?)", (id_cold, id_hist))

    db.decay_scores(days_threshold=60, decay_amount=0.5)

    entry_cold = db.get_entry(id_cold)
    assert entry_cold["grade"] == 4.5
    assert entry_cold["tier"] == "cold"

    entry_hist = db.get_entry(id_hist)
    assert entry_hist["grade"] == pytest.approx(7.8)
    assert entry_hist["tier"] == "historical"


def test_detect_conflict(db):
    # Undated active entries with numeric drift → genuine conflict
    db.add_entry(file="identity.md", fact="Active students currently: 27", source="manual", grade=9, tier="active")
    db.add_entry(file="identity.md", fact="Active students currently: 24", source="manual", grade=9, tier="active")
    conflicts = db.detect_conflicts(file="identity.md")
    assert len(conflicts) >= 1
    assert "students" in conflicts[0]["entry_a"].lower() or "students" in conflicts[0]["entry_b"].lower()


def test_detect_conflict_no_false_positives(db):
    db.add_entry(file="identity.md", fact="Name: Ionut Rosu", source="[Claude 2026-04]", grade=10, tier="active")
    db.add_entry(file="identity.md", fact="Location: Constanta", source="[Claude 2026-04]", grade=9, tier="active")
    conflicts = db.detect_conflicts(file="identity.md")
    assert len(conflicts) == 0


# --- Embeddings ---

def test_store_and_get_embedding(db):
    entry_id = db.add_entry(file="test.md", fact="Test embedding", source="[test]", grade=8, tier="active")
    fake_embedding = [0.1, 0.2, 0.3, 0.4]
    db.store_embedding(entry_id, fake_embedding)
    result = db.get_embedding(entry_id)
    assert result is not None
    assert len(result) == 4
    assert abs(result[0] - 0.1) < 0.001

def test_search_by_embedding(db):
    id1 = db.add_entry(file="test.md", fact="Python programming language", source="[test]", grade=8, tier="active")
    id2 = db.add_entry(file="test.md", fact="JavaScript web development", source="[test]", grade=8, tier="active")
    db.store_embedding(id1, [0.9, 0.1, 0.0, 0.0])
    db.store_embedding(id2, [0.0, 0.0, 0.9, 0.1])
    query_vec = [0.85, 0.15, 0.0, 0.0]
    results = db.semantic_search(query_vec, limit=2)
    assert len(results) >= 1
    assert results[0]["fact"] == "Python programming language"


# --- File Metadata ---

def test_set_and_get_file_meta(db):
    db.set_file_meta("user_identity.md", file_type="user", description="Core bio")
    meta = db.get_file_meta("user_identity.md")
    assert meta["file_type"] == "user"
    assert meta["description"] == "Core bio"

def test_get_file_meta_default(db):
    meta = db.get_file_meta("unknown_file.md")
    assert meta["file_type"] == "user"
    assert meta["description"] == ""

def test_get_all_file_meta(db):
    db.set_file_meta("identity.md", file_type="user", description="Bio")
    db.set_file_meta("goals.md", file_type="project", description="Projects")
    all_meta = db.get_all_file_meta()
    assert "identity.md" in all_meta
    assert "goals.md" in all_meta
    assert all_meta["identity.md"]["file_type"] == "user"


def test_purge_old_sessions(db):
    for i in range(10):
        db.save_session(project=f"Project {i}", status=f"Status {i}")
    db.purge_old_sessions(keep=3)
    row = db._execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert row[0] == 3
    latest = db.get_latest_session()
    assert latest["project"] == "Project 9"
