# tests/test_sync.py
"""Tests for sync.py — flat file sync, decay scheduling, dream gate."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    db = KontextDB(str(tmp_path / "test.db"))
    yield db
    db.close()


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "- [Test File](test_file.md) — test entries\n", encoding="utf-8"
    )
    return mem


def _write_memory_file(memory_dir, filename, entries):
    """Helper: write a simple memory file with bullet entries."""
    lines = [
        "---",
        f"name: {filename.replace('.md', '')}",
        "description: test",
        "type: user",
        "---",
        "",
        "## Active",
        "",
    ]
    for e in entries:
        lines.append(f"- {e}")
    (memory_dir / filename).write_text("\n".join(lines), encoding="utf-8")


class TestSync:
    def test_imports_new_entries(self, db, memory_dir):
        _write_memory_file(memory_dir, "test_file.md", [
            "Fact one about testing",
            "Fact two about something else",
        ])
        from sync import sync
        result = sync(memory_dir=memory_dir, db=db)
        assert result["synced"] >= 2
        assert result["files_checked"] >= 1

    def test_skips_duplicates(self, db, memory_dir):
        db.add_entry(file="test_file.md", fact="Fact one about testing", source="[test]", grade=5)
        _write_memory_file(memory_dir, "test_file.md", [
            "Fact one about testing",
        ])
        from sync import sync
        result = sync(memory_dir=memory_dir, db=db)
        assert result["synced"] == 0
        assert result["skipped"] >= 1

    def test_fuzzy_dedup_catches_near_duplicates(self, db, memory_dir):
        """Sync should detect near-duplicates via 85% similarity on second run."""
        from difflib import SequenceMatcher
        fact_a = "Active students: 24 paying"
        fact_b = "Active students: 24 paying students"
        sim = SequenceMatcher(None, fact_a.lower(), fact_b.lower()).ratio()
        assert sim >= 0.85, f"Test precondition: similarity {sim} < 0.85"

        # First sync imports fact_a
        _write_memory_file(memory_dir, "test_file.md", [fact_a])
        from sync import sync
        result1 = sync(memory_dir=memory_dir, db=db)
        assert result1["synced"] == 1

        # Add near-duplicate — second sync should catch it
        _write_memory_file(memory_dir, "test_file.md", [fact_a, fact_b])
        result2 = sync(memory_dir=memory_dir, db=db)
        assert result2["synced"] == 0  # fact_b caught by fuzzy dedup

    def test_skips_memory_md(self, db, memory_dir):
        from sync import sync
        result = sync(memory_dir=memory_dir, db=db)
        # MEMORY.md should not be parsed for entries
        entries = db.get_entries()
        assert not any(e["file"] == "MEMORY.md" for e in entries)

    def test_handles_missing_memory_dir(self):
        from sync import sync
        result = sync(memory_dir=Path("/nonexistent/path"))
        assert result["synced"] == 0

    def test_dry_run_doesnt_modify_db(self, db, memory_dir):
        _write_memory_file(memory_dir, "test_file.md", ["New fact for dry run"])
        from sync import sync
        # Pass the tmp_path fixture db — otherwise sync() creates its own
        # production KontextDB() and the assertion below is vacuous.
        result = sync(memory_dir=memory_dir, dry_run=True, db=db)
        assert result["synced"] >= 1
        # DB should still be empty
        entries = db.get_entries(file="test_file.md")
        assert len(entries) == 0
        # Dry-run must also skip decay + dream (both are writes).
        assert result["decayed"] == 0
        assert result["dreamed"] == 0


class TestMaybeDream:
    def test_dream_runs_when_stamp_missing(self, db, tmp_path):
        from sync import _maybe_dream, _DREAM_STAMP
        # Ensure stamp doesn't exist
        stamp = tmp_path / "_dream_last_test"
        import sync
        original_stamp = sync._DREAM_STAMP
        sync._DREAM_STAMP = stamp
        try:
            result = _maybe_dream(db)
            assert isinstance(result, int)
        finally:
            sync._DREAM_STAMP = original_stamp

    def test_dream_skips_when_recent(self, db, tmp_path):
        from sync import _maybe_dream
        import sync
        stamp = tmp_path / "_dream_last_test"
        stamp.write_text(datetime.now().isoformat(), encoding="utf-8")
        original_stamp = sync._DREAM_STAMP
        sync._DREAM_STAMP = stamp
        try:
            result = _maybe_dream(db)
            assert result == 0  # Should skip
        finally:
            sync._DREAM_STAMP = original_stamp


class TestDecay:
    def test_decay_reduces_old_grades(self, db):
        # Add entry with old last_accessed
        db.add_entry(file="test.md", fact="Old entry", source="[test]", grade=8)
        # Force last_accessed to 90 days ago
        db.conn.execute(
            "UPDATE entries SET last_accessed = datetime('now', '-90 days') WHERE fact = 'Old entry'"
        )
        db.conn.commit()
        from sync import _run_decay
        decayed = _run_decay(db)
        entry = db.search_entries("Old entry")[0]
        assert entry["grade"] < 8
