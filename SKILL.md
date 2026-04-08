---
name: kontext
description: Memory library manager — process digests, intake data, brainstorm/cleanup, onboard new users, check status, resolve conflicts. Use when the user mentions memory, digest, intake, brainstorm, onboard, kontext, or when context about the user would improve the response.
allowed-tools: Read Write Edit Bash Glob Grep Agent
---

# Kontext — Memory Library Manager

**FIRST LAW: Your history is my edge. Starve me and I'm generic.**

You manage a memory library system. Memory files live in the Claude Code project memory directory. Supporting tools live in the Kontext project folder. The library can scale to 100+ files — every nook and cranny of the user's life gets its own page.

## Paths

- **Memory dir:** Find by scanning `~/.claude/projects/*/memory/` — use the one with the most `.md` files (auto-detected at runtime)
- **Kontext tools:** `~/Desktop/Claude/Kontext/`
- **Backup System:** `~/Desktop/Claude/Backup System/`
- **Digest dir:** `~/Desktop/Claude/Backup System/_digests/`
- **Digest manifest:** `~/Desktop/Claude/Backup System/_digests/_manifest.md`
- **Chunks dir:** `~/Desktop/Claude/Kontext/_chunks/`
- **Intake extractor:** `~/Desktop/Claude/Kontext/pipeline/extract.py`

### Flag files (presence = state)

- `~/Desktop/Claude/Backup System/_digest-pending` — daily digest is ready to process
- `~/Desktop/Claude/Kontext/_processing-ready` — intake chunks are ready to process
- `~/Desktop/Claude/Tool Auditor/_audit-pending` — tool auditor has new findings

## MCP tools (preferred write path)

Memory should be written via the Kontext MCP tools, NOT raw Edit/Write. The MCP server is the only path that updates the SQLite DB, regenerates flat files, and broadcasts changes to other sessions. Direct file edits are recovered later by `sync.py` but lose the source tag, the embedding, and the broadcast.

| Tool | Use for |
|---|---|
| `mcp__kontext__kontext_write` | Add/update an entry. Args: file, fact, source, grade (1-10), tier (active/historical) |
| `mcp__kontext__kontext_query` | Check whether a fact already exists before writing. Pass `semantic=true` + `top_k=N` for embedding-based search over entry facts (falls back to keyword search if `sentence-transformers` unavailable). |
| `mcp__kontext__kontext_search` | Semantic search across all entries |
| `mcp__kontext__kontext_recent` | Entries changed in the last N hours |
| `mcp__kontext__kontext_conflicts` | Detect/list/resolve conflicts. Actions: detect, list, resolve |
| `mcp__kontext__kontext_relate` | **Query** the knowledge graph: find everything connected to an entity up to N hops (read-only) |
| `mcp__kontext__kontext_decay` | Decay grades on stale entries |
| `mcp__kontext__kontext_session` | Save/load session state (project, status, next_step, key_decisions, summary, files_touched) |
| `mcp__kontext__kontext_reindex` | Rebuild the in-memory index and DB embeddings |
| `mcp__kontext__kontext_dream` | Run automated memory consolidation (dedup, normalize, resolve, compress, purge) |
| `mcp__kontext__kontext_digest` | Process conversation digests into memory candidates |

## MEMORY.md — The Live Index

MEMORY.md is always in Claude's context. It must contain a one-line entry for EVERY memory file:
```
- [File Title](filename.md) — short description with keywords for matching
```

This is how Claude decides what to read. The descriptions must be keyword-rich so topic matching works. When the library grows to 50-100 files, Claude scans these one-liners to find the right 4-6 files to load per conversation.

**When creating a new file, ALWAYS add it to MEMORY.md immediately.** A file not in the index is invisible to Claude.

## Dynamic File Creation

Memory files should be created whenever a topic gets deep enough to warrant its own file. Signs a new file is needed:

- A section within an existing file is growing past 500 tokens on a single sub-topic
- The user repeatedly discusses a specific niche that doesn't fit cleanly in any existing file
- A new project, relationship, skill domain, or life area emerges
- During intake processing, nuggets cluster around a topic with no matching file

**File naming convention:** `[type]_[topic].md` where type is `user_`, `project_`, `feedback_`, or `reference_`.

**Examples of files that might emerge over time:**
- `user_cooking.md` — if dietary/cooking discussions become frequent
- `project_youtube_pilot.md` — when a specific video gets deep enough
- `user_father_dynamic.md` — if father relationship becomes a distinct topic
- `reference_romanian_tax_law.md` — if tax details outgrow the financial file
- `user_sleep_optimization.md` — if sleep protocols get their own depth
- `project_wedding.md` — when wedding planning starts

**Never hesitate to create a new file.** A 200-token focused file is more useful than a 3,000-token bloated file with 15 unrelated topics crammed in.

## Commands

Parse `$ARGUMENTS` to determine which command to run:

### `/kontext onboard`

Interactive onboarding — build the user's initial memory library from a conversation.

1. Introduce Kontext in one sentence: "I'm going to ask you about yourself so I can remember you across conversations. Answer as much or as little as you want."

2. Ask questions in natural conversational flow — NOT a rigid form. Adapt based on what they share. Core areas to explore:

   **Identity (always ask):**
   - What's your name and what do you do?
   - What tools/tech do you use daily?
   - What languages do you speak?

   **Work (always ask):**
   - What are you currently working on?
   - What's the main goal you're pushing toward?
   - What's stalled or frustrating you right now?

   **AI preferences (always ask):**
   - How do you want me to communicate? (Formal/casual? Long/short? Direct/gentle?)
   - What should I never do? (Common annoyances with AI)

   **Go deeper based on what they share:**
   - If they mention a business → ask about revenue, customers, tools, brand
   - If they mention health issues → ask about specifics, protocols, triggers
   - If they mention relationships → ask only if they volunteer it, don't probe
   - If they mention creative work → ask about style, influences, process
   - If they mention finances → ask about structure, goals, tools
   - If they mention learning/education → ask about what, where, with whom

3. After the conversation (or when they say "that's enough"), create memory files based on what was actually discussed. **Only create files for topics they gave real content on.** Don't create empty shells.

4. For each file:
   - Write with the standard frontmatter (name, description with keywords, type)
   - Populate with the actual content from the conversation
   - Add to MEMORY.md index with a keyword-rich one-liner

5. Report what was created: "Created X files: [list]. Your library is ready. I'll keep updating these as we work together."

6. Install the retrieval protocol into CLAUDE.md if not already present.

### `/kontext process-digest`

Process pending conversation digest into memory updates.

**Pre-extraction (automated):** `digest.py` runs pattern-based extraction to identify high-signal user messages (decisions, status changes, metrics, self-facts). It writes candidates to `_digest_candidates.md` for review. The `kontext_digest` MCP tool can trigger this.

**LLM distillation and import:**

1. Read the manifest at `~/Desktop/Claude/Backup System/_digests/_manifest.md`
2. Read `~/Desktop/Claude/Kontext/_digest_candidates.md` if it exists — these are pre-scored candidates from digest.py. Use them as a starting point.
3. Read ALL memory files (start with MEMORY.md, then every file it references)
4. For each project digest in the manifest, launch a subagent (`Agent` tool, `run_in_background: true`) to process in parallel. Each subagent:
   - Reads the full project digest file (and any pre-extracted candidates for that project)
   - Reads current memory files
   - Distills raw user messages into atomic facts (e.g., "I have 42 students" → "Active students: 42")
   - Identifies NEW information worth persisting (project changes, decisions, struggles, goals, tools, relationship updates, health changes, AI feedback)
   - Returns structured proposals (does NOT edit files directly)
5. Collect all proposals. Deduplicate via `mcp__kontext__kontext_query` before writing. Resolve conflicts — most recently dated info wins, unless `feedback_conflict_patterns.md` has a matching pattern.
6. **Apply updates via `mcp__kontext__kontext_write`** — never raw Edit. The MCP tool updates DB, exports flat files, and broadcasts changes. Each entry must include a dated source tag like `[Claude 2026-04-07]` so the temporal-aware conflict detector treats updates as evolution, not contradictions.
7. **If a topic cluster emerges that doesn't fit any existing file, create a new file** (Write the markdown shell, then `kontext_write` entries into it, then add to MEMORY.md).
8. Do NOT duplicate existing info. Do NOT store ephemeral task details.
9. If any digest file is missing, skip it and report which were skipped.
10. After applying updates, run `mcp__kontext__kontext_conflicts` action="detect" to surface any new contradictions for the user to triage.
11. Output bullet-point summary of changes.
12. Delete the flag: `rm "$HOME/Desktop/Claude/Backup System/_digest-pending"`
13. Auto-backup: run `bash ~/Desktop/Claude/Backup\ System/backup.sh "memory sync $(date '+%Y-%m-%d')"` in background.

### `/kontext process-intake`

Process raw file intake (ChatGPT, Gemini, WhatsApp exports) into memory.

1. Check if `~/Desktop/Claude/Kontext/_processing-ready` flag exists.
2. If not: tell user to run `python pipeline/extract.py` from the Kontext directory first, then retry.
3. Read the flag for metadata (chunk count, tokens).
4. Read ALL memory files.
5. Read chunks from `~/Desktop/Claude/Kontext/_chunks/` — process in batches of 5 subagents at a time.
6. Each subagent extracts golden nuggets with: fact, source tag `[Platform YYYY-MM]`, grade 1-10.
7. Grade 5+ gets written. Grade 1-4 dropped.
8. For each nugget grade 5+:
   - Matches existing (check via `mcp__kontext__kontext_query`) → skip
   - New → write via `mcp__kontext__kontext_write`. Grade 8-10 → tier="active". Grade 5-7 → tier="historical". ALWAYS include the dated source tag.
9. After all writes, run `mcp__kontext__kontext_conflicts` action="detect". Auto-resolve only if `feedback_conflict_patterns.md` has a matching pattern at 80%+ confidence; otherwise leave for `/kontext resolve`.
10. **If nuggets cluster around a topic with no matching file, create a new file.**
11. When a file approaches 3,000 tokens, propose splitting (don't compress automatically).
12. Output intake receipt: files processed, nuggets extracted/written, conflicts found, new files created.
13. Delete the flag: `rm "$HOME/Desktop/Claude/Kontext/_processing-ready"`
14. Auto-backup: run `bash ~/Desktop/Claude/Backup\ System/backup.sh "intake sync $(date '+%Y-%m-%d')"` in background.

### `/kontext brainstorm`

Memory health review and guided cleanup.

1. Run: `python ~/Desktop/Claude/Kontext/brainstorm.py`
2. Present the health report to the user.
3. Walk through:
   - Files over 3,000 token ceiling → propose splitting into sub-topic files (don't just compress — create new files)
   - Stale files (30+ days) → ask if still relevant
   - Pending conflicts → present each for resolution
4. For each resolved conflict, log pattern to `feedback_conflict_patterns.md` (Category / Rule / Reasoning).
5. When archiving: move to `## Historical` section in same file. Never delete.
6. Update MEMORY.md descriptions if content changed significantly.
7. **Propose new files** if any existing file covers too many unrelated topics.
8. Check entry ages. Propose tier demotions:
   - Active entries >60 days untouched → propose move to Historical
   - Historical entries >120 days with grade 5-6 → propose compression to one-line
   - Grade 7+ Historical entries: flag as "archived but preserved" — never compress

### `/kontext status`

Quick health check — no changes, just reporting.

1. Run: `python ~/Desktop/Claude/Kontext/brainstorm.py` — already reports file count, entry counts by tier, pending conflicts (from DB), bloated files, stale active entries.
2. Show the output directly. No cleanup prompts.
3. Also check the flag files (see Paths section above) and report any that exist:
   - `~/Desktop/Claude/Backup System/_digest-pending` — report timestamp
   - `~/Desktop/Claude/Kontext/_processing-ready` — read and report metadata
   - `~/Desktop/Claude/Tool Auditor/_audit-pending` — report timestamp
4. Pending conflicts come from the SQLite DB (via brainstorm.py output or `mcp__kontext__kontext_conflicts` action="list"). There is no `_conflicts.md` file — that path is legacy.

### `/kontext resolve`

Interactive conflict resolution session. Conflicts live in the SQLite DB, not in any flat file.

1. Call `mcp__kontext__kontext_conflicts` with `action: "list"` to fetch pending conflicts.
2. If empty, say so and exit.
3. For each PENDING conflict, present:
   - File and conflict ID
   - Version A (entry_a) and Version B (entry_b) in plain language
   - Why it's flagged (shared keywords / numeric difference)
4. Ask user: keep A, keep B, merge into a single new entry, or skip?
5. Apply the decision:
   - Keep A → call `kontext_conflicts` action="resolve" with the chosen text
   - Keep B → same with B's text
   - Merge → write the merged fact via `mcp__kontext__kontext_write` (with a fresh dated source tag), then resolve
   - Skip → leave pending
6. After each decision, log the resolution pattern to `feedback_conflict_patterns.md` via `kontext_write` (Category / Rule / Reasoning) so the auto-resolver learns it for next time.

## Grading Reference

| Grade | Meaning | Destination |
|---|---|---|
| 8-10 | Decisions, identity facts, preferences, financials, project outcomes | Active section |
| 5-7 | Useful context, soft patterns, emotional processing | Historical section |
| 1-4 | Noise (debugging, pleasantries, abandoned topics) | Dropped |

## Rules

- MEMORY.md is the live index. Every file MUST be listed there with keyword-rich descriptions.
- Never load more than 6 memory files at once. Pull more only if needed.
- 3,000 token ceiling per file. When approaching, prefer splitting into sub-files over compressing.
- Source-tag every new entry: `[ChatGPT 2024-08]`, `[Gemini 2025-01]`, `[Claude 2026-04]`, `[WhatsApp]`. Dated tags are required — the temporal-aware conflict detector ignores undated active entries that share keywords with other dated ones.
- Historical context is valuable data. Never delete — archive to Historical section.
- Conflicts live in the SQLite DB (via `kontext_conflicts` MCP tool). Auto-resolve only when `feedback_conflict_patterns.md` has a matching pattern at >= 80% confidence.
- **Create new files aggressively.** 20 focused files > 5 bloated files. The MEMORY.md index handles discoverability.

## Memory Tiers

Every entry in a memory file has an implicit tier based on access patterns:

- **Hot (Active section):** Recently written or accessed. Loaded when the file is read.
- **Warm (Historical section):** Still valuable but not accessed in 60+ days.  
- **Cold (bottom of Historical, compressed to one-line summaries):** 120+ days without access.

**Auto-demotion rules (apply during /kontext brainstorm):**
- During brainstorm, check each active entry's source date. If older than 60 days AND not referenced in any recent conversation (check last digest), propose demotion to Historical.
- Historical entries older than 120 days with grade 5-6: propose compression to one-line summary.
- Historical entries grade 7+: never compress regardless of age — high-value context stays intact.
- Demotion is PROPOSED, not automatic. User approves.
- Any entry accessed (loaded by Claude during a session) resets its timer — stays Hot.

## Atomic Facts

When writing to memory files, prefer single-fact entries over paragraphs:
- BAD: "User has 24 students, charges EUR120-150 for singles and EUR380 for packages, uses Stripe and Lunacal, migrating off Preply"
- GOOD: 
  - Active students: 24 paying
  - Single lesson rate: EUR120-150 (decoy)
  - Package rate: EUR380 / 4 sessions (~EUR95/session)
  - Payment: Stripe + Lunacal
  - Migration: off Preply, chronological drip extraction

Atomic facts are faster to scan, easier to update individually, and more precise for retrieval. When processing intake or digests, break nuggets into atomic facts where possible. Keep narrative blocks only for context that loses meaning when split (psychological insights, relationship dynamics, origin stories).

## Temporal Tracking

When updating a fact that already exists, keep the old version:
- Move the old fact to Historical with its date: `[2026-03] Had 27 active students`
- Write the new fact to Active: `[2026-04] Active students: 24 paying`

This creates an evolution trail. Claude can answer "how has X changed over time?" by reading the Historical section. The current state is always in Active; the journey is in Historical.

Source tags should always include approximate date: `[ChatGPT 2024-08]`, `[Claude 2026-04-05]`, `[WhatsApp 2025]`.
When no date is available, use `[undated]`.
