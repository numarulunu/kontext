# tests/test_integration.py
"""End-to-end integration tests for the full Kontext pipeline."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
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
        "- [User Identity](user_identity.md) — name, role, background\n"
        "- [Project Goals](project_goals.md) — goals and plans\n",
        encoding="utf-8",
    )
    (mem / "user_identity.md").write_text(
        "---\nname: User Identity\ndescription: name and role\ntype: user\n---\n\n"
        "## Active\n\n- Name: Test User\n",
        encoding="utf-8",
    )
    (mem / "project_goals.md").write_text(
        "---\nname: Project Goals\ndescription: goals\ntype: project\n---\n\n"
        "## Active\n\n- Launch product\n",
        encoding="utf-8",
    )
    return mem


class TestWriteExportSyncCycle:
    """Test the full write → export → sync round-trip."""

    def test_write_and_export(self, db, memory_dir):
        """Write entries to DB, export to flat files, verify content."""
        from export import export_file, export_memory_index

        # Write entries
        db.add_entry(file="user_identity.md", fact="Location: Berlin", source="[test]", grade=9, tier="active")
        db.add_entry(file="user_identity.md", fact="Role: Voice teacher", source="[test]", grade=8, tier="active")
        db.add_entry(file="user_identity.md", fact="Old location: Paris", source="[test]", grade=6, tier="historical")

        # Export
        content = export_file(db, "user_identity.md")
        (memory_dir / "user_identity.md").write_text(content, encoding="utf-8")

        # Verify
        text = (memory_dir / "user_identity.md").read_text(encoding="utf-8")
        assert "Location: Berlin" in text
        assert "Voice teacher" in text

    def test_sync_reimports_manual_edits(self, db, memory_dir):
        """If user edits a flat file directly, sync should import changes."""
        from sync import sync

        # Add initial entry to DB
        db.add_entry(file="user_identity.md", fact="Name: Test User", source="[test]", grade=9)

        # Manually add a new entry to the flat file
        content = (memory_dir / "user_identity.md").read_text(encoding="utf-8")
        content += "\n- Hobby: Singing\n"
        (memory_dir / "user_identity.md").write_text(content, encoding="utf-8")

        # Sync should pick up the new entry
        result = sync(memory_dir=memory_dir, db=db)
        assert result["synced"] >= 1

    def test_session_continuity_pipeline(self, db):
        """Test the full session save → get round-trip."""
        # Save session
        db.save_session(
            project="Test Project",
            status="in progress",
            next_step="write tests",
            key_decisions="use pytest",
            summary="Building test suite for Kontext",
            files_touched="db.py, mcp_server.py",
            workspace="C:/repos/kontext",
        )

        # Get session
        session = db.get_latest_session(workspace="C:/repos/kontext")
        assert session is not None
        assert session["project"] == "Test Project"
        assert session["summary"] == "Building test suite for Kontext"
        assert session["files_touched"] == "db.py, mcp_server.py"


class TestConflictDetectionPipeline:
    """Test conflict detection → resolution flow using DB only."""

    def test_detect_and_resolve(self, db):
        # Add conflicting entries
        db.add_entry(file="test.md", fact="Active students: 27 paying", source="[Claude 2026-03]", grade=9, tier="active")
        db.add_entry(file="test.md", fact="Active students: 24 paying", source="[test]", grade=9, tier="active")

        # Detect
        conflicts = db.detect_conflicts()
        assert len(conflicts) >= 1

        # Resolve
        pending = db.get_pending_conflicts()
        assert len(pending) >= 1
        db.resolve_conflict(pending[0]["id"], "Kept: Active students: 24 paying")

        # Verify resolved
        remaining = db.get_pending_conflicts()
        assert len(remaining) < len(pending)


class TestDreamConsolidation:
    """Test dream phases work correctly on real data."""

    def test_dedup_merges_duplicates(self, db):
        from dream import phase_dedup
        db.add_entry(file="test.md", fact="Active students: 24 paying", source="[test]", grade=9)
        db.add_entry(file="test.md", fact="Active students: 24 paying students", source="[test]", grade=8)

        stats = phase_dedup(db)
        assert stats["merged"] >= 1
        # Only one should remain
        entries = db.get_entries(file="test.md")
        assert len(entries) == 1
        assert entries[0]["grade"] == 9  # Higher grade kept

    def test_full_dream_cycle(self, db):
        from dream import dream
        db.add_entry(file="test.md", fact="Test fact A", source="[test]", grade=5, tier="active")
        db.add_entry(file="test.md", fact="Test fact B", source="[test]", grade=8, tier="active")

        results = dream(db, dry_run=True)
        assert "dedup" in results
        assert "normalize" in results
        assert "resolve" in results
        assert "compress" in results
        assert "purge" in results


class TestTransactionSafety:
    """Test that transactions protect against partial updates."""

    def test_rollback_on_failure(self, db):
        initial_count = len(db.get_entries())
        try:
            with db.transaction():
                db.conn.execute(
                    "INSERT INTO entries (file, fact, grade, tier) VALUES (?, ?, ?, ?)",
                    ("test.md", "should be rolled back", 5, "active"),
                )
                raise ValueError("Simulated failure")
        except ValueError:
            pass

        assert len(db.get_entries()) == initial_count

    def test_commit_on_success(self, db):
        initial_count = len(db.get_entries())
        with db.transaction():
            db.conn.execute(
                "INSERT INTO entries (file, fact, grade, tier) VALUES (?, ?, ?, ?)",
                ("test.md", "should be committed", 5, "active"),
            )

        assert len(db.get_entries()) == initial_count + 1


class TestInputValidation:
    """Test that MCP tools properly validate inputs."""

    def test_grade_clamping(self, db):
        """Grades should be clamped 1-10 at the MCP layer."""
        # Direct DB doesn't clamp — that's MCP's job
        eid = db.add_entry(file="test.md", fact="test", grade=99)
        entry = db.get_entry(eid)
        assert entry["grade"] == 99  # DB allows it — MCP should prevent this

    def test_embedding_blob_validation(self, db):
        """Malformed BLOBs should return None, not crash."""
        eid = db.add_entry(file="test.md", fact="blob test", grade=5)
        db.conn.execute("UPDATE entries SET embedding = ? WHERE id = ?", (b"\x00\x01\x02", eid))
        db.conn.commit()
        result = db.get_embedding(eid)
        assert result is None

    def test_semantic_search_file_filter(self, db):
        """semantic_search should accept file filter."""
        import struct
        vec = [0.1, 0.2, 0.3]
        blob = struct.pack(f'{len(vec)}f', *vec)

        eid1 = db.add_entry(file="a.md", fact="fact in a", grade=5)
        eid2 = db.add_entry(file="b.md", fact="fact in b", grade=5)
        db.conn.execute("UPDATE entries SET embedding = ? WHERE id = ?", (blob, eid1))
        db.conn.execute("UPDATE entries SET embedding = ? WHERE id = ?", (blob, eid2))
        db.conn.commit()

        results_all = db.semantic_search(vec, limit=10)
        results_a = db.semantic_search(vec, limit=10, file="a.md")
        assert len(results_a) <= len(results_all)
        assert all(r["file"] == "a.md" for r in results_a)
