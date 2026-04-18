# tests/test_migration_18.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import KontextDB


REQUIRED_COLUMNS = {
    "id",
    "run_id",
    "query_text",
    "category",
    "held_out",
    "recall_at_3",
    "recall_at_5",
    "mrr",
    "latency_ms",
    "mode",
    "rank",
    "top_files",
    "git_sha",
    "schema_version",
    "ts",
}

REQUIRED_INDEXES = {"idx_re_run_id", "idx_re_ts"}


def test_retrieval_evals_table_created(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    try:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='retrieval_evals'"
        ).fetchone()
        assert row is not None, "retrieval_evals table not created"
    finally:
        db.close()


def test_retrieval_evals_columns(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    try:
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(retrieval_evals)").fetchall()}
        missing = REQUIRED_COLUMNS - cols
        assert not missing, f"Missing columns: {missing}"
    finally:
        db.close()


def test_retrieval_evals_indexes(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    try:
        idx = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='retrieval_evals'"
            ).fetchall()
        }
        missing = REQUIRED_INDEXES - idx
        assert not missing, f"Missing indexes: {missing}"
    finally:
        db.close()
