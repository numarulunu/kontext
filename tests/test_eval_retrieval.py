"""Tests for retrieval_evals persistence (migration #18) and delta computation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    return KontextDB(str(db_path))


def test_persist_rows_writes_to_retrieval_evals(db):
    from eval_retrieval import _persist_rows
    rows = [
        {
            "query": "how do I sleep better?",
            "category": "domain-obvious",
            "held_out": False,
            "expected_files": "user_health_protocols.md",
            "top_files": "user_health_protocols.md|user_psychology.md",
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "mrr": 1.0,
            "latency_ms": 42,
        },
    ]
    run_id = "20260418T000000Z"
    _persist_rows(db, rows, run_id=run_id, mode="rrf", rank="first_seen",
                  git_sha="abc1234", schema_version=18)
    persisted = db.conn.execute(
        "SELECT run_id, query_text, category, recall_at_3, mode, rank, git_sha, schema_version "
        "FROM retrieval_evals WHERE run_id=?",
        (run_id,),
    ).fetchall()
    assert len(persisted) == 1
    r = persisted[0]
    assert r[0] == run_id
    assert r[1] == "how do I sleep better?"
    assert r[2] == "domain-obvious"
    assert abs(r[3] - 1.0) < 1e-9
    assert r[4] == "rrf"
    assert r[5] == "first_seen"
    assert r[6] == "abc1234"
    assert r[7] == 18


def test_compute_delta_returns_none_prior_for_first_run(db):
    from eval_retrieval import _persist_rows, _compute_delta
    _persist_rows(db, [{
        "query": "q1", "category": "domain-obvious", "held_out": False,
        "expected_files": "a.md", "top_files": "a.md",
        "recall_at_3": 0.8, "recall_at_5": 0.9, "mrr": 0.8, "latency_ms": 10,
    }], run_id="run_1", mode="rrf", rank="first_seen", git_sha="x", schema_version=18)
    delta = _compute_delta(db, current_run_id="run_1", mode="rrf", rank="first_seen")
    assert delta is None or delta["prior_run_id"] is None


def test_compute_delta_computes_mean_recall_at_3_diff(db):
    from eval_retrieval import _persist_rows, _compute_delta
    base_row = lambda q, r3: {
        "query": q, "category": "domain-obvious", "held_out": False,
        "expected_files": "a.md", "top_files": "a.md",
        "recall_at_3": r3, "recall_at_5": r3, "mrr": r3, "latency_ms": 10,
    }
    _persist_rows(db, [base_row("q1", 0.5), base_row("q2", 0.6)],
                  run_id="run_A", mode="rrf", rank="first_seen",
                  git_sha="a", schema_version=18)
    _persist_rows(db, [base_row("q1", 0.8), base_row("q2", 0.9)],
                  run_id="run_B", mode="rrf", rank="first_seen",
                  git_sha="b", schema_version=18)
    delta = _compute_delta(db, current_run_id="run_B", mode="rrf", rank="first_seen")
    assert delta["prior_run_id"] == "run_A"
    assert abs(delta["recall_at_3_delta"] - 0.3) < 1e-9  # 0.85 - 0.55
