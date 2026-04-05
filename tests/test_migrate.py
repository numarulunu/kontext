# tests/test_migrate.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
from pathlib import Path
from db import KontextDB
from migrate import parse_memory_file, migrate_all


@pytest.fixture
def tmp_memory(tmp_path):
    """Create a fake memory directory with sample files."""
    mem = tmp_path / "memory"
    mem.mkdir()

    # MEMORY.md index
    (mem / "MEMORY.md").write_text(
        "- [Identity](user_identity.md) — name, age, location\n"
        "- [Goals](project_goals.md) — what is being built\n"
    )

    # Sample memory file with Active and Historical sections
    (mem / "user_identity.md").write_text(
        "---\nname: Identity\ndescription: Core bio\ntype: user\n---\n\n"
        "## Active (Grade 8-10)\n\n"
        "[Claude 2026-04] Name: Ionut Rosu. Age: 26. Grade: 10\n"
        "[Claude 2026-04] Gaming PC: Windows 11, GTX 1080 Ti. Grade: 8\n\n"
        "## Historical (Grade 5-7)\n\n"
        "[Gemini 2025] Was in Dubai at some point. Grade: 5\n"
    )

    (mem / "project_goals.md").write_text(
        "---\nname: Goals\ndescription: Projects\ntype: project\n---\n\n"
        "## Active Projects\n\n"
        "### YouTube\n"
        "[Claude 2026-04] Documentary channel with POV Guy character. Grade: 8\n"
    )

    return mem


def test_parse_memory_file(tmp_memory):
    entries = parse_memory_file(tmp_memory / "user_identity.md")
    assert len(entries) == 3
    assert entries[0]["fact"] == "Name: Ionut Rosu. Age: 26."
    assert entries[0]["grade"] == 10
    assert entries[0]["tier"] == "active"
    assert entries[0]["source"] == "[Claude 2026-04]"
    assert entries[2]["tier"] == "historical"


def test_migrate_all(tmp_memory, tmp_path):
    db_path = tmp_path / "migrated.db"
    db = KontextDB(str(db_path))
    count = migrate_all(tmp_memory, db)
    assert count >= 3
    files = db.list_files()
    assert "user_identity.md" in files
    assert "project_goals.md" in files
    db.close()
