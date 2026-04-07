# tests/test_brainstorm.py
"""Tests for pipeline/brainstorm.py — health report generation."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))

import pytest
from pathlib import Path
from brainstorm import analyze_file, generate_report, estimate_tokens


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "- [User Identity](user_identity.md) — name, role\n"
        "- [Goals](project_goals.md) — goals and plans\n",
        encoding="utf-8",
    )
    (mem / "user_identity.md").write_text(
        "---\nname: User Identity\ndescription: name and role\ntype: user\n---\n\n"
        "## Active\n\n"
        "- Name: Test User\n"
        "- Location: Constanta\n"
        "- Role: Voice teacher\n\n"
        "## Historical\n\n"
        "- [2025-01] Previously lived in Bucharest\n",
        encoding="utf-8",
    )
    (mem / "project_goals.md").write_text(
        "---\nname: Project Goals\ndescription: goals\ntype: project\n---\n\n"
        "## Active\n\n"
        "- Launch Skool community\n"
        "- Ship YouTube pilot\n",
        encoding="utf-8",
    )
    return mem


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_basic_prose(self):
        text = "The quick brown fox jumps over the lazy dog."
        tokens = estimate_tokens(text)
        assert 8 <= tokens <= 15  # ~44 chars / 4 = 11

    def test_code_estimates_higher(self):
        code = "    def __init__(self, db_path: str = None):\n" * 10
        prose = "This is a simple prose sentence about life. " * 10
        code_tokens = estimate_tokens(code)
        prose_tokens = estimate_tokens(prose)
        # Code should estimate more tokens per char
        assert code_tokens > prose_tokens * 0.9  # At least comparable

    def test_url_heavy_text(self):
        text = "Visit https://example.com/very/long/path/to/something for details. " * 5
        tokens = estimate_tokens(text)
        # Should handle URLs without crashing
        assert tokens > 0


class TestAnalyzeFile:
    def test_basic_analysis(self, memory_dir):
        result = analyze_file(memory_dir / "user_identity.md")
        assert result["filename"] == "user_identity.md"
        assert result["name"] == "User Identity"
        assert result["type"] == "user"
        assert result["total_tokens"] > 0

    def test_detects_historical_section(self, memory_dir):
        result = analyze_file(memory_dir / "user_identity.md")
        assert result["has_historical"] is True
        assert result["historical_tokens"] > 0
        assert result["active_tokens"] > 0

    def test_no_historical_section(self, memory_dir):
        result = analyze_file(memory_dir / "project_goals.md")
        assert result["has_historical"] is False

    def test_entry_counts(self, memory_dir):
        result = analyze_file(memory_dir / "user_identity.md")
        assert result["active_entry_count"] == 3
        assert result["historical_entry_count"] == 1

    def test_last_modified(self, memory_dir):
        result = analyze_file(memory_dir / "user_identity.md")
        assert result["last_modified"] is not None
        assert result["days_since_modified"] is not None


class TestGenerateReport:
    def test_report_contains_summary(self, memory_dir):
        report = generate_report(memory_dir)
        assert "Memory Health Report" in report
        assert "Total files:" in report

    def test_report_lists_files(self, memory_dir):
        report = generate_report(memory_dir)
        assert "user_identity.md" in report
        assert "project_goals.md" in report

    def test_report_with_target_file(self, memory_dir):
        report = generate_report(memory_dir, target_file="user_identity")
        assert "user_identity.md" in report

    def test_report_target_file_not_found(self, memory_dir):
        result = generate_report(memory_dir, target_file="nonexistent")
        assert "No memory file matching" in result

    def test_report_shows_conflicts_from_db(self, memory_dir):
        report = generate_report(memory_dir)
        # Should show conflict status (either "none" or count)
        assert "Conflicts:" in report
