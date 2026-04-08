# SMAC Follow-up Plan — Deferred Fixes

**Source:** `docs/smac-reports/2026-04-07-how-best-should-i-optimize.md`
**Status:** top-15 fixes already applied (commit in progress). This plan covers the 6 larger items that were skipped because each needs its own session.

Ordered by ROI. Each task is independent — pick any one per session.

---

## Task 1 — FTS5 full-text search (replaces LIKE)

**Why:** `search_entries` uses `LIKE '%query%'` which can never use an index. Every call is a full table scan. Compounds inside `dream.phase_resolve` (was fixed separately with exact-match lookup) and inside `kontext_query`'s `search` path. Already escapes wildcards now but still O(n).

**Scope:** ~80 lines, 1 migration, test updates.

**Steps:**
1. Add `entries_fts` virtual table to `_create_tables` in `db.py`:
   ```sql
   CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
       fact, content='entries', content_rowid='id',
       tokenize='trigram'
   );
   ```
2. Add triggers to keep FTS in sync with `entries`:
   ```sql
   CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
       INSERT INTO entries_fts(rowid, fact) VALUES (new.id, new.fact); END;
   CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
       INSERT INTO entries_fts(entries_fts, rowid, fact) VALUES('delete', old.id, old.fact); END;
   CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
       INSERT INTO entries_fts(entries_fts, rowid, fact) VALUES('delete', old.id, old.fact);
       INSERT INTO entries_fts(rowid, fact) VALUES (new.id, new.fact); END;
   ```
3. Backfill for existing rows: `INSERT INTO entries_fts(rowid, fact) SELECT id, fact FROM entries` — gated on schema_version (see Task 2) or a "fts_initialized" marker row.
4. Rewrite `search_entries` to use `entries_fts MATCH ?` with `bm25(entries_fts)` ranking, keeping the optional `file`/`tier`/`min_grade` filters as outer predicates.
5. Update `tests/test_db.py::TestSearchEntries` to cover:
   - Substring match (trigram handles this)
   - FTS ranking order differs from current `ORDER BY grade DESC`
   - Wildcard safety (no longer relevant but keep the test)

**Gotchas:**
- SQLite must be compiled with FTS5 (standard on Windows/macOS Python builds; verify with `sqlite3.sqlite_version` and `PRAGMA compile_options`).
- `tokenize='trigram'` requires SQLite ≥3.34. Python 3.11 ships with 3.37+. Safe.
- Trigger order matters — `entries_ad` must run before `entries_au` in the script.

**Risk:** LOW. Triggers are the standard FTS5 pattern. Tests will catch regressions.

**Files:** `db.py`, `tests/test_db.py`.

---

## Task 2 — `schema_version` table + proper migration framework

**Why:** Current `_migrate` runs `PRAGMA table_info` on every startup and has no rollback. Adding each new migration means adding another column-existence check. Prevents Task 1 from cleanly backfilling FTS on existing DBs.

**Scope:** ~50 lines.

**Steps:**
1. Add table at the top of `_create_tables`:
   ```sql
   CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
   INSERT OR IGNORE INTO schema_version VALUES (0);
   ```
2. Rewrite `_migrate` as a list of `(version, callable)` pairs:
   ```python
   MIGRATIONS = [
       (1, _migrate_add_session_summary_cols),
       (2, _migrate_dedup_and_unique_indexes),
       (3, _migrate_fts5_backfill),  # Task 1
   ]
   ```
3. Each migration wrapped in `with self.transaction():` + bumps `schema_version`.
4. Current DB is at implicit version 0 → set to version 2 on first run of new code (the UNIQUE-index dedup already ran once).
5. Add test: `test_migration_idempotent` + `test_migration_forward_only` (no downgrade).

**Risk:** MED. One botched migration can brick the production DB. Back up `kontext.db` before first run (setup.sh already handles this — verify). Test on a copy first.

**Files:** `db.py`, `tests/test_db.py`.

---

## Task 3 — Wire `db.semantic_search` into MCP

**Why:** Full per-entry embedding infrastructure is built, tested, cached (after this session's fixes), and unreachable. `kontext_search` only matches MEMORY.md file descriptions — not individual facts. "What did the user say about pricing?" currently returns files, not entries.

**Scope:** ~40 lines.

**Steps:**
1. Add `semantic: bool` param to `kontext_query` tool schema in `mcp_server.py`:
   ```python
   "semantic": {"type": "boolean", "description": "Use embedding-based semantic search on entry facts (requires sentence-transformers)", "default": False},
   "top_k": {"type": "integer", "description": "Max results for semantic search", "default": 10},
   ```
2. In the handler, when `semantic=True` and `search_text` is set:
   ```python
   from mcp_server import get_model
   vec = get_model().encode(search_text).tolist()
   qresults = db.semantic_search(vec, limit=top_k, min_grade=min_grade or 0, file=args.get("file"))
   ```
3. Gracefully fall back to `search_entries` if `sentence_transformers` is not installed (catch `ImportError` and append a warning to the response).
4. Add test in `tests/test_mcp_server.py::TestKontextQuery::test_semantic_search_path` — mock `get_model` and `db.semantic_search`.

**Risk:** LOW. Reuses existing code. No schema changes.

**Files:** `mcp_server.py`, `tests/test_mcp_server.py`, `SKILL.md` (document the new flag).

---

## Task 4 — Delete dead `pipeline/brainstorm.py`

**Why:** 368 lines of file-scanning code duplicated by root `brainstorm.py` (DB-centric). Root is what `SKILL.md` actually runs. Pipeline version is still imported by `tests/test_brainstorm.py` though — that's the only reference keeping it alive.

**Scope:** ~50 lines across 3 files.

**Steps:**
1. Grep the entire project (including `intake/`, `.claude/` hooks, and any shell scripts) for `pipeline.brainstorm` / `pipeline/brainstorm` / `from brainstorm import analyze_file` to confirm no external reference.
2. Read `tests/test_brainstorm.py` — decide which assertions (if any) should transfer to a new `tests/test_brainstorm_db.py` that tests the root DB-centric version.
3. Delete `pipeline/brainstorm.py`.
4. Delete or rewrite `tests/test_brainstorm.py` against the root module.
5. Run full test suite + manually invoke `/kontext brainstorm` to confirm the skill still works.

**Risk:** MED. Easy to miss a hidden import (especially from a hook or setup script). Do the grep thoroughly.

**Files:** delete `pipeline/brainstorm.py`; rewrite `tests/test_brainstorm.py`.

---

## Task 5 — Test coverage for digest orchestration & MCP error paths

**Why:** The most dangerous untested code paths in the system:
- `digest.process_digests` / `digest.auto_import` — raw user messages go straight to the DB with only pattern-match gating. Zero integration tests.
- `mcp_server.kontext_write` DB-failure branch — if `db.add_entry` raises (disk full, locked), the outer handler catches it but there's no test that the MCP response is well-formed.
- `install_hooks.py` partial-install state (UserPromptSubmit installed, PostCompact missing) — the guards exist, the test doesn't.

**Scope:** ~120 lines of tests, no production code changes.

**Steps:**
1. `tests/test_digest.py`: add `TestProcessDigests` class:
   - `test_auto_mode_imports_high_grade_candidates` (tmp_path DB, fake digest file)
   - `test_auto_mode_clears_pending_flag_on_success`
   - `test_auto_mode_re_run_produces_zero_new_imports` (idempotency)
   - `test_auto_mode_skips_low_grade_candidates`
   - `test_partial_crash_leaves_pending_flag_set` (monkeypatch `db.add_entry` to raise midway)
2. `tests/test_mcp_server.py::TestKontextWrite::test_db_failure_returns_clean_error`:
   ```python
   with patch("mcp_server._get_db") as mock_get_db:
       mock_db = MagicMock()
       mock_db.add_entry.side_effect = RuntimeError("disk full")
       mock_get_db.return_value = mock_db
       resp = handle_request(_make_write_request("x.md", "fact"), memory_dir, [])
       assert resp.get("error") or "failed" in resp["result"]["content"][0]["text"].lower()
   ```
3. `tests/test_install_hooks.py::test_installs_missing_postcompact_only`:
   - Full install → delete just `settings["hooks"]["PostCompact"]` → re-run install → assert both present, no UserPromptSubmit duplication.

**Risk:** LOW. Tests only.

**Files:** `tests/test_digest.py`, `tests/test_mcp_server.py`, `tests/test_install_hooks.py`.

---

## Task 6 — `conftest.py` with fast/slow markers

**Why:** Current suite is 10.5s and always runs in full. Once Task 5 adds the digest integration tests (which touch sentence-transformers if available), CI will slow down. Also useful for local dev loop.

**Scope:** ~20 lines.

**Steps:**
1. Create `tests/conftest.py`:
   ```python
   import pytest

   def pytest_addoption(parser):
       parser.addoption("--fast", action="store_true", default=False,
                        help="Skip tests marked @pytest.mark.slow")

   def pytest_configure(config):
       config.addinivalue_line("markers", "slow: long-running test (skipped with --fast)")

   def pytest_collection_modifyitems(config, items):
       if not config.getoption("--fast"):
           return
       skip = pytest.mark.skip(reason="--fast mode")
       for item in items:
           if "slow" in item.keywords:
               item.add_marker(skip)
   ```
2. Mark known slow tests with `@pytest.mark.slow` (any test that actually imports `sentence_transformers` or runs real dream cycles).
3. CI: add a second job `test-fast` that runs on every push with `--fast`; full suite only on PR + master.

**Risk:** NONE.

**Files:** `tests/conftest.py` (new), `.github/workflows/tests.yml`.

---

## Suggested Order

Start a new session and pick **one** of these. Recommended sequence if you want to batch:

1. **Task 2** (schema versioning) — unblocks Task 1's clean backfill.
2. **Task 1** (FTS5) — biggest perf win on search.
3. **Task 3** (semantic `kontext_query`) — high truth-value feature, short diff.
4. **Task 5** (tests) — plugs the dangerous untested paths.
5. **Task 6** (conftest) — needed before Task 5 adds slow tests.
6. **Task 4** (delete dead code) — lowest urgency; do last or skip.

If you only do one: **Task 1 (FTS5)**. If you only do two: add **Task 3**.

---

## How to start a new session on this

Open a fresh Claude Code session in `C:\Users\Gaming PC\Desktop\Claude\Kontext` and say:

> Read `docs/smac-reports/_followup-plan.md` and start Task N. Use TDD: write the failing test first.

The plan has enough detail that the new session shouldn't need to re-read the SMAC report unless it wants the original evidence for a finding.
