"""
Kontext smoke tests — regression coverage for the bugs we just fixed.

Run from the Kontext directory:
    python -m pytest tests/ -v

Each test uses a temp DB so the production kontext.db is never touched.
"""

import os
import sys
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Make Kontext root importable when pytest is run from anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import KontextDB  # noqa: E402


@pytest.fixture
def db():
    """Fresh in-memory-ish DB per test (file-backed temp so WAL works)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    d = KontextDB(db_path=path)
    yield d
    d.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# DB layer
# ─────────────────────────────────────────────────────────────────────────────

class TestEntries:
    def test_add_entry_returns_id(self, db):
        eid = db.add_entry("user_test.md", "Active students: 24", source="[Claude 2026-04]", grade=9)
        assert eid > 0

    def test_add_entry_dedup_on_exact_match(self, db):
        eid1 = db.add_entry("user_test.md", "Active students: 24")
        eid2 = db.add_entry("user_test.md", "Active students: 24")
        assert eid1 == eid2  # Same row, no duplicate

    def test_add_entry_distinct_facts(self, db):
        eid1 = db.add_entry("user_test.md", "Active students: 24")
        eid2 = db.add_entry("user_test.md", "Active students: 27")
        assert eid1 != eid2

    def test_get_entries_filters(self, db):
        db.add_entry("a.md", "fact 1", grade=9, tier="active")
        db.add_entry("a.md", "fact 2", grade=3, tier="historical")
        db.add_entry("b.md", "fact 3", grade=9, tier="active")
        assert len(db.get_entries(file="a.md")) == 2
        assert len(db.get_entries(tier="active")) == 2
        assert len(db.get_entries(min_grade=8)) == 2


class TestExecuteCommits:
    """Regression: db.execute() used to NOT commit, leading to silent data loss
    if any caller forgot to call db.conn.commit() afterward."""

    def test_execute_commits(self, db):
        db.add_entry("a.md", "to be deleted")
        db.execute("DELETE FROM entries WHERE file = ?", ("a.md",))
        # Reopen connection — change must persist without explicit commit
        db.conn.close()
        conn = sqlite3.connect(db.db_path)
        rows = conn.execute("SELECT COUNT(*) FROM entries WHERE file = 'a.md'").fetchone()
        conn.close()
        assert rows[0] == 0


class TestConflictDetector:
    """Regression: detector used to flag any same-file pair sharing 2+ keywords,
    which produced a huge backlog of false positives because facts that EVOLVE
    over time naturally share keywords. The detector is now temporal-aware."""

    def test_dated_entries_are_not_conflicts(self, db):
        # Same file, same topic, different values, BOTH dated → evolution, not conflict
        db.add_entry("user_test.md", "Active paying students: 24",
                     source="[Claude 2026-04]", grade=9)
        db.add_entry("user_test.md", "Active paying students: 27",
                     source="[Claude 2026-03]", grade=9)
        conflicts = db.detect_conflicts()
        assert conflicts == [], f"dated entries should not conflict: {conflicts}"

    def test_historical_tier_is_not_conflict(self, db):
        # One active, one demoted to historical → already-resolved evolution
        db.add_entry("user_test.md", "Active paying students: 24",
                     source="", grade=9, tier="active")
        db.add_entry("user_test.md", "Active paying students: 27",
                     source="", grade=9, tier="historical")
        conflicts = db.detect_conflicts()
        assert conflicts == []

    def test_undated_concurrent_numeric_conflict_is_flagged(self, db):
        # Both undated, both active, share 3+ value words including a number
        # → genuine contradiction
        db.add_entry("user_test.md", "Active paying students total 24 currently", source="")
        db.add_entry("user_test.md", "Active paying students total 27 currently", source="")
        conflicts = db.detect_conflicts()
        assert len(conflicts) == 1

    def test_unrelated_entries_no_false_positive(self, db):
        db.add_entry("user_test.md", "Lives in Bucharest Romania")
        db.add_entry("user_test.md", "Trains five days per week")
        conflicts = db.detect_conflicts()
        assert conflicts == []


class TestFileMeta:
    def test_set_and_get_file_meta(self, db):
        db.set_file_meta("user_x.md", file_type="user", description="Test description with keywords")
        meta = db.get_file_meta("user_x.md")
        assert meta["description"] == "Test description with keywords"
        assert meta["file_type"] == "user"

    def test_get_file_meta_missing_returns_default(self, db):
        meta = db.get_file_meta("never_set.md")
        assert meta["description"] == ""
        assert meta["file_type"] == "user"


class TestRecentChanges:
    def test_get_recent_changes(self, db):
        db.add_entry("a.md", "recent fact")
        recent = db.get_recent_changes(hours=1)
        assert len(recent) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Export layer
# ─────────────────────────────────────────────────────────────────────────────

class TestExport:
    """Regression: export_memory_index used to ignore DB file_meta and only
    read the hardcoded _FILE_META dict, leaving descriptions blank for any
    file added via kontext_write."""

    def test_export_memory_index_uses_db_file_meta(self, db, tmp_path):
        from export import export_memory_index
        db.add_entry("project_new_thing.md", "fact", grade=9)
        db.set_file_meta("project_new_thing.md", "project", "Brand new project description")
        export_memory_index(db, tmp_path)
        content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        assert "Brand new project description" in content

    def test_export_file_renders_sections(self, db, tmp_path):
        from export import export_file
        db.add_entry("a.md", "active fact", grade=9, tier="active")
        db.add_entry("a.md", "historical fact", grade=6, tier="historical")
        content = export_file(db, "a.md")
        assert "active fact" in content
        assert "historical fact" in content
        assert "## Active" in content
        assert "## Historical" in content


# ─────────────────────────────────────────────────────────────────────────────
# Sync layer
# ─────────────────────────────────────────────────────────────────────────────

class TestSync:
    """Regression: sync.py duplicate-check used a 50-char LIKE prefix then
    exact comparison — produced false negatives and accumulated duplicates."""

    def test_sync_dedup_exact_match(self, db, tmp_path, monkeypatch):
        """sync.py must not re-import an entry that already exists in the DB."""
        import sync as sync_mod
        import db as db_module

        # The exact fact text we'll write into both DB and the file
        fact = "Active paying students total 24 currently enrolled"

        # Pre-populate the DB
        db.add_entry("user_x.md", fact, source="[2026-04]", grade=9)

        # Build a fake memory dir; parse_memory_file expects frontmatter + sections
        memdir = tmp_path / "memory"
        memdir.mkdir()
        (memdir / "user_x.md").write_text(
            "---\nname: x\ndescription: t\ntype: user\n---\n\n"
            "## Active (Grade 8-10)\n\n"
            f"[2026-04] {fact} Grade: 9\n",
            encoding="utf-8",
        )

        # sync.py does `from db import KontextDB` inside the function — patch the
        # db module's class so the fresh import inside sync.sync() picks up our temp DB
        db_path = db.db_path
        original_init = db_module.KontextDB.__init__

        def patched_init(self, db_path_arg=None):
            return original_init(self, db_path=db_path)

        monkeypatch.setattr(db_module.KontextDB, "__init__", patched_init)

        sync_mod.sync(memory_dir=memdir, dry_run=False)

        conn = sqlite3.connect(db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE file = 'user_x.md'"
        ).fetchone()[0]
        conn.close()
        assert n == 1, f"sync created duplicates: {n} rows"


# ─────────────────────────────────────────────────────────────────────────────
# Brainstorm
# ─────────────────────────────────────────────────────────────────────────────

class TestBrainstorm:
    def test_brainstorm_runs_clean(self, db, monkeypatch, capsys):
        import brainstorm
        monkeypatch.setattr(brainstorm, "KontextDB", lambda: KontextDB(db_path=db.db_path))
        db.add_entry("a.md", "fact one", grade=9)
        db.add_entry("a.md", "fact two", grade=6, tier="historical")
        rc = brainstorm.main()
        captured = capsys.readouterr()
        assert rc == 0
        assert "KONTEXT HEALTH REPORT" in captured.out
        assert "Files tracked" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# MCP server importability
# ─────────────────────────────────────────────────────────────────────────────

class TestMcpServer:
    def test_mcp_server_imports(self):
        import mcp_server
        assert hasattr(mcp_server, "find_memory_dir")

    def test_all_kontext_tools_defined(self):
        import mcp_server
        src = Path(mcp_server.__file__).read_text(encoding="utf-8")
        for tool in [
            "kontext_search", "kontext_reindex", "kontext_write",
            "kontext_query", "kontext_relate", "kontext_recent",
            "kontext_decay", "kontext_session", "kontext_conflicts",
        ]:
            assert tool in src, f"MCP tool {tool} missing from mcp_server.py"
