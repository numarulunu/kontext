# tests/test_export.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from db import KontextDB
from export import export_file, export_all, export_memory_index


@pytest.fixture
def populated_db(tmp_path):
    db = KontextDB(str(tmp_path / "test.db"))
    db.add_entry(file="user_identity.md", fact="Name: Ionut Rosu", source="[Claude 2026-04]", grade=10, tier="active")
    db.add_entry(file="user_identity.md", fact="Was in Dubai", source="[Gemini 2025]", grade=5, tier="historical")
    db.add_entry(file="project_goals.md", fact="YouTube channel launching", source="[Claude 2026-04]", grade=8, tier="active")
    return db


def test_export_file(populated_db, tmp_path):
    content = export_file(populated_db, "user_identity.md")
    assert "## Active" in content
    assert "Name: Ionut Rosu" in content
    assert "## Historical" in content
    assert "Was in Dubai" in content


def test_export_all(populated_db, tmp_path):
    out_dir = tmp_path / "export"
    out_dir.mkdir()
    export_all(populated_db, out_dir)
    assert (out_dir / "user_identity.md").exists()
    assert (out_dir / "project_goals.md").exists()
    content = (out_dir / "user_identity.md").read_text()
    assert "Name: Ionut Rosu" in content


def test_export_memory_index(populated_db, tmp_path):
    out_dir = tmp_path / "export"
    out_dir.mkdir()
    export_memory_index(populated_db, out_dir)
    index = (out_dir / "MEMORY.md").read_text()
    assert "user_identity.md" in index
    assert "project_goals.md" in index
