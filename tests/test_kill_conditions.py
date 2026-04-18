# tests/test_kill_conditions.py
"""Tests for phase_kill_conditions (v0.1 self-improving agents kill-switch)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

import pytest

from db import KontextDB
from dream import phase_kill_conditions


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    yield db
    db.close()


def _insert_eval_row(db, *, run_id, query, recall_at_3, ts):
    db.conn.execute(
        """INSERT INTO retrieval_evals
           (run_id, query_text, category, held_out, recall_at_3, recall_at_5,
            mrr, latency_ms, mode, rank, top_files, git_sha, schema_version, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, query, "", 0, float(recall_at_3), 0.0, 0.0, 0,
         "rrf", "first_seen", "", "", 0, ts),
    )
    db.conn.commit()


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------

class TestDriftCheck:
    def test_insufficient_data_when_fewer_than_two_runs(self, db):
        # Only one run — cannot compute delta
        now = datetime.now(timezone.utc).isoformat()
        _insert_eval_row(db, run_id="r1", query="q1", recall_at_3=1.0, ts=now)
        _insert_eval_row(db, run_id="r1", query="q2", recall_at_3=0.5, ts=now)

        result = phase_kill_conditions(db, dry_run=True)
        assert result["drift_check"]["status"] == "insufficient_data"

    def test_insufficient_data_when_runs_too_close(self, db):
        # Two runs, but <6 days apart
        now = datetime.now(timezone.utc)
        ts1 = now.isoformat()
        ts2 = (now - timedelta(days=2)).isoformat()
        _insert_eval_row(db, run_id="r1", query="q1", recall_at_3=1.0, ts=ts2)
        _insert_eval_row(db, run_id="r2", query="q1", recall_at_3=0.9, ts=ts1)

        result = phase_kill_conditions(db, dry_run=True)
        assert result["drift_check"]["status"] == "insufficient_data"

    def test_ok_when_delta_within_threshold(self, db):
        now = datetime.now(timezone.utc)
        ts_old = (now - timedelta(days=7)).isoformat()
        ts_new = now.isoformat()
        # Identical query set, recall drifts by 2%
        for q, r_old, r_new in [("q1", 1.0, 1.0), ("q2", 1.0, 0.96)]:
            _insert_eval_row(db, run_id="old", query=q, recall_at_3=r_old, ts=ts_old)
            _insert_eval_row(db, run_id="new", query=q, recall_at_3=r_new, ts=ts_new)

        result = phase_kill_conditions(db, dry_run=True)
        assert result["drift_check"]["status"] == "ok"
        assert abs(result["drift_check"]["delta_recall_at_3"]) <= 0.05

    def test_fail_when_delta_exceeds_threshold(self, db):
        now = datetime.now(timezone.utc)
        ts_old = (now - timedelta(days=7)).isoformat()
        ts_new = now.isoformat()
        for q, r_old, r_new in [("q1", 1.0, 0.5), ("q2", 1.0, 0.7)]:
            _insert_eval_row(db, run_id="old", query=q, recall_at_3=r_old, ts=ts_old)
            _insert_eval_row(db, run_id="new", query=q, recall_at_3=r_new, ts=ts_new)

        result = phase_kill_conditions(db, dry_run=True)
        assert result["drift_check"]["status"] == "fail"
        assert abs(result["drift_check"]["delta_recall_at_3"]) > 0.05
        assert result["any_kill_fired"] is True


# ---------------------------------------------------------------------------
# Curation load
# ---------------------------------------------------------------------------

class TestCurationLoad:
    def test_ok_when_commits_under_threshold(self, db, monkeypatch):
        # Mock subprocess to return 3 commits
        import dream as dream_mod

        def fake_git_count(args_tuple):
            return 3

        monkeypatch.setattr(dream_mod, "_count_git_commits_since", fake_git_count)
        result = phase_kill_conditions(db, dry_run=True)
        assert result["curation_load"]["status"] == "ok"
        assert result["curation_load"]["commits_7d"] == 3

    def test_warn_when_commits_exceed_threshold(self, db, monkeypatch):
        import dream as dream_mod

        def fake_git_count(args_tuple):
            return 5

        monkeypatch.setattr(dream_mod, "_count_git_commits_since", fake_git_count)
        result = phase_kill_conditions(db, dry_run=True)
        assert result["curation_load"]["status"] == "warn"
        assert result["curation_load"]["commits_7d"] == 5
        assert result["any_kill_fired"] is True


# ---------------------------------------------------------------------------
# SCAR review rate
# ---------------------------------------------------------------------------

class TestScarReviewRate:
    def test_insufficient_data_when_no_promotions(self, db, monkeypatch):
        import dream as dream_mod

        # No commits at all on _scar_promotions.md in last 30d
        monkeypatch.setattr(dream_mod, "_scar_promotion_commits", lambda: [])
        result = phase_kill_conditions(db, dry_run=True)
        assert result["scar_review_rate"]["status"] == "insufficient_data"
        assert result["scar_review_rate"]["promoted_30d"] == 0


# ---------------------------------------------------------------------------
# Phase smoke — doesn't crash
# ---------------------------------------------------------------------------

class TestPhaseSmoke:
    def test_phase_runs_without_error(self, db):
        # Even with empty db + whatever git state, must return a dict
        result = phase_kill_conditions(db, dry_run=True)
        assert "drift_check" in result
        assert "curation_load" in result
        assert "scar_review_rate" in result
        assert "any_kill_fired" in result
        assert isinstance(result["any_kill_fired"], bool)
