# tests/test_dream.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from db import KontextDB
from dream import (
    phase_dedup, phase_normalize, phase_resolve,
    phase_compress, phase_purge, similarity, dream,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    yield db
    db.close()


class TestSimilarity:
    def test_identical(self):
        assert similarity("hello world", "hello world") == 1.0

    def test_similar(self):
        assert similarity("Active students: 27", "Active students: 24") > 0.85

    def test_different(self):
        assert similarity("the sky is blue", "I like pizza") < 0.5


class TestDedup:
    def test_merges_near_duplicates(self, db):
        db.add_entry(file="test.md", fact="Active students: 27", grade=7)
        db.add_entry(file="test.md", fact="Active students: 24", grade=9)
        stats = phase_dedup(db)
        assert stats["merged"] == 1
        remaining = db.get_entries(file="test.md")
        assert len(remaining) == 1
        assert remaining[0]["grade"] == 9  # kept the higher grade

    def test_keeps_different_entries(self, db):
        db.add_entry(file="test.md", fact="User lives in Romania")
        db.add_entry(file="test.md", fact="User teaches singing")
        stats = phase_dedup(db)
        assert stats["merged"] == 0
        assert len(db.get_entries(file="test.md")) == 2

    def test_dry_run_doesnt_modify(self, db):
        db.add_entry(file="test.md", fact="Active students: 27", grade=7)
        db.add_entry(file="test.md", fact="Active students: 24", grade=9)
        stats = phase_dedup(db, dry_run=True)
        assert stats["pairs_found"] == 1
        assert stats["merged"] == 0
        assert len(db.get_entries(file="test.md")) == 2


class TestNormalize:
    def test_anchors_relative_date(self, db):
        db.add_entry(
            file="test.md",
            fact="Started teaching last month",
            source="[Claude 2026-04]",
        )
        stats = phase_normalize(db)
        assert stats["anchored"] == 1
        entry = db.get_entries(file="test.md")[0]
        assert "(as of 2026-04)" in entry["fact"]

    def test_skips_already_anchored(self, db):
        db.add_entry(
            file="test.md",
            fact="Started teaching last month (as of 2026-03)",
            source="[Claude 2026-04]",
        )
        stats = phase_normalize(db)
        assert stats["anchored"] == 0

    def test_skips_no_source_date(self, db):
        db.add_entry(file="test.md", fact="Started teaching last month", source="manual")
        stats = phase_normalize(db)
        assert stats["flagged"] == 1
        assert stats["anchored"] == 0


class TestResolve:
    def test_resolves_missing_entry(self, db):
        # Create a conflict where one entry was already deleted
        db.add_conflict(file="test.md", entry_a="Fact that exists", entry_b="Fact that was deleted")
        # Backdate the conflict so it's >7 days old
        db.conn.execute(
            "UPDATE conflicts SET created_at = datetime('now', '-10 days') WHERE id = 1"
        )
        db.conn.commit()
        db.add_entry(file="test.md", fact="Fact that exists", grade=8)
        stats = phase_resolve(db)
        assert stats["auto_resolved"] == 1

    def test_skips_recent_conflicts(self, db):
        db.add_conflict(file="test.md", entry_a="Fact A", entry_b="Fact B")
        db.add_entry(file="test.md", fact="Fact A", grade=8)
        db.add_entry(file="test.md", fact="Fact B", grade=8)
        stats = phase_resolve(db)
        assert stats["skipped"] == 1
        assert stats["auto_resolved"] == 0

    def test_newer_date_wins(self, db):
        db.add_entry(file="test.md", fact="Students: 27", source="[Claude 2026-03]", grade=8)
        db.add_entry(file="test.md", fact="Students: 24", source="[Claude 2026-04]", grade=8)
        db.add_conflict(file="test.md", entry_a="Students: 27", entry_b="Students: 24")
        db.conn.execute(
            "UPDATE conflicts SET created_at = datetime('now', '-10 days') WHERE id = 1"
        )
        db.conn.commit()
        stats = phase_resolve(db)
        assert stats["auto_resolved"] == 1
        # Older entry should be demoted to historical
        entries = db.get_entries(file="test.md")
        tiers = {e["fact"]: e["tier"] for e in entries}
        assert tiers["Students: 24"] == "active"
        assert tiers["Students: 27"] == "historical"


class TestCompress:
    def test_strips_source_tags(self, db):
        db.add_entry(
            file="test.md",
            fact="[Claude 2026-01] User started singing at age 12",
            grade=2, tier="cold",
        )
        stats = phase_compress(db)
        assert stats["compressed"] == 1
        entry = db.get_entries(file="test.md")[0]
        assert "[Claude 2026-01]" not in entry["fact"]

    def test_truncates_long_entries(self, db):
        long_fact = "A " * 100  # 200 chars
        db.add_entry(file="test.md", fact=long_fact.strip(), grade=2, tier="cold")
        stats = phase_compress(db)
        assert stats["compressed"] == 1
        entry = db.get_entries(file="test.md")[0]
        assert len(entry["fact"]) <= 120

    def test_ignores_active_entries(self, db):
        db.add_entry(file="test.md", fact="Important active fact", grade=9, tier="active")
        stats = phase_compress(db)
        assert stats["compressed"] == 0


class TestPurge:
    def test_purges_dead_entries(self, db):
        db.add_entry(file="test.md", fact="Ancient forgotten fact", grade=1)
        # Backdate last_accessed
        db.conn.execute(
            "UPDATE entries SET last_accessed = datetime('now', '-150 days') WHERE id = 1"
        )
        db.conn.commit()
        stats = phase_purge(db)
        assert stats["purged"] == 1
        assert len(db.get_entries(file="test.md")) == 0

    def test_keeps_higher_grade(self, db):
        db.add_entry(file="test.md", fact="Still useful fact", grade=3)
        db.conn.execute(
            "UPDATE entries SET last_accessed = datetime('now', '-150 days') WHERE id = 1"
        )
        db.conn.commit()
        stats = phase_purge(db)
        assert stats["purged"] == 0

    def test_keeps_recently_accessed(self, db):
        db.add_entry(file="test.md", fact="Low grade but recent", grade=1)
        stats = phase_purge(db)
        assert stats["purged"] == 0


class TestDreamOrchestrator:
    def test_full_cycle(self, db):
        # Add entries that exercise multiple phases
        db.add_entry(file="test.md", fact="Duplicate fact A", grade=7)
        db.add_entry(file="test.md", fact="Duplicate fact A!", grade=9)
        db.add_entry(file="test.md", fact="[Claude 2026-01] Cold compressed entry with long parens (this should be removed entirely from the fact)", grade=2, tier="cold")
        results = dream(db)
        assert "dedup" in results
        assert "compress" in results

    def test_single_phase(self, db):
        results = dream(db, phase="dedup")
        assert "dedup" in results
        assert len(results) == 1
