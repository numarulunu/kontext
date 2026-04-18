# Self-Improving Agents v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end self-improvement loop that can be killed on measurable evidence: (1) persist retrieval eval results to a new `retrieval_evals` table and compute pre/post deltas; (2) add a `scar_promote` dream phase that scans `project_*_log.md` files, clusters SCAR entries appearing in ≥2 independent occurrences, and emits a git-tracked `_scar_promotions.md` for manual review.

**Architecture:** Extend the already-working `eval_retrieval.py` harness to write its rows to a new SQLite table (migration #18) in addition to the existing CSV output, and compute a delta table against the most recent prior run of the same mode+rank. Add one new phase function to `dream.py` (`phase_scar_promote`) that reuses dream's existing locking and transaction scaffolding; the phase parses `[Source YYYY-MM-DD] SCAR: ... Grade: N` lines from `project_*_log.md` files under `$KONTEXT_MEMORY_DIR`, clusters by text similarity ≥0.70, and emits `_scar_promotions.md` at repo root. No new MCP tools, no new daemons, no `prompt_scores`/`skill_events`/`self_debug_log` tables — those were rejected by mastermind challenger round as vapor without a ground-truth signal.

**Tech Stack:** Python 3 (stdlib: `difflib.SequenceMatcher`, `sqlite3`, `argparse`, `datetime`, `pathlib`, `re`); existing Kontext modules (`db.KontextDB`, `retrieval.expand`, `retrieval.rrf_merge`, `sentence_transformers`); existing test framework (pytest with `tmp_path` fixture pattern from `tests/test_dream.py`).

**Source decision:** `docs/mastermind-reports/2026-04-18-self-improving-agents-decision.md`

**Three kill conditions (verify at end — if any fires, revert and stop):**
1. Replay drift >5% on unchanged corpus in 7 days (`recall@3` on identical queries/corpus noise-dominates the effect size we'd try to detect).
2. Curation cost >1 hour/week over the first 4 weeks of ownership.
3. SCAR review rate <50% in first 30 days (measured by git commits touching `_scar_promotions.md`).

---

## File Structure

**Modified:**
- `db.py` (lines 443-460 + new migration function near line 386) — add migration #18 and register it
- `eval_retrieval.py` (around `main()` lines 356-391) — persist rows to `retrieval_evals` and compute+print delta
- `dream.py` (after `phase_purge` line 370 + `PHASES` dict near line 396 + module docstring near line 8) — add `phase_scar_promote` and register it

**Created:**
- `tests/test_eval_retrieval.py` — unit + integration tests for new persistence and delta
- `tests/test_scar_promote.py` — unit + integration tests for SCAR parser, clusterer, dream phase
- `_scar_promotions.md` (repo root, git-tracked) — written by `phase_scar_promote`; created on first run

**Touched for baseline commit (Task 8):**
- `docs/eval-baselines/eval_results_rrf_<ts>.csv` — committed baseline CSV (follow existing pattern)

**Environment:**
- `KONTEXT_MEMORY_DIR` env var — if unset, the phase falls back to auto-detecting the Kontext memory export dir. Default fallback order: `$KONTEXT_MEMORY_DIR` → `$HOME/.claude/projects/C--Users-Gaming-PC-Desktop-Claude-Kontext/memory` → skip phase with log warning.

---

## Part 1 — Retrieval Evals Persistence + Delta

### Task 1: Migration #18 — `retrieval_evals` table

**Files:**
- Modify: `db.py` (add function near line 386; append to `MIGRATIONS` list line 459)
- Test: `tests/test_eval_retrieval.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_retrieval.py`:

```python
"""Tests for retrieval_evals persistence (migration #18) and delta computation."""
from __future__ import annotations

import pytest
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    return KontextDB(str(db_path))


def test_migration_18_creates_retrieval_evals_table(db):
    cols = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(retrieval_evals)").fetchall()
    }
    expected = {
        "id", "run_id", "query_text", "category", "held_out",
        "recall_at_3", "recall_at_5", "mrr", "latency_ms",
        "mode", "rank", "top_files", "git_sha", "schema_version", "ts",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_migration_18_creates_run_id_index(db):
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='retrieval_evals'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_re_run_id" in names
    assert "idx_re_ts" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_retrieval.py -v`
Expected: FAIL with "no such table: retrieval_evals" (or similar).

- [ ] **Step 3: Write the migration**

Add to `db.py` right after `_migration_17_sessions_files_loaded` (around line 385):

```python
def _migration_18_retrieval_evals(conn):
    """Persist eval_retrieval.py results for pre/post delta tracking."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS retrieval_evals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            query_text TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            held_out INTEGER NOT NULL DEFAULT 0,
            recall_at_3 REAL NOT NULL DEFAULT 0.0,
            recall_at_5 REAL NOT NULL DEFAULT 0.0,
            mrr REAL NOT NULL DEFAULT 0.0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'rrf',
            rank TEXT NOT NULL DEFAULT 'first_seen',
            top_files TEXT DEFAULT '',
            git_sha TEXT DEFAULT '',
            schema_version INTEGER DEFAULT 0,
            ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_re_run_id ON retrieval_evals(run_id);
        CREATE INDEX IF NOT EXISTS idx_re_ts ON retrieval_evals(ts);
    """)
```

Append to the `MIGRATIONS` list in `db.py` line 459 (after the `(17, ...)` row):

```python
    (18, _migration_18_retrieval_evals),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_retrieval.py -v`
Expected: PASS — both `test_migration_18_creates_retrieval_evals_table` and `test_migration_18_creates_run_id_index`.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_eval_retrieval.py
git commit -m "feat(db): add retrieval_evals table (migration #18)"
```

---

### Task 2: Persist eval rows to `retrieval_evals` alongside CSV

**Files:**
- Modify: `eval_retrieval.py` (add helper `_persist_rows` after line 241 `_mean`; call it in `main()` after CSV write line 384)
- Test: `tests/test_eval_retrieval.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_retrieval.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_retrieval.py::test_persist_rows_writes_to_retrieval_evals -v`
Expected: FAIL with `AttributeError` or `ImportError` for `_persist_rows`.

- [ ] **Step 3: Implement `_persist_rows`**

Add to `eval_retrieval.py` right after the `_mean` function (around line 242):

```python
def _persist_rows(db, rows: list[dict], *, run_id: str, mode: str, rank: str,
                  git_sha: str, schema_version: int) -> int:
    """Persist eval rows to retrieval_evals table. Returns count inserted."""
    cur = db.conn.cursor()
    inserted = 0
    for r in rows:
        cur.execute(
            """INSERT INTO retrieval_evals
               (run_id, query_text, category, held_out, recall_at_3, recall_at_5,
                mrr, latency_ms, mode, rank, top_files, git_sha, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                r["query"],
                r.get("category", ""),
                1 if r.get("held_out") else 0,
                float(r["recall_at_3"]),
                float(r["recall_at_5"]),
                float(r["mrr"]),
                int(r["latency_ms"]),
                mode,
                rank,
                r.get("top_files", ""),
                git_sha,
                schema_version,
            ),
        )
        inserted += 1
    db.conn.commit()
    return inserted
```

- [ ] **Step 4: Wire `_persist_rows` into `main()`**

In `eval_retrieval.py` `main()`, right after the CSV write block (after `_log.info(f"END rows={len(rows)} out={out_path}")` line 391), add:

```python
    # Persist to retrieval_evals for pre/post delta tracking (v0.1 self-improvement loop)
    try:
        import subprocess
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        git_sha = ""
    try:
        schema_version = int(db.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0])
    except Exception:
        schema_version = 0
    try:
        inserted = _persist_rows(db, rows, run_id=ts, mode=mode, rank=args.rank,
                                 git_sha=git_sha, schema_version=schema_version)
        print(f"Persisted {inserted} rows to retrieval_evals (run_id={ts}).")
        _log.info(f"PERSISTED rows={inserted} run_id={ts} git={git_sha} schema={schema_version}")
    except Exception as e:
        print(f"WARNING: persist to retrieval_evals failed: {e}", file=sys.stderr)
        _log.warning(f"PERSIST_FAIL: {e}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_eval_retrieval.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add eval_retrieval.py tests/test_eval_retrieval.py
git commit -m "feat(eval): persist eval results to retrieval_evals table"
```

---

### Task 3: Delta-from-baseline computation + display

**Files:**
- Modify: `eval_retrieval.py` (add `_compute_delta` helper after `_persist_rows`; call from `main()` before final log line)
- Test: `tests/test_eval_retrieval.py` (add 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_retrieval.py`:

```python
def test_compute_delta_returns_zero_for_first_run(db):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_eval_retrieval.py -v`
Expected: FAIL with `ImportError: cannot import name '_compute_delta'`.

- [ ] **Step 3: Implement `_compute_delta`**

Add to `eval_retrieval.py` right after `_persist_rows`:

```python
def _compute_delta(db, *, current_run_id: str, mode: str, rank: str) -> dict | None:
    """Compute mean-metric delta vs the most recent prior run of same mode+rank.

    Returns None if there's no prior run. Otherwise returns:
        {"prior_run_id", "recall_at_3_delta", "recall_at_5_delta",
         "mrr_delta", "latency_ms_delta", "n_current", "n_prior"}
    """
    prior = db.conn.execute(
        """SELECT run_id FROM retrieval_evals
           WHERE mode=? AND rank=? AND run_id != ?
           ORDER BY ts DESC LIMIT 1""",
        (mode, rank, current_run_id),
    ).fetchone()
    if prior is None:
        return {"prior_run_id": None}
    prior_run_id = prior[0]

    def _means(run_id: str):
        row = db.conn.execute(
            """SELECT AVG(recall_at_3), AVG(recall_at_5), AVG(mrr),
                      AVG(latency_ms), COUNT(*) FROM retrieval_evals
               WHERE run_id=? AND mode=? AND rank=?""",
            (run_id, mode, rank),
        ).fetchone()
        return {
            "r3": row[0] or 0.0, "r5": row[1] or 0.0,
            "mrr": row[2] or 0.0, "lat": row[3] or 0.0, "n": row[4] or 0,
        }

    cur = _means(current_run_id)
    prv = _means(prior_run_id)
    return {
        "prior_run_id": prior_run_id,
        "recall_at_3_delta": cur["r3"] - prv["r3"],
        "recall_at_5_delta": cur["r5"] - prv["r5"],
        "mrr_delta": cur["mrr"] - prv["mrr"],
        "latency_ms_delta": cur["lat"] - prv["lat"],
        "n_current": cur["n"],
        "n_prior": prv["n"],
    }
```

- [ ] **Step 4: Wire `_compute_delta` into `main()`**

In `eval_retrieval.py` `main()`, right after the `PERSISTED` log line added in Task 2, add:

```python
    # Compute delta vs most recent prior run of same mode+rank
    try:
        delta = _compute_delta(db, current_run_id=ts, mode=mode, rank=args.rank)
        if delta is None or delta.get("prior_run_id") is None:
            print("Delta: (no prior run of this mode+rank — baseline established)")
        else:
            sign = lambda x: ("+" if x >= 0 else "") + f"{x:.3f}"
            print()
            print("Delta vs prior run:")
            print(f"  prior_run_id   = {delta['prior_run_id']}")
            print(f"  recall@3 delta = {sign(delta['recall_at_3_delta'])} (n={delta['n_current']} vs {delta['n_prior']})")
            print(f"  recall@5 delta = {sign(delta['recall_at_5_delta'])}")
            print(f"  mrr delta      = {sign(delta['mrr_delta'])}")
            print(f"  latency delta  = {sign(delta['latency_ms_delta'])}ms")
        _log.info(f"DELTA {delta}")
    except Exception as e:
        print(f"WARNING: delta computation failed: {e}", file=sys.stderr)
        _log.warning(f"DELTA_FAIL: {e}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_eval_retrieval.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add eval_retrieval.py tests/test_eval_retrieval.py
git commit -m "feat(eval): compute and display pre/post delta vs prior run"
```

---

## Part 2 — SCAR Auto-Promotion Dream Phase

### Task 4: SCAR parser — extract `[Source YYYY-MM-DD] SCAR: ... Grade: N` entries

**Files:**
- Modify: `dream.py` (add helper functions before `PHASES` dict, around line 380)
- Test: `tests/test_scar_promote.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_scar_promote.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scar_promote.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_scar_entries'`.

- [ ] **Step 3: Implement `_parse_scar_entries`**

Add to `dream.py` right before the `PHASES` dict (around line 380):

```python
# ---------------------------------------------------------------------------
# Phase 6: SCAR auto-promotion (self-improvement loop v0.1)
# ---------------------------------------------------------------------------

_SCAR_LINE_RE = re.compile(
    r"^\[(?P<source>[^\]]+)\]\s+SCAR:\s*(?P<text>.*?)(?:\.\s*Grade:\s*(?P<grade>\d+))?\s*$"
)
_DATE_IN_SOURCE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_scar_entries(path: Path) -> list[dict]:
    """Parse SCAR entries from a project_*_log.md file.

    Returns list of dicts with keys: source, date, text, grade, file, line.
    Lines that don't match the SCAR pattern are skipped (ARCH/EVO/OPEN/PERF are ignored).
    """
    entries: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return entries

    for lineno, line in enumerate(raw.splitlines(), start=1):
        m = _SCAR_LINE_RE.match(line.strip())
        if not m:
            continue
        source = m.group("source").strip()
        text = m.group("text").strip().rstrip(".")
        grade_s = m.group("grade")
        grade = int(grade_s) if grade_s else 0
        dm = _DATE_IN_SOURCE_RE.search(source)
        date = dm.group(1) if dm else ""
        entries.append({
            "source": source,
            "date": date,
            "text": text,
            "grade": grade,
            "file": str(path),
            "line": lineno,
        })
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scar_promote.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dream.py tests/test_scar_promote.py
git commit -m "feat(dream): add SCAR entry parser for project_*_log.md files"
```

---

### Task 5: SCAR clustering — find patterns with ≥2 independent entries

**Files:**
- Modify: `dream.py` (add `_cluster_scars` helper right after `_parse_scar_entries`)
- Test: `tests/test_scar_promote.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scar_promote.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scar_promote.py -v`
Expected: 3 new tests FAIL with `ImportError`.

- [ ] **Step 3: Implement `_cluster_scars`**

Add to `dream.py` right after `_parse_scar_entries`:

```python
def _cluster_scars(entries: list[dict], *, threshold: float = 0.70) -> list[dict]:
    """Cluster SCAR entries by text similarity ≥ threshold.

    Only returns clusters where:
      - count >= 2
      - the cluster spans ≥2 distinct `date` values (independence requirement)

    Returns list of dicts: {representative, members, count, dates, max_grade, files}.
    """
    if not entries:
        return []

    # Sort by text for deterministic cluster assignment
    sorted_entries = sorted(entries, key=lambda e: e["text"])
    clusters: list[list[dict]] = []

    for e in sorted_entries:
        assigned = False
        for cluster in clusters:
            rep = cluster[0]["text"]
            if similarity(rep, e["text"]) >= threshold:
                cluster.append(e)
                assigned = True
                break
        if not assigned:
            clusters.append([e])

    out: list[dict] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        dates = sorted({m["date"] for m in cluster if m["date"]})
        if len(dates) < 2:
            continue  # same date = not independent
        # Pick representative as highest-grade member
        rep = max(cluster, key=lambda m: m["grade"])
        out.append({
            "representative": rep["text"],
            "members": cluster,
            "count": len(cluster),
            "dates": dates,
            "max_grade": rep["grade"],
            "files": sorted({m["file"] for m in cluster}),
        })
    # Sort output by count desc, then max_grade desc
    out.sort(key=lambda c: (-c["count"], -c["max_grade"]))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scar_promote.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dream.py tests/test_scar_promote.py
git commit -m "feat(dream): add SCAR clustering with independence requirement"
```

---

### Task 6: `phase_scar_promote` writes `_scar_promotions.md`

**Files:**
- Modify: `dream.py` (add phase function after `_cluster_scars`; register in `PHASES` dict line 396; update module docstring line 8-19)
- Test: `tests/test_scar_promote.py` (add dream-phase tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scar_promote.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scar_promote.py -v`
Expected: 3 new tests FAIL with `ImportError`.

- [ ] **Step 3: Implement `phase_scar_promote`**

Add to `dream.py` right after `_cluster_scars`:

```python
def _resolve_memory_dir() -> Path | None:
    """Locate the memory export dir. Order: env → ~/.claude fallback → None."""
    env = os.environ.get("KONTEXT_MEMORY_DIR")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    fallback = Path.home() / ".claude" / "projects" / \
        "C--Users-Gaming-PC-Desktop-Claude-Kontext" / "memory"
    return fallback if fallback.exists() else None


def _resolve_promotions_path() -> Path:
    env = os.environ.get("KONTEXT_SCAR_PROMOTIONS_PATH")
    if env:
        return Path(env).expanduser()
    return Path(__file__).parent / "_scar_promotions.md"


def _render_promotions_md(clusters: list[dict], *, run_ts: str) -> str:
    if not clusters:
        return (
            f"# SCAR Promotions — {run_ts}\n\n"
            "No clusters found this run (no SCAR patterns appeared in ≥2 "
            "independent entries from distinct dates).\n"
        )
    lines = [f"# SCAR Promotions — {run_ts}", ""]
    lines.append(
        f"Found **{len(clusters)}** cluster(s). Review each, then either:\n"
        "- write a `kontext_write` rule that prevents recurrence, OR\n"
        "- delete its block below and commit to dismiss.\n"
    )
    for i, c in enumerate(clusters, start=1):
        lines.append(f"## Cluster {i} — count={c['count']}, max_grade={c['max_grade']}")
        lines.append("")
        lines.append(f"**Representative:** {c['representative']}")
        lines.append("")
        lines.append(f"**Distinct dates:** {', '.join(c['dates'])}")
        lines.append(f"**Source files:** {', '.join(Path(f).name for f in c['files'])}")
        lines.append("")
        lines.append("**Members:**")
        for m in c["members"]:
            lines.append(
                f"- `[{m['source']}]` {m['text']} (grade {m['grade']}, "
                f"{Path(m['file']).name}:{m['line']})"
            )
        lines.append("")
        lines.append("**Suggested rule:** _<fill in to promote; delete this block to dismiss>_")
        lines.append("")
    return "\n".join(lines) + "\n"


def phase_scar_promote(db: KontextDB, dry_run: bool = False) -> dict:
    """Scan project_*_log.md files, cluster SCAR entries with ≥2 independent
    occurrences, and emit _scar_promotions.md for manual review.

    Does NOT modify the Kontext DB. Output is a single git-tracked file the
    user reviews. Revert = `git checkout _scar_promotions.md`.
    """
    memory_dir = _resolve_memory_dir()
    promotions_path = _resolve_promotions_path()

    if memory_dir is None:
        _log.warning("SCAR_PROMOTE: no memory dir found (set KONTEXT_MEMORY_DIR)")
        return {"clusters_found": 0, "entries_scanned": 0, "files_scanned": 0, "skipped": True}

    log_files = sorted(memory_dir.glob("project_*_log.md"))
    all_entries: list[dict] = []
    for f in log_files:
        all_entries.extend(_parse_scar_entries(f))

    clusters = _cluster_scars(all_entries, threshold=0.70)

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not dry_run:
        body = _render_promotions_md(clusters, run_ts=run_ts)
        promotions_path.write_text(body, encoding="utf-8")
        _log.info(
            f"SCAR_PROMOTE wrote={promotions_path} clusters={len(clusters)} "
            f"entries={len(all_entries)} files={len(log_files)}"
        )
    else:
        _log.info(
            f"SCAR_PROMOTE dry-run clusters={len(clusters)} "
            f"entries={len(all_entries)} files={len(log_files)}"
        )

    return {
        "clusters_found": len(clusters),
        "entries_scanned": len(all_entries),
        "files_scanned": len(log_files),
    }
```

- [ ] **Step 4: Register phase in `PHASES` dict**

In `dream.py` at the `PHASES` dict around line 396, add the new entry:

```python
PHASES = {
    "dedup": phase_dedup,
    "normalize": phase_normalize,
    "resolve": phase_resolve,
    "compress": phase_compress,
    "purge": phase_purge,
    "scar_promote": phase_scar_promote,
}
```

- [ ] **Step 5: Update module docstring**

In `dream.py` replace the Phases section in the docstring (lines 8-13) with:

```
Phases:
  1. Dedup       — merge near-duplicate entries (same file, similar fact)
  2. Normalize   — convert relative dates to absolute, clean formatting
  3. Resolve     — auto-resolve stale conflicts (>7 days, same file, one is newer)
  4. Compress    — summarize cold-tier entries into compact one-liners
  5. Purge       — delete entries with grade <= 1 that haven't been accessed in 120+ days
  6. ScarPromote — scan project_*_log.md, cluster SCARs appearing in ≥2 independent
                   entries, emit _scar_promotions.md for manual review
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_scar_promote.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add dream.py tests/test_scar_promote.py
git commit -m "feat(dream): add phase_scar_promote — auto-cluster SCAR patterns for review"
```

---

### Task 7: Integration smoke — `python dream.py --phase scar_promote`

**Files:**
- Test: `tests/test_scar_promote.py` (add CLI invocation test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scar_promote.py`:

```python
def test_dream_cli_runs_scar_promote_phase_only(tmp_path, monkeypatch):
    """Invoking dream.py --phase scar_promote runs only that phase and exits 0."""
    import subprocess, sys
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "project_alpha_log.md").write_text(
        "[Claude 2026-04-01] SCAR: same bug one way. Grade: 7\n"
        "[Claude 2026-04-10] SCAR: same bug another phrasing. Grade: 7\n",
        encoding="utf-8",
    )
    promotions = tmp_path / "_scar_promotions.md"
    env = {
        **dict(__import__("os").environ),
        "KONTEXT_MEMORY_DIR": str(memory_dir),
        "KONTEXT_SCAR_PROMOTIONS_PATH": str(promotions),
        "KONTEXT_DB_PATH": str(tmp_path / "test.db"),
    }
    result = subprocess.run(
        [sys.executable, "dream.py", "--phase", "scar_promote"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert promotions.exists()
```

- [ ] **Step 2: Run test to verify it passes**

The phase is already wired into `PHASES` in Task 6, so `--phase scar_promote` should work without further code changes — the test checks the CLI path.

Run: `pytest tests/test_scar_promote.py::test_dream_cli_runs_scar_promote_phase_only -v`
Expected: PASS. If it fails because `dream.py` CLI doesn't support `--phase scar_promote`, inspect the `argparse` choices in `dream.py` `main()` and add `"scar_promote"` to the `--phase` choices list (search for `"--phase"` in `dream.py`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_scar_promote.py
git commit -m "test(dream): integration smoke for scar_promote CLI invocation"
```

---

## Part 3 — Baseline + Verification

### Task 8: Run full harness end-to-end, commit baseline

**Files:**
- Create: `docs/eval-baselines/eval_results_rrf_<ts>.csv` (auto-generated)
- Run: `python -m eval_retrieval --mode=rrf`
- Run: `python dream.py --phase scar_promote`

- [ ] **Step 1: Run the full test suite to confirm nothing regressed**

Run: `pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 2: Run eval_retrieval to establish baseline and persist first rows**

Run: `python -m eval_retrieval --mode=rrf`
Expected output includes:
- Per-query results (30 rows)
- `category × split` summary table
- `CSV written: docs/eval-baselines/eval_results_rrf_<ts>.csv`
- `Persisted 30 rows to retrieval_evals (run_id=<ts>).`
- `Delta: (no prior run of this mode+rank — baseline established)` (first run)

- [ ] **Step 3: Run eval_retrieval a second time immediately to confirm delta path works**

Run: `python -m eval_retrieval --mode=rrf`
Expected output includes the `Delta vs prior run:` block with small near-zero deltas (unchanged corpus → unchanged retrieval → delta ≈ 0).

- [ ] **Step 4: Run the SCAR promotion phase on real memory dir**

Run: `python dream.py --phase scar_promote`
Expected:
- Exit code 0
- `_scar_promotions.md` written at Kontext repo root
- Inspect the file manually: at least one cluster should appear if multiple `project_*_log.md` files contain related SCAR entries

- [ ] **Step 5: Verify kill-condition measurement plumbing**

Open the new `retrieval_evals` table and confirm:

```bash
sqlite3 kontext.db "SELECT run_id, COUNT(*), AVG(recall_at_3) FROM retrieval_evals GROUP BY run_id;"
```

Expected: 2 rows (the two runs from Steps 2-3), each with 30 queries and identical recall@3 averages (to within floating-point).

- [ ] **Step 6: Commit baseline CSV and promotions review file**

```bash
git add docs/eval-baselines/eval_results_rrf_*.csv _scar_promotions.md
git commit -m "chore(eval): commit baseline eval CSV and initial SCAR promotions"
```

- [ ] **Step 7: Final integration check — run dream full cycle**

Run: `python dream.py --dry-run`
Expected: reports all 6 phases including `scar_promote` without modifying anything. Confirms the phase is wired into the full dream cycle, not only standalone.

---

## Verification Against Mastermind Spec

| Mastermind v0.1 requirement | Task(s) |
|-----------------------------|---------|
| Frozen-eval replay harness with YAML query set | Task 2-3 (eval_retrieval.py already exists; we extend it) |
| Emit `recall@3` to a `retrieval_evals` table | Tasks 1, 2 |
| Git-pinned YAML baseline | Existing `eval_retrieval.yaml` already git-tracked |
| SCAR auto-promotion from `project_*_log.md` | Tasks 4, 5, 6 |
| ≥2 independent occurrences requirement | Task 5 (distinct-dates clustering check) |
| Git-tracked `_scar_promotions.md` with one-command revert | Task 6 (`git checkout _scar_promotions.md` reverts) |
| Integrated into dream cycle (not a new daemon) | Tasks 6, 7, 8 step 7 |
| No `prompt_scores`, no `skill_events`, no `self_debug_log` | Plan does not add these |

## Kill-Condition Measurement

After 7 days of use:
- **Drift check:** re-run `python -m eval_retrieval --mode=rrf` on unchanged corpus. If `recall@3` delta >5%, noise dominates — revert.
- **Curation check:** log time spent curating/refreshing `eval_retrieval.yaml` and `_scar_promotions.md`. If >1 hr/week over 4 weeks, revert.
- **Review rate:** `git log --since="30 days ago" -- _scar_promotions.md | wc -l`. If fewer than half the SCAR promotions were touched, revert.

Revert command (nuclear): `git revert <commit-range>` for the 8 feature commits, then `python -c "from db import KontextDB; KontextDB().conn.executescript('DROP TABLE IF EXISTS retrieval_evals;')"`.
