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


def test_phase_scar_promote_writes_promotions_file(db, tmp_path, monkeypatch):
    from dream import phase_scar_promote

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "project_alpha_log.md").write_text(
        "[Claude 2026-04-01] SCAR: missing lockfile check on write. Grade: 9\n"
        "[Claude 2026-04-08] SCAR: no lockfile check on write path. Grade: 8\n"
        "[Claude 2026-04-15] ARCH: decided on WAL mode. Grade: 5\n",
        encoding="utf-8",
    )
    (memory_dir / "project_beta_log.md").write_text(
        "[Claude 2026-04-02] SCAR: totally different pytest leak. Grade: 6\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("KONTEXT_MEMORY_DIR", str(memory_dir))
    promotions = tmp_path / "_scar_promotions.md"
    monkeypatch.setenv("KONTEXT_SCAR_PROMOTIONS_PATH", str(promotions))

    result = phase_scar_promote(db, dry_run=False)
    assert result["clusters_found"] == 1
    assert result["entries_scanned"] >= 3
    assert result["files_scanned"] == 2
    assert promotions.exists()
    body = promotions.read_text(encoding="utf-8")
    assert "lockfile" in body.lower()
    assert "2026-04-01" in body
    assert "2026-04-08" in body


def test_phase_scar_promote_dry_run_does_not_write(db, tmp_path, monkeypatch):
    from dream import phase_scar_promote
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "project_a_log.md").write_text(
        "[Claude 2026-04-01] SCAR: shared bug. Grade: 7\n"
        "[Claude 2026-04-10] SCAR: shared bug variant. Grade: 6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KONTEXT_MEMORY_DIR", str(memory_dir))
    promotions = tmp_path / "_scar_promotions.md"
    monkeypatch.setenv("KONTEXT_SCAR_PROMOTIONS_PATH", str(promotions))

    result = phase_scar_promote(db, dry_run=True)
    assert result["clusters_found"] >= 1
    assert not promotions.exists()


def test_phase_scar_promote_handles_missing_memory_dir(db, tmp_path, monkeypatch):
    from dream import phase_scar_promote
    monkeypatch.setenv("KONTEXT_MEMORY_DIR", str(tmp_path / "does_not_exist"))
    monkeypatch.setenv(
        "KONTEXT_SCAR_PROMOTIONS_PATH", str(tmp_path / "_scar_promotions.md")
    )
    result = phase_scar_promote(db, dry_run=False)
    assert result["clusters_found"] == 0
    assert result["files_scanned"] == 0
    assert result.get("skipped") is True


def test_phase_scar_promote_registered_in_phases_dict():
    """phase_scar_promote must be registered in the PHASES dispatch dict."""
    from dream import PHASES, phase_scar_promote
    assert "scar_promote" in PHASES
    assert PHASES["scar_promote"] is phase_scar_promote


def test_dream_cli_runs_scar_promote_phase_only(tmp_path):
    """Invoking dream.py --phase scar_promote runs only that phase and exits 0."""
    import os
    import subprocess
    import sys

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "project_alpha_log.md").write_text(
        "[Claude 2026-04-01] SCAR: same bug one way. Grade: 7\n"
        "[Claude 2026-04-10] SCAR: same bug another phrasing. Grade: 7\n",
        encoding="utf-8",
    )
    promotions = tmp_path / "_scar_promotions.md"
    env = {
        **os.environ,
        "KONTEXT_MEMORY_DIR": str(memory_dir),
        "KONTEXT_SCAR_PROMOTIONS_PATH": str(promotions),
        "KONTEXT_DB_PATH": str(tmp_path / "test.db"),
    }
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "dream.py", "--phase", "scar_promote"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert promotions.exists()


def test_dream_full_cycle_includes_scar_promote(db, tmp_path, monkeypatch):
    """Running run_dream() (the full cycle) invokes phase_scar_promote alongside
    the other phases. Guards against future regressions that silently drop the
    phase from the PHASES dispatch or the runner loop."""
    from dream import dream

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "project_gamma_log.md").write_text(
        "[Claude 2026-04-01] SCAR: duplicate inserts in add_entry. Grade: 8\n"
        "[Claude 2026-04-09] SCAR: duplicate inserts during add_entry path. Grade: 7\n",
        encoding="utf-8",
    )
    promotions = tmp_path / "_scar_promotions.md"
    monkeypatch.setenv("KONTEXT_MEMORY_DIR", str(memory_dir))
    monkeypatch.setenv("KONTEXT_SCAR_PROMOTIONS_PATH", str(promotions))

    report = dream(db, dry_run=True)
    assert "scar_promote" in report, f"scar_promote missing from report: {list(report.keys())}"
    scar = report["scar_promote"]
    assert scar["files_scanned"] == 1
    assert scar["entries_scanned"] == 2
    assert scar["clusters_found"] == 1
