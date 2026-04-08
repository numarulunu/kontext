# SMAC Report: How best should I optimize this codebase?

**Generated:** 2026-04-07 | **Agents:** 5R + 5V | **Verification coverage:** 100% | **Disputed:** 1 | **Partial:** 3

50 findings across 5 angles (DB, MCP, Pipeline, Consolidation, Ops). Every finding cross-verified by reading the cited file:line. One verifier flipped a finding (Ops-F9). Three were downgraded to PARTIAL where evidence was looser than the researcher claimed.

---

## Top 15 Ranked Findings

| # | Finding | Impact | Effort | Conf | Verified | Score |
|---|---|---|---|---|---|---|
| 1 | `kontext_relate` documented as write tool, implemented as read-only | HIGH | LOW | 100% | CONFIRMED | 3.00 |
| 2 | `semantic_search` full-table BLOB scan + Python dot product per call | HIGH | MED | 97% | CONFIRMED | 2.91 |
| 3 | TOCTOU race in `add_entry`/`add_relation` (no UNIQUE constraint) | HIGH | LOW | 95% | CONFIRMED | 2.85 |
| 4 | Dream phases not atomic — crash mid-phase = partial DB state | HIGH | MED | 95% | CONFIRMED | 2.85 |
| 5 | WhatsApp AM/PM regex captures `PM] Alice` as sender | HIGH | LOW | 95% | CONFIRMED | 2.85 |
| 6 | `detect_conflicts` writes mid-read, no transaction, no dedup | HIGH | LOW | 92% | CONFIRMED | 2.76 |
| 7 | `phase_resolve` uses LIKE with unescaped wildcards | HIGH | LOW | 85% | CONFIRMED | 2.55 |
| 8 | `kontext_write` export not transactional — DB/FS skew on crash | MED | MED | 95% | CONFIRMED | 1.90 |
| 9 | `idx_entries_fact` useless; `LIKE '%x%'` always full scan → use FTS5 | MED | MED | 99% | CONFIRMED | 1.98 |
| 10 | `get_entry` writes `last_accessed` on every read (write amplification) | MED | LOW | 96% | CONFIRMED | 1.92 |
| 11 | `prune_graph` N+1 DELETE pattern (one commit per row) | MED | LOW | 98% | CONFIRMED | 1.96 |
| 12 | No log rotation across 5 writers (`_kontext.log` etc unbounded) | MED | LOW | 100% | CONFIRMED | 2.00 |
| 13 | `sync.py --dry-run` still mutates DB via decay + dream | MED | LOW | 100% | CONFIRMED | 2.00 |
| 14 | `db.semantic_search` exists but no MCP tool wires it up | MED | MED | 100% | CONFIRMED | 2.00 |
| 15 | `"Claude:"` turn label missing from Gemini parser regex | MED | LOW | 100% | CONFIRMED | 2.00 |

Findings 16–50 follow with lower scores. Full details below.

---

## Detail — Top Findings

### 1. `kontext_relate` documented as "Add relation" but is read-only
**Files:** `mcp_server.py:307-317` (schema), `mcp_server.py:547-572` (handler), `SKILL.md:40` (doc)
The MCP schema only has `entity` + `depth`. The handler calls `query_connections` + `describe_entity`. Grep confirms zero `db.add_relation` calls in `mcp_server.py`. SKILL.md tells Claude to use it for writes — so any session following the docs silently does a read.
**Fix:** rename to `kontext_graph_query` and update SKILL.md, OR add an `action: add|query` param wired to `db.add_relation`.

### 2. `semantic_search` full-table BLOB scan per call
**File:** `db.py:512-518`
Every call SELECTs all rows where `embedding IS NOT NULL`, deserializes BLOBs in Python, dot-products in a loop. ~5 MB transfer per query at current scale; gets worse linearly.
**Fix (cheap):** cache deserialized vectors in a numpy dict at startup, invalidate on `store_embedding`. **Fix (right):** `sqlite-vec` extension for native ANN.

### 3. TOCTOU race in `add_entry` / `add_relation`
**Files:** `db.py:145-154`, `db.py:311-319`
Check-then-insert with no enclosing transaction. Two concurrent writers (MCP server + sync.py) can both pass the SELECT and both INSERT. Schema has no `UNIQUE(file, fact)` or `UNIQUE(entity_a, relation, entity_b)`.
**Fix:** add UNIQUE constraints + `INSERT OR IGNORE`. Eliminates race and is faster.

### 4. Dream phases not atomic
**File:** `dream.py:398-403`
Phase loop runs 5 functions sequentially. Each `db.delete/update/resolve` call commits immediately (`db.py:121-124`). Crash in phase 3 = phases 1+2 already written, no rollback, no checkpoint state. `db.transaction()` exists but isn't used.
**Fix:** wrap phase loop in `db.transaction()`, OR record completed-phase state to a file.

### 5. WhatsApp AM/PM regex breaks sender
**File:** `pipeline/parsers.py:276-277`
Time group `(\d{1,2}:\d{2}(?::\d{2})?)` doesn't consume AM/PM. `[4/7/26, 2:30 PM] Alice: hello` parses sender as `PM] Alice`. Verified by runtime test.
**Fix:** `(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)`

### 6. `detect_conflicts` writes mid-read, no dedup on conflicts table
**Files:** `db.py:402-463`, schema line 73
O(n²) loop, one COMMIT per detected conflict, no transaction, no `UNIQUE(file, entry_a, entry_b)`. Re-running inserts duplicate rows.
**Fix:** wrap in transaction; add UNIQUE + INSERT OR IGNORE.

### 7. `phase_resolve` LIKE wildcard risk
**File:** `dream.py:263-266`
`db.search_entries(c["entry_a"][:50])` uses LIKE; if a stored fact contains `%` or `_`, matching breaks.
**Fix:** replace with direct `WHERE fact = ?` exact lookup. Eliminates wildcard risk + the post-filter loop.

### 8. `kontext_write` export not transactional
**File:** `mcp_server.py:500-513`
DB commit happens, then `write_text` and `export_memory_index` can fail leaving filesystem stale. No rollback or compensating reindex.
**Fix:** catch FS errors separately and log; DB is source of truth, but trigger a deferred reindex on FS failure.

### 9. `idx_entries_fact` useless; replace LIKE with FTS5
**Files:** `db.py:105` (index), `db.py:193` (LIKE), called from `dream.py:263-264` in a loop
Leading-`%` LIKE can never use a B-tree index. Index wastes write overhead.
**Fix:** drop the index; create `entries_fts` virtual table (FTS5 trigram tokenizer); rewrite `search_entries` to MATCH.

### 10. `get_entry` writes `last_accessed` on every read
**File:** `db.py:169-172`
Every read = a write commit. MCP query/search loops compound this. Sub-second precision is worthless — `last_accessed` is only consumed by `decay_scores` and `phase_purge`.
**Fix:** dirty-set + flush at close, OR only update when delta > 1 hour.

### 11. `prune_graph` N+1 DELETE
**File:** `graph.py:165-176`
Per-row delete + commit. 500 noisy relations = 500 commits, holds write lock for the loop.
**Fix:** single `DELETE FROM relations WHERE id IN (?, ?, ...)`.

### 12. No log rotation anywhere
**Files:** `mcp_server.py:29`, `sync.py:18`, `digest.py:50`, `dream.py:37`, `brainstorm.py:19`
All use `FileHandler` / `basicConfig`. Zero matches for `RotatingFileHandler`. `_kontext.log` already 45 KB and grows on every MCP call.
**Fix:** swap each to `RotatingFileHandler(maxBytes=1_000_000, backupCount=2)`.

### 13. `sync.py --dry-run` still mutates DB
**File:** `sync.py:141-150`
Dry-run only skips `add_entry`. `_run_decay(db)` and `_maybe_dream(db)` run unconditionally — both write.
**Fix:** gate both behind `if not dry_run`.

### 14. `db.semantic_search` exists but no MCP tool calls it
**Files:** `db.py:499-539`, `mcp_server.py` (zero refs)
Per-entry embedding search infrastructure built and tested but unreachable. `kontext_search` only matches MEMORY.md file descriptions, not entries.
**Fix:** add `semantic=true` flag to `kontext_query`; encode and call `db.semantic_search`.

### 15. `"Claude:"` turn label missing from Gemini parser
**File:** `pipeline/parsers.py:198-201`
`_GEMINI_TURN_RE` alternation: `You|User|Human|Gemini|Model|Assistant|AI` — no `Claude|Opus|Sonnet|Haiku`. Claude.ai/Claude Code transcripts fall through to plain-text and lose role separation.
**Fix:** add `Claude|Claude Code|Opus|Sonnet|Haiku` to alternation.

---

## Findings 16–50 (compact)

### MCP / API
- **16. `kontext_search` silent query truncation** — `mcp_server.py:437-438` truncates queries >500 chars with no caller warning. Append warning string. (LOW/LOW/100%)
- **17. `kontext_dream` calls `find_memory_dir()` redundantly** — `mcp_server.py:608-611` ignores `memory_dir` already in scope. Use the param. (LOW/LOW/100%)
- **18. `kontext_query` ignores `file`/`tier` filters when `search` is set** — `mcp_server.py:524-533`. Either extend `db.search_entries` to accept filters or document the mutual exclusion. (LOW/LOW/100%)
- **19. Uncapped numeric args** — `decay_amount`, `days_threshold`, `hours`, `depth` in `mcp_server.py:650-651, 577, 555` (`depth` actually at 556). Add `min/max` clamps mirroring `top_k` at line 433. (LOW/LOW/100%)
- **20. Embed failure returns success** — `mcp_server.py:503-516`. Append `(NOTE: embedding failed — run kontext_reindex)` to return string. (LOW/LOW/100%)
- **21. `kontext_conflicts` resolve action not logged** — `mcp_server.py:727-733`. One-line `_logger.info(...)`. (LOW/LOW/100%)

### DB
- **22. No schema_version table** — `db.py:112-119`. PRAGMA table_info on every startup; ALTER not in transaction. Add `schema_version` row + gated migrations. (MED/LOW/98%)
- **23. `export_all` 3 queries × N files** — `export.py:62-64,98-100`. One bulk query partitioned in Python. (MED/LOW/97%)
- **24. `phase_purge`/`phase_normalize` unfiltered fetch** — `dream.py:218,361`. Push grade/age filter into SQL. (LOW/LOW/99%)
- **25. No `PRAGMA synchronous=NORMAL`** — `db.py:44`. Add `synchronous=NORMAL` and `cache_size=-16000`. (LOW/LOW/85%)

### Pipeline
- **26. `pipeline/brainstorm.py` is dead code** — root `brainstorm.py` is what runs (SKILL.md:176). Delete. (MED/LOW/100%)
- **27. Romanian `o să`/`vreau să` over-broad** *(PARTIAL)* — `grading.py:44-45`. Verifier note: patterns require diacritics so the no-diacritic example actually fires on `\bANAF\b`. With diacritics it does over-score. Tighten or downgrade to PREFERENCE. (MED/LOW/70%)
- **28. `\bprefer\b` Romanian row matches English** — `grading.py:95`. Restrict or delete. (LOW/LOW/100%)
- **29. `_split_oversized_conversation` re-renders messages** — `pipeline/chunker.py:88-113`. Cache rendered sizes. (LOW/LOW/100%)
- **30. Chunk overlap silently dropped when last convo > 8000 chars** — `pipeline/chunker.py:186-193`. Log a WARN and/or include text-tail overlap. (LOW/LOW/100%)
- **31. `find_memory_dir` picks by file count** — `pipeline/brainstorm.py:34-49`. Moot if F26 is actioned. (LOW/LOW/100%)
- **32. `file_hash` called twice per file** — `pipeline/extract.py:115,242`. Cache once in `find_new_files`. (LOW/LOW/100%)
- **33. No Romanian grading test fixtures** — `tests/test_intake.py:244-316` English-only. Add 3 RO assertions. (LOW/LOW/100%)

### Consolidation
- **34. digest.py double-processes files** *(PARTIAL)* — `digest.py:416-417`. DB dedup mitigates the silent-duplicate risk; partial-import after crash is still a hole. Move processed files to `_processed/`. (MED/LOW/85%)
- **35. `auto_import` stores raw user messages** *(PARTIAL)* — `digest.py:352-386`. Confirmed for `--auto` only. Add LLM distillation step OR remove `--auto`. (MED/MED/95%)
- **36. `phase_dedup` redundant guard** — `dream.py:162-189`. Remove `if e1["id"] in merged_ids` (can't fire); inner e2 guard is fine. (LOW/LOW/100%)
- **37. brainstorm.py root vs pipeline functional overlap** — see F26. (LOW/LOW/100%)
- **38. Text dedup uses 80-char prefix exact-match** — `digest.py:432-438`. Apply SequenceMatcher inter-candidate at 0.85. (MED/LOW/100%)
- **39. `days_since` returns 999 on null `last_accessed`** — `dream.py:53-62, 364`. Null + grade≤1 → purge. Return 0 on null instead, backfill nulls. (MED/LOW/100%)
- **40. No tests for `process_digests`/`auto_import`** — `tests/test_digest.py`. Add integration tests with tmp_path DB. (MED/MED/100%)
- **41. `phase_compress` strips parentheticals ≥20 chars blindly** — `dream.py:331,340`. Tighten regex to known boilerplate or raise threshold. (LOW/LOW/100%)

### Ops / Tests / CI
- **42. `install_hooks.py` partial-install state untested** — `tests/test_install_hooks.py`. Add asymmetric fixture test. (MED/LOW/100%)
- **43. `test_dry_run_doesnt_modify_db` is vacuous** — `tests/test_sync.py:99-106` doesn't pass `db=db`, so the assertion runs against an unrelated empty fixture. Pass `db=db`. (MED/LOW/100%)
- **44. `setup.sh` `$PYTHON` unbound at line 150** — sentence-transformers probe runs outside the guard at 141-146. Wrap in `if [ -n "$PYTHON" ]`. (MED/LOW/100%)
- **45. `setup.sh` no `py` launcher in probe list** — `setup.sh:134`. Add `py` between `python` and the hardcoded paths. (LOW/TRIVIAL/100%)
- **46. CI matrix is 3.11/3.12 but production runs 3.14** — `.github/workflows/tests.yml:14`. Add 3.13. (MED/TRIVIAL/100%)
- **47. No `conftest.py` / no slow markers** — add fast/slow split. (LOW/LOW/95%)
- **48. `kontext_write` DB-failure path untested** — `tests/test_mcp_server.py:116-166`. Add patched-raise test. (MED/LOW/100%)

### Disputed
- **49. ~~`_maybe_dream` writes stamp before exports succeed~~** *(DISPUTED)* — verifier read `sync.py:42-58` and found stamp write at line 56 is *inside* the same try block as the exports. Export failure causes the except to swallow and skip the stamp write. The real (opposite) bug: if `export_all` succeeds but `export_memory_index` raises, stamp is not written and dream re-runs unnecessarily next session. Move stamp write to *after* both exports inside an inner try block, OR treat stamp write as the success signal it already is.

---

## Disputed Findings

| Finding | Researcher | Verifier | Reason |
|---|---|---|---|
| F49 (Ops F9) — _dream_stamp written before exports | Ops Researcher | Ops Verifier | Stamp write is inside the same try block as exports; export failure already skips stamp. The real bug is the inverse: partial export success leaves dream un-stamped. Recommendation still valid (move stamp logic), but for the opposite reason. |

## Coverage Gaps

| Role | Status | Notes |
|---|---|---|
| All 5 researchers | OK | All returned within window |
| All 5 verifiers | OK | 100% verification coverage |

---

## The Five Highest-ROI Moves

1. **Wire `kontext_relate` to actually write, OR rename it** (F1). Currently lying to Claude — every session that follows SKILL.md silently does the wrong thing.
2. **Add UNIQUE constraints + `INSERT OR IGNORE` on `entries`, `relations`, `conflicts`** (F3, F6). Eliminates 3 race conditions in one schema migration.
3. **Wrap dream phase loop in `db.transaction()`** (F4). Single context manager. Eliminates partial-state risk for the most destructive code path in the system.
4. **Cache deserialized embeddings in a numpy dict + invalidate on write** (F2). Removes per-query BLOB scan. ~20 lines.
5. **Drop `idx_entries_fact`, add FTS5 virtual table** (F9). Makes search actually indexed AND removes wasted insert overhead.

After those five: log rotation (F12), `kontext_write` embed-failure surfacing (F20), and `sync.py --dry-run` correctness (F13) are each one-line fixes.
