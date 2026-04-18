"""Tests for phase_scar_promote: SCAR entry parser, clusterer, and dream phase."""
from __future__ import annotations

from pathlib import Path
import pytest
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    return KontextDB(str(tmp_path / "test.db"))


def test_parse_scar_entries_extracts_source_date_and_text(tmp_path):
    from dream import _parse_scar_entries
    log = tmp_path / "project_x_log.md"
    log.write_text(
        "# Project X Log\n"
        "\n"
        "[Claude 2026-04-07] SCAR: TOCTOU race in add_entry allows duplicates. Grade: 9\n"
        "[Claude 2026-04-10] ARCH: decided to keep sqlite single-writer. Grade: 7\n"
        "[Claude 2026-04-12] SCAR: resolve_conflict write-back missed entries. Grade: 8\n",
        encoding="utf-8",
    )
    entries = _parse_scar_entries(log)
    assert len(entries) == 2
    assert entries[0]["source"] == "Claude 2026-04-07"
    assert entries[0]["date"] == "2026-04-07"
    assert "TOCTOU" in entries[0]["text"]
    assert entries[0]["grade"] == 9
    assert entries[0]["file"] == str(log)
    assert entries[1]["date"] == "2026-04-12"
    assert entries[1]["grade"] == 8


def test_parse_scar_entries_ignores_non_scar_tags(tmp_path):
    from dream import _parse_scar_entries
    log = tmp_path / "project_y_log.md"
    log.write_text(
        "[Claude 2026-04-01] ARCH: foo. Grade: 5\n"
        "[Claude 2026-04-02] EVO: bar. Grade: 3\n"
        "[Claude 2026-04-03] OPEN: baz. Grade: 4\n"
        "[Claude 2026-04-04] PERF: qux. Grade: 6\n",
        encoding="utf-8",
    )
    assert _parse_scar_entries(log) == []


def test_parse_scar_entries_missing_grade_defaults_to_zero(tmp_path):
    from dream import _parse_scar_entries
    log = tmp_path / "project_z_log.md"
    log.write_text(
        "[Claude 2026-04-15] SCAR: no grade here\n",
        encoding="utf-8",
    )
    entries = _parse_scar_entries(log)
    assert len(entries) == 1
    assert entries[0]["grade"] == 0


def test_cluster_scars_merges_similar_from_different_dates():
    from dream import _cluster_scars
    entries = [
        {"source": "Claude 2026-04-01", "date": "2026-04-01",
         "text": "TOCTOU race in add_entry allows duplicates",
         "grade": 9, "file": "a.md", "line": 1},
        {"source": "Claude 2026-04-08", "date": "2026-04-08",
         "text": "TOCTOU race in add_entry allowing duplicate inserts",
         "grade": 8, "file": "a.md", "line": 2},
        {"source": "Claude 2026-04-15", "date": "2026-04-15",
         "text": "completely different: pytest fixture leak in test_dream",
         "grade": 5, "file": "b.md", "line": 3},
    ]
    clusters = _cluster_scars(entries, threshold=0.70)
    assert len(clusters) == 1  # third is a singleton and filtered out
    c = clusters[0]
    assert c["count"] == 2
    assert c["dates"] == ["2026-04-01", "2026-04-08"]
    assert len(c["members"]) == 2


def test_cluster_scars_requires_distinct_dates():
    from dream import _cluster_scars
    entries = [
        {"source": "Claude 2026-04-01", "date": "2026-04-01",
         "text": "same bug same day", "grade": 9, "file": "a.md", "line": 1},
        {"source": "Claude 2026-04-01", "date": "2026-04-01",
         "text": "same bug same day", "grade": 9, "file": "a.md", "line": 2},
    ]
    clusters = _cluster_scars(entries, threshold=0.70)
    assert clusters == []  # same date = not independent, filtered


def test_cluster_scars_empty_input_returns_empty():
    from dream import _cluster_scars
    assert _cluster_scars([], threshold=0.70) == []
