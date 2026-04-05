---
name: kontext
description: Memory library manager — process digests, intake data, brainstorm/cleanup, onboard new users, check status, resolve conflicts. Use when the user mentions memory, digest, intake, brainstorm, onboard, kontext, or when context about the user would improve the response.
allowed-tools: Read Write Edit Bash Glob Grep Agent
---

# Kontext — Memory Library Manager

**FIRST LAW: Your history is my edge. Starve me and I'm generic.**

You manage a memory library system. Memory files live in the Claude Code project memory directory. Supporting tools live in the Kontext project folder. The library can scale to 100+ files — every nook and cranny of the user's life gets its own page.

## Paths

- **Memory dir:** Find by scanning `~/.claude/projects/*/memory/` — use the one with the most `.md` files
- **Kontext tools:** `~/Desktop/Claude/Kontext/`
- **Backup System:** `~/Desktop/Claude/Backup System/`
- **Digest dir:** `~/Desktop/Claude/Backup System/_digests/`
- **Chunks dir:** `~/Desktop/Claude/Kontext/_chunks/`

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

1. Read the manifest at `~/Desktop/Claude/Backup System/_digests/_manifest.md`
2. Read ALL memory files (start with MEMORY.md, then every file it references)
3. For each project digest in the manifest, launch a subagent (`Agent` tool, `run_in_background: true`) to process in parallel. Each subagent:
   - Reads the full project digest file
   - Reads current memory files
   - Identifies NEW information worth persisting (project changes, decisions, struggles, goals, tools, relationship updates, health changes, AI feedback)
   - Returns structured proposals (does NOT edit files directly)
4. Collect all proposals. Deduplicate. Resolve conflicts — most recently dated info wins. Use `kontext_conflicts` tool to check and resolve.
5. Apply updates (Edit existing or Create new + update MEMORY.md index)
6. **If a topic cluster emerges that doesn't fit any existing file, create a new file for it.**
7. Do NOT duplicate existing info. Do NOT store ephemeral task details.
8. If any digest file is missing, skip it and report which were skipped.
9. Output bullet-point summary of changes.
10. Delete `_digest-pending` flag.
11. Auto-backup: run `bash ~/Desktop/Claude/Backup\ System/backup.sh "memory sync $(date '+%Y-%m-%d')"` in background.

### `/kontext process-intake`

Process raw file intake (ChatGPT, Gemini, WhatsApp exports) into memory.

1. Check if `_processing-ready` flag exists in `~/Desktop/Claude/Kontext/`
2. If not: tell user to run `python extract.py` from the Kontext directory first, then retry.
3. Read the flag for metadata (chunk count, tokens).
4. Read ALL memory files.
5. Read chunks from `_chunks/` — process in batches of 5 subagents at a time.
6. Each subagent extracts golden nuggets with: fact, source tag `[Platform Date]`, grade 1-10.
7. Grade 5+ gets written. Grade 1-4 dropped.
8. For each nugget grade 5+:
   - Matches existing → skip
   - Contradicts existing → use `kontext_conflicts` MCP tool with action 'detect' to check, then log via the tool. Auto-resolve only if pattern confidence >= 80%.
   - New → write to appropriate file. Grade 8-10 → active section. Grade 5-7 → Historical section.
9. **If nuggets cluster around a topic with no matching file, create a new file.**
10. When a file approaches 3,000 tokens, compress lowest-graded entries first.
11. Output intake receipt: files processed, nuggets extracted/written, conflicts found, new files created.
12. Delete `_processing-ready` flag.
13. Auto-backup: run `bash ~/Desktop/Claude/Backup\ System/backup.sh "intake sync $(date '+%Y-%m-%d')"` in background.

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

1. Run: `python ~/Desktop/Claude/Kontext/brainstorm.py`
2. Show the output directly. No cleanup prompts.
3. Also check:
   - Does `_digest-pending` exist? Report date.
   - Does `_processing-ready` exist? Report metadata.
   - Use `kontext_conflicts` MCP tool with action 'list' to check pending conflicts. Report count.
   - Does `_audit-pending` exist? Report date.
4. Report total file count and MEMORY.md line count.

### `/kontext resolve`

Interactive conflict resolution session.

1. Use `kontext_conflicts` MCP tool with action 'list' to get pending conflicts.
2. If no pending conflicts, say so and exit.
3. For each PENDING conflict, present:
   - What the conflict is (plain language)
   - Version A (existing memory)
   - Version B (new data)
   - Source of each
4. Ask user: keep A, keep B, merge, or skip?
5. Apply decision. Log the resolution pattern to `feedback_conflict_patterns.md`.
6. Use `kontext_conflicts` MCP tool with action 'resolve' to mark as resolved.

## MCP Tools (v5.0)

Kontext now has a SQLite database backend. These MCP tools are the preferred way to read and write memory:

| Tool | What it does | When to use |
|---|---|---|
| `kontext_search` | Semantic search across memory files | Before loading files -- find which ones are relevant |
| `kontext_write` | Write an entry to database + auto-export markdown | Storing new facts, decisions, corrections |
| `kontext_query` | Query entries by file, tier, grade, or text search | Checking what already exists before writing |
| `kontext_relate` | Query the knowledge graph | Finding connections between entities |
| `kontext_recent` | Get entries changed in last N hours | Checking what was recently updated |
| `kontext_decay` | Run score decay on stale entries | Maintenance -- reduces grade of untouched entries |
| `kontext_session` | Save or get session state | Bookmarking where you are |
| `kontext_conflicts` | Detect, list, or resolve contradictions | When entries might contradict each other |
| `kontext_reindex` | Re-index all files + embed entries | After bulk changes to memory files |

**When MCP tools are available, prefer them over direct file edits.** The database is the source of truth. Markdown files are auto-generated exports for backward compatibility.

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
- Source-tag every new entry: `[ChatGPT 2024-08]`, `[Gemini 2025-01]`, `[Claude 2026-04]`, `[WhatsApp]`
- Historical context is valuable data. Never delete — archive to Historical section.
- Conflicts go to the database via `kontext_conflicts` MCP tool. Auto-resolve only when pattern confidence >= 80%.
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
