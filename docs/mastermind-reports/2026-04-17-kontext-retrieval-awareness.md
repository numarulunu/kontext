# Mastermind Report: Kontext — Memory Retrieval & Awareness Efficiency

**Generated:** 2026-04-17 | **Council:** 7 (3 fixed + 4 dynamic) | **Challengers:** 7 | **Spec agents:** 3

## The Idea
"Does pulling from memory work efficiently? Is Claude aware of everything it should be when working with me? Make retrieval as smooth, optimized, and efficient as possible. Be creative, find solutions, think outside the box."

---

## Ranked Approaches

| # | Approach | Score | Supported By | Overall Verdict |
|---|----------|-------|--------------|-----------------|
| 1 | **Retrieval Engine Upgrade** | **1.323** | Technical Architect + Retrieval Systems Engineer | VIABLE |
| 2 | Instrument First, Optimize Second | 1.30 | Critical Challenger + Observability Analyst | VIABLE |
| 3 | Prompt-Layer Redesign | 1.224 | Product Strategist + Prompt Engineering + Context Window Economist | RISKY |

**Tiebreak:** Top two within 1.77% (under 5% threshold). Approach 1 wins by score but its value collapses if Claude ignores what retrieval returns (attention-decay — strong finding from Approach 2's challenger). The Phase 4 spec therefore **folds in the cheap instrumentation pieces from Approach 2** (`files_loaded` + `kontext_query_calls` logging, `query_log` table), because the Retrieval Engineer's own eval-set and regression-testing points already require them. Net: ship the upgrade **with** telemetry, not sequentially.

Approach 3 had three points killed by challengers — notably Session Intent Declaration (friction against revealed workflow preference) and the unverified "hook-output = user-message-slot" claim. The remaining strong pieces (frozen `MEMORY_INDEX.md`, post-compact re-anchor, hardwire blind-spots routing) are absorbed into the winning scope as low-cost additions.

---

## Winning Approach: Retrieval Engine Upgrade

### Summary
Keep Kontext's architecture (SQLite FTS5 + optional `sentence-transformers`, CLAUDE.md routing table, MCP tools). Fix the *dead* parts and add the measurement that makes future changes provable. This is not a rewrite — it is unblocking retrieval signals that already exist but are currently unused, plus the minimum eval harness to know whether any of it works.

### Why This Won
- **Highest-leverage fix in the entire council deck was rated STRONG by its challenger:** `semantic=True` is opt-in by default in `kontext_query`, meaning `sentence-transformers` (already installed) is effectively dead in production. One-character default flip produces a real signal lift.
- **File-level rerank by entry-grade aggregate** beats description-cosine by using actual per-query evidence rather than a 105-word `description` field.
- **An eval set is non-negotiable** — without it, every other claim is opinion. 30 labeled queries with recall@3 + MRR creates an objective gate.
- **Scope discipline:** cross-encoder rerank, graph traversal, turn-1 extraction replacing the routing table, and session-intent UI all got killed by challengers as overkill or wrong-premise for a 24-file / 1023-entry single-user corpus.

---

## Product Spec

### MVP (v1) — one release

1. **Flip `semantic=True` as default** — `mcp_server.py` tool schema + handler default. `sentence-transformers` is already installed; `store_embedding` already fires on write. Routes every production query through vector similarity instead of keyword match.
2. **File-level rerank by entry signal** — after search, group results by `file_path`, score each file as sum of top-3 entry grades in the result set. Replaces description-cosine.
3. **Query-log table** — new migration: `query_log(id, query_text, tool, results_json, session_id, ts)`. Ground-truth source for eval + regression.
4. **Dedup pass** — one-time hash-on-normalized-text across 1023 entries. Deduplicate before re-indexing. Lighter repeat on every `kontext_write`.
5. **Deterministic HyDE-style expansion** — static dict (~40 trigger phrases → `{intent, domain}`). `"I'm stuck"` → `{procrastination, avoidance, blind_spots, project_goals}`. No LLM call, no network.
6. **Token-budget cap** — replace 6-file cap at session start with 4k-token accumulation ranked by score.
7. **Session telemetry** — append `files_loaded[]` + `kontext_query_calls[]` to existing `events` table at SessionStart/End. Unlocks eval.
8. **30-query eval set + baseline run** — `eval_retrieval.yaml` + `python -m eval_retrieval` → CSV. Baseline captured before any change ships. Gate for all future changes.
9. **Frozen `MEMORY_INDEX.md` + post-compact re-anchor** — strip dynamic content (counts, dates); freeze to file list + one-line description. Stops prompt-cache thrash on memory additions.
10. **Routing table patch** — add `blind_spots, launch, stuck, procrastination` → `user_blind_spots.md` in CLAUDE.md.

### V2 (defer)
- Adaptive expansion map (expand static dict from 4+ weeks of query-log patterns)
- Score-decay weighting (deprioritize files with 0 hits across 30+ queries)
- Cross-file relationship surfacing via existing `graph.py` (wire to ranking)
- Cross-encoder reranker — only if eval shows recall@3 < 0.65 after the v1 phases

### User Stories
1. As Ionut, when I say "I'm stuck on the Vocality launch," I want Claude to surface `user_blind_spots.md` + `project_goals.md` — not just the file matching the first literal keyword.
2. As Ionut, when I ask about PFA invoices, I want top-3 retrieved entries to come from `user_financial_architecture.md` ranked by per-query evidence, not file-description cosine.
3. As Ionut, when a session starts narrowly, I want the 4k token budget used on high-signal chunks — not padded with weakly-relevant files to hit a count.
4. As Ionut, after shipping any retrieval change, I want one script run to tell me whether recall@3 improved — not months of subjective observation.
5. As Ionut, when I say "Luiza tension," I want `user_luiza_dynamic.md` retrieved even when the query contains no literal filename tokens.
6. As Ionut, I want every `kontext_query` logged so that 4 weeks in I can audit which topics never surface and fix them with evidence.

### Success Metrics
| Metric | Baseline | Target |
|--------|----------|--------|
| Recall@3 on 30-query eval set | TBD at baseline run | +15pp vs baseline |
| MRR on 30-query eval set | TBD | +0.10 vs baseline |
| Queries per session hitting semantic path | 0% (default=False) | 100% |
| Duplicate entries in DB | unknown (of 1023) | 0 after dedup |
| Session start token load (avg) | ~1,800 (6 × ~300-word files) | ≤4,000 at higher information density |

### Competitive Edge
- **vs Prompt-Layer Redesign:** doesn't touch CLAUDE.md structure — no prompt-cache thrash on routing edits.
- **vs Instrument-First-only:** instrumentation alone produces data with no retrieval improvement. This ships measurable recall gains in v1, not v6.
- **vs doing nothing:** the `sentence-transformers` pipeline is installed but has never fired in production. The gap between "built" and "on" is a single default value.

---

## Technical Spec

### Architecture

**Query flow (new layers in bold):**

```
[Claude calls kontext_query / kontext_search]
        ↓
[query_expand(query)] → {literal, intent, domain_label}    ← NEW retrieval/query_expand.py
        ↓
[FTS5.search(literal) + db.semantic_search(intent_vec)]    ← existing db.py
        ↓
[rrf_merge(fts_results, sem_results, k=60)]                ← NEW retrieval/rrf.py (~15 LOC)
        ↓
[file_rerank(merged)  → group by file, score = sum(top-3 grades)] ← replaces search() in mcp_server.py
        ↓
[token_budget_gate(files, budget=4096)]                    ← replaces 6-file cap in hooks/session_summary.py
        ↓
[query_log_write(query, results, session_id)]              ← NEW db.py method
        ↓
[return to Claude]
```

**Telemetry path:** `hooks/session_summary.py` → `db.save_event()`. `files_loaded[]` and `kontext_query_calls[]` appended to existing `events` table JSON column at SessionStart/End. No new table required (Migration 16 only if column missing).

### Stack

| Component | Decision | Why |
|---|---|---|
| RRF merge | inline ~15 LOC | `rrf_score(rank) = 1/(60+rank)`, dep not justified |
| HyDE expansion | pure dict lookup | deterministic, ~0ms, no LLM / network |
| Dedup hash | stdlib `hashlib.sha256(unicodedata.normalize("NFC", text.strip().lower()).encode())` | no dep |
| Eval harness | custom CLI, not pytest | pytest doesn't own the CSV reporter cleanly |
| Embedding model | `all-MiniLM-L6-v2` (already used) | no change |
| FTS5 | SQLite built-in (already used) | no change |
| Token counting | `len(text) // 4` approximation | no tiktoken dep |
| Cross-encoder | **NOT ADDED** in v1 | only if eval shows recall@3 < 0.65 |

### Build vs Buy
Nothing new to buy. Every new component is <150 LOC of pure Python.

### Implementation Phases

| Phase | What | Complexity | Dependencies |
|-------|------|-----------|--------------|
| 1 | Dedup + `query_log` schema | LIGHT | none |
| 2 | Flip `semantic=True` default + RRF merge | LIGHT | Phase 1 (log the merged results) |
| 3 | Query expansion dict | LIGHT | Phase 2 (expansion feeds dual-path RRF) |
| 4 | File-level rerank (`search()` rewrite) | MODERATE | Phase 2 (needs semantic on entries) |
| 5 | Token-budget cap + session telemetry | MODERATE | Phase 4 (new file list) |
| 6 | Eval harness + 30-query set | MODERATE | none (runs vs any phase) |
| 7 | `MEMORY_INDEX.md` freeze + routing patch | LIGHT | none |

### Function signatures that change
```python
# mcp_server.py — schema default False → True, handler default True
kontext_query(search, semantic=True, top_k=10, file, tier, min_grade)

# mcp_server.py — full rewrite
def search(query: str, entries: list[dict], top_k: int = 5) -> list[dict]: ...
    # now runs db.semantic_search + group-by-file grade aggregation

# db.py — new
def query_log_write(self, query: str, tool_name: str,
                    results_json: str, session_id: str = "") -> int: ...

# retrieval/rrf.py — new
def rrf_merge(fts_results: list[dict], sem_results: list[dict],
              k: int = 60) -> list[dict]: ...

# retrieval/query_expand.py — new
def expand(query: str) -> dict:   # keys: literal, intent, domain_label
```

### New files
```
retrieval/
  __init__.py
  rrf.py
  query_expand.py
scripts/
  dedup_entries.py
eval_retrieval.py
eval_retrieval.yaml
```

### Schema migrations
Migration 15 — additive only (no existing table touched):
```sql
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY,
    query_text TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    results_json TEXT,
    session_id TEXT DEFAULT '',
    ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
```
Migration 16 — only if `events` lacks `files_loaded`: `ALTER TABLE events ADD COLUMN files_loaded TEXT DEFAULT ''`. Version-gated, idempotent.

`CLAUDE.md` is not touched by migrations. `MEMORY_INDEX.md` shape is frozen after Phase 7.

---

## Risk Analysis

### Risk Register

| Risk | L | I | Mitigation |
|---|---|---|---|
| `semantic=True` silently returns empty on entries with stale/missing embeddings | MED | HIGH | Dry-run 10 representative entries pre-flip; check embedding coverage rate; reindex gap |
| Eval overfit — 30 queries on 24 files, pass 3 ranker is memorized | HIGH | MED | Reserve 8 queries as held-out, never touch during tuning; train/held-out divergence = kill signal |
| Retrieval improves, Claude still ignores — long-context attention decay | HIGH | HIGH | Pre-flight: force-load file, ask dependent Q, verify citation; if already failing here, retrieval is not the bottleneck |
| Dedup merges entries referenced elsewhere (query_log FK, joins) | LOW | HIGH | Audit FK deps first; first pass is "mark superseded," never hard-delete |
| MEMORY_INDEX "frozen" is convention, not lock | HIGH | MED | Accept it; optional git pre-commit hook rejecting edits without prefix |
| CLAUDE.md routing edit → full prompt-cache invalidation | MED | MED | Batch all routing changes into single edit at session end |
| HyDE false positives — "stuck" expands into unrelated domains | MED | MED | Constrain to top-1 expansion; discard if retrieved file's domain doesn't match |
| `sentence-transformers` cold-start 300–800ms on first call | MED | MED | Lazy-load model; cache in process memory; measure p95 before flip |

### Kill Conditions
1. **Pre-flight shows retrieval is not the failure mode.** Claude retrieves the right file and ignores its content anyway. Retrieval work = zero lift; stop before a line is written.
2. **`semantic=True` p95 latency > 400ms in the target environment.** Recall gain doesn't justify UX regression; 6-file heuristic is faster and good enough.
3. **After 10 tuning passes, held-out recall@3 delta < 0.05 over BM25 baseline.** Corpus is too small and too curated for embeddings to add signal the keyword index doesn't already provide.

### Biggest Unknown
**Does retrieval quality actually explain observed misses, or does Claude retrieve correctly and fail downstream?**

**Pre-flight (< 1 hour, zero code changes):**
1. Pick 5 queries from the planned eval set.
2. Run `kontext_query` manually with current settings; log returned files.
3. Manually load those files into a fresh session; ask the same question cold.
4. If Claude answers correctly → retrieval is the problem. Proceed with the full plan.
5. If Claude ignores the loaded file or hallucinates anyway → consumption is the problem. Stop; redirect to prompt-layer fix.

### Scope Creep Traps
- **Cross-encoder reranking.** Seductive as the "correct" ML answer. RRF noise floor is higher than any cross-encoder gain on a 24-file corpus. Adds a second model dep + ~200ms latency for no measurable delta. Gate behind eval shortfall, don't build speculatively.
- **Graph traversal / entity linking.** Looks like relational enrichment. Requires a schema that doesn't exist and maintenance burden that kills the lightweight design. File-rerank covers ~90% of the same intent at 5% of the work.
- **Session Intent Declaration UI.** "Explicit signal" feels clean but adds friction Ionut won't maintain past day 3. Revealed behavior always defeats declared intent. Routing hardwire covers the known cases without user input.
- **Query-log dashboard.** Logging is necessary. A UI on top is premature until the log has 30+ days of signal. Read it with SQL; build visualization only if SQL becomes unmanageable.

---

## Challenger Highlights

The most valuable challenges that shaped the final spec:

- **Retrieval Systems Engineer challenger:** flagged that `semantic=True` being opt-in is the *real* production kill-switch — should be point #1, not #6. The final plan makes it Phase 2 (after the query-log that proves it worked).
- **Product Strategist challenger:** killed Session Intent Declaration on revealed-preference grounds. Saved the spec from shipping a feature Ionut would abandon inside a week.
- **Critical Challenger challenger:** pushed back on the "pause and diagnose for 2 weeks" stance by pointing out cheap obvious fixes exist. Result: the pre-flight is a <1 hour check, not a 2-week project.
- **Technical Architect challenger:** "BM25 rewards term frequency, not semantic similarity" — this is why semantic+RRF stayed in the scope even as other heavyweight IR pieces got cut.
- **Observability Analyst challenger:** "1–2 sessions/day = statistically thin signal for weeks" forced the dashboard out of v1 (reserved for SQL-on-log, not UI).
- **Context Window Economist challenger:** "On-demand fetch assumes Claude correctly gates `kontext_query`" — accepted; this is why the plan keeps *eager* load with a token budget rather than moving to pure on-demand.

## Coverage Gaps

| Role | Status | Impact |
|------|--------|--------|
| All 7 council members | OK | None — full deck ran |
| All 7 challengers | OK | None — full adversarial coverage |
| All 3 spec agents | OK | None |

No degraded-output sections. No UNCHALLENGED points. No `LOW VERIFICATION COVERAGE` warning.

---

## Execution Order (deduplicated)

1. **Pre-flight check** (<1 hour, zero code): verify retrieval is actually the bottleneck before building anything.
2. **Phase 6 first** (eval harness + 30-query set): capture the baseline. No other phase ships without a recall delta from this.
3. **Phase 1** (dedup + query_log schema).
4. **Phase 2** (flip `semantic=True` + RRF) — single highest-ROI change.
5. **Phase 3** (query expansion dict).
6. **Phase 4** (file rerank rewrite).
7. **Phase 5** (token budget + session telemetry).
8. **Phase 7** (MEMORY_INDEX freeze + routing patch) — low-risk, can ship any time.
9. Re-run eval; if recall@3 improvement < 0.05, invoke Kill Condition 3 before writing anything else.
