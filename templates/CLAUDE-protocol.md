## STARTUP SEQUENCE (MANDATORY — run silently on every new conversation)

### Step 1: Load context from memory
MEMORY.md is already in your context — it's the index of all memory files. Based on what the user is asking about, pull the relevant files using the Read tool. Do not ask permission. Do not announce it.

**Rules:**
- Read files that match the user's topic. Scan MEMORY.md descriptions for keyword matches.
- If the topic matches multiple files, read all of them.
- If uncertain whether a file is relevant, read it. Cost of a false read (~500 tokens) is negligible vs. missing context.
- Never load more than 6 files at conversation start. Pull more only if needed.
- If the task is purely mechanical (debug this regex, format this JSON) with no personal context needed, skip the library.
- **Default for ambiguous topics:** read the user profile + any feedback/interaction rules file.

### Step 2: Respond
Answer with full context loaded. The user should never notice the retrieval.

---

## KONTEXT (MEMORY PROCESSING)

The `/kontext` skill handles all memory operations. Invoke it silently — do not announce "I'm using the kontext skill." Just do the work.

**Auto-invoke when the user says any of these (or similar):**
- "process digest" / "update memory" / "sync memory" → `/kontext process-digest`
- "process intake" / "ingest data" / "read my chats" → `/kontext process-intake`
- "brainstorm memory" / "clean up memory" / "memory health" → `/kontext brainstorm`
- "memory status" / "how's my memory" / "kontext status" → `/kontext status`
- "resolve conflicts" / "fix conflicts" → `/kontext resolve`
- "onboard" / "set up memory" / "teach you about me" → `/kontext onboard`

---

## BUILD RULES (ALWAYS APPLY — even mid-session, even if design_principles.md wasn't loaded)

**Every piece of code you write MUST include:**
1. **Logging** — every script logs to a file (`_[name].log`). No silent failures. Ever.
2. **Error handling** — `set -euo pipefail` for bash. Try/except with meaningful messages for Python. No bare exceptions.
3. **Standard repo structure** — README.md, LICENSE, .gitignore, requirements.txt or package.json. Non-negotiable.
4. **Version tracking** — version number in the code or config. Bump on every change.
5. **Plain language** — all outputs, errors, and status messages in 5th-grade language. No jargon.
6. **Approval before action** — propose changes, explain in plain language, wait for yes. Never auto-deploy.
7. **Portability** — no hardcoded paths. Use `~`, `$HOME`, auto-detection.

---

## MEMORY UPDATE AWARENESS (HIGH PRIORITY — do not deprioritize during complex tasks)

After EVERY user message, before responding to the task, spend 1 second asking yourself: "Did the user just reveal a fact, decision, preference, status change, or correction that should be in memory?" If yes, write it to the relevant file immediately — before continuing with the task response. This is not optional and not deferrable to "natural pauses."

What triggers a memory write:
- User states a fact about themselves ("I have 40 reviews now", "I moved to a new apartment")
- User makes a decision ("I'm going with Revolut", "killing the project")
- User corrects existing info ("it's 24 students, not 27")
- Project status changes ("YouTube is live", "app is stalled")
- User gives AI feedback ("stop doing X", "I like when you Y")
- User mentions a new tool, person, goal, or relationship not in memory

Do NOT interrupt the conversation to announce updates. Write silently, then respond to the task.

**After every memory file edit, broadcast the change:**
```bash
echo "CHANGED_FILE_NAME.md" >> ~/.claude/projects/_memory_broadcast
```
This tells other active sessions which files changed. A hook in those sessions detects the broadcast and re-reads the updated files automatically.

**When you receive a "[Kontext]" or "[Kontext Sync]" message from a hook:**
- `[Kontext] Session resumed or started` → Re-read MEMORY.md index and load files relevant to the current conversation topic.
- `[Kontext Sync] Memory updated: file1.md, file2.md` → Re-read those specific files.
- `[Kontext Sync] Memory files changed since last check: file1.md` → Same — re-read those files.
Do not announce any of this to the user. Silently refresh your context and continue.

**Long session rule:** In conversations exceeding ~30 exchanges, write memory updates incrementally — do not batch them for session end. Context compression can erase unwritten insights.

**At end of significant sessions, ask:** "What in the memory files needs updating?"

---

