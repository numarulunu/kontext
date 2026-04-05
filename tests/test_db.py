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
    db.add_entry(file="identity.md", fact="Active students: 27", source="[Claude 2026-03]", grade=9, tier="active")
    db.add_entry(file="identity.md", fact="Active students: 24", source="[Claude 2026-04]", grade=9, tier="active")
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
