#!/usr/bin/env python3
"""On session-end, ask Haiku if anything SCAR-worthy happened; if yes,
append one line to the relevant project log file.

Stop-hook invariants:
- Must finish in under ~8 seconds (Claude Code Stop timeout is lenient
  but a hung hook degrades UX).
- Zero-cost on idle sessions — early-exit if no tool_events were logged
  in the last hour.
- Never raises — all errors swallowed + logged. A failed SCAR write
  must not break session termination.
- API key resolution: cloud/config_store for base_url + model + key,
  with env var fallback (same pattern as dashboard_synth).

What counts as SCAR-worthy (delegated to Haiku judgement):
- A debug cycle that ended in a non-obvious fix.
- An architectural decision with a reason trace.
- A scar you'd want to remember 3 months from now (not "wrote code").
- An incident where something silently failed (log for next time).

What is NOT SCAR-worthy:
- Routine code edits without a surprise.
- Read-only exploration / Q&A.
- Single-command runs.

If Haiku says "nothing worth writing" → script exits 0 silently.
Otherwise → appends one line to the matching project_*_log.md file
(default: project_kontext_log.md if no specific match).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Paths + setup
THIS = Path(__file__).resolve()
ROOT = THIS.parents[1]
sys.path.insert(0, str(ROOT))

LOG_PATH = Path.home() / ".claude" / "_auto_scar.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("auto_scar")


SYSTEM_PROMPT = """You are reviewing a Claude Code session that just ended. You have the last ~45 minutes of user prompts + tool events.

Decide: is any ONE thing worth writing to a persistent memory log?

Write a SCAR only if the session produced ONE of:
- A debug cycle that ended in a non-obvious fix (tag: SCAR)
- An architectural decision with reasoning (tag: ARCH)
- An evolution: system went from X to Y because Z (tag: EVO)
- A measured performance or cost finding (tag: PERF)
- An open design question that needs to be parked (tag: OPEN)

Skip (return {"scar": null}) for:
- Routine edits with no surprise
- Read-only exploration or Q&A
- A session that just ran a single command
- Work that's incomplete mid-stream

If you decide to write a SCAR, it MUST be:
- One line (≤160 chars)
- Starts with the tag: "SCAR:" / "ARCH:" / "EVO:" / "PERF:" / "OPEN:"
- States the why, not the what — a reader in 3 months needs the reason
- No "the user" — write in direct voice

Return ONLY JSON: {"scar": "SCAR: … (≤160 chars)"} or {"scar": null}."""


def find_db() -> Path | None:
    env = os.environ.get("KONTEXT_DB_PATH")
    if env and Path(env).exists():
        return Path(env)
    for p in [
        Path.home() / "AppData" / "Roaming" / "Kontext" / "kontext.db",
        Path.home() / ".config" / "kontext" / "kontext.db",
        Path.home() / ".kontext" / "server.db",
        Path("/app/data/kontext.db"),
    ]:
        if p.exists():
            return p
    return None


def load_session_events(db_path: Path, minutes: int = 60) -> dict:
    """Pull the last N minutes of user_prompts + tool_events from DB."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except Exception as e:
        log.error("DB_OPEN_FAIL: %s", e)
        return {"prompts": [], "tools": []}

    try:
        prompts = [
            {"ts": r["created_at"], "content": (r["content"] or "")[:300]}
            for r in conn.execute(
                "SELECT created_at, content FROM user_prompts "
                f"WHERE created_at > datetime('now', '-{minutes} minutes') "
                "ORDER BY created_at ASC LIMIT 50"
            )
        ]
        tools = [
            {"ts": r["created_at"], "name": r["tool_name"],
             "summary": (r["summary"] or "")[:200],
             "file": r["file_path"] or "",
             "grade": r["grade"]}
            for r in conn.execute(
                "SELECT created_at, tool_name, summary, file_path, grade FROM tool_events "
                f"WHERE created_at > datetime('now', '-{minutes} minutes') "
                "ORDER BY created_at ASC LIMIT 80"
            )
        ]
    except sqlite3.OperationalError as e:
        log.warning("DB schema mismatch: %s", e)
        prompts, tools = [], []
    finally:
        conn.close()
    return {"prompts": prompts, "tools": tools}


def pick_log_file(memory_root: Path, tools: list[dict]) -> Path:
    """Heuristic: if most tool file_paths are in a Kontext-like project,
    write to project_kontext_log.md; otherwise write to the most-touched
    project's log if one exists, or default to project_kontext_log.md.
    """
    default = memory_root / "project_kontext_log.md"
    # Count tool events by inferred project
    from collections import Counter
    c: Counter = Counter()
    for t in tools:
        p = (t.get("file") or "").lower()
        if "kontext" in p:
            c["project_kontext_log.md"] += 1
        elif "vocality" in p:
            c["project_vocality_content.md"] += 1
        elif "mastermind" in p:
            c["project_mastermind_log.md"] += 1
        elif "transcriptor" in p:
            c["project_transcriptor_changelog.md"] += 1
    if c:
        top = c.most_common(1)[0][0]
        candidate = memory_root / top
        if candidate.exists():
            return candidate
    return default if default.exists() else memory_root / "project_kontext_log.md"


def main() -> int:
    t_start = time.time()
    db_path = find_db()
    if not db_path:
        log.info("NO_DB — exiting")
        return 0

    events = load_session_events(db_path, minutes=60)
    n_p = len(events["prompts"])
    n_t = len(events["tools"])
    if n_p + n_t < 3:
        log.info("IDLE_SESSION prompts=%d tools=%d — skip", n_p, n_t)
        return 0

    # Key resolution — same chain as dashboard_synth
    try:
        from cloud.config_store import (
            get_anthropic_api_key, get_anthropic_base_url, get_anthropic_model,
        )
        api_key = get_anthropic_api_key()
        base_url = get_anthropic_base_url()
        model = get_anthropic_model()
    except Exception:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        model = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5"
    if not api_key:
        log.info("NO_API_KEY — skip")
        return 0

    user_msg = f"{SYSTEM_PROMPT}\n\n---\n\nLast-hour prompts ({n_p}):\n"
    for p in events["prompts"][-12:]:
        user_msg += f"- [{p['ts']}] {p['content']}\n"
    user_msg += f"\nLast-hour tool events ({n_t}):\n"
    for t in events["tools"][-20:]:
        user_msg += f"- [{t['ts']}] {t['name']} {t['summary']}\n"

    try:
        import urllib.request, urllib.error
        body = json.dumps({
            "model": model,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": user_msg}],
        }).encode("utf-8")
        url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com/v1") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        # Direct Anthropic wants x-api-key instead; detect by hostname
        if not base_url or "anthropic.com" in (base_url or ""):
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com/v1") + "/messages"
            # Anthropic expects its own shape — wrap in messages API
            body = json.dumps({
                "model": model,
                "max_tokens": 200,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            }).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=7) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("API_CALL_FAIL: %s", e)
        return 0

    # Extract text (OpenAI shape vs Anthropic shape)
    try:
        if "choices" in payload:
            text = payload["choices"][0]["message"]["content"]
        else:
            text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
    except Exception:
        text = ""

    # Parse JSON response
    scar: str | None = None
    try:
        stripped = text.strip()
        if stripped.startswith("```"):
            import re
            m = re.search(r"\{[\s\S]*\}", stripped)
            stripped = m.group(0) if m else "{}"
        data = json.loads(stripped)
        scar = (data.get("scar") or "").strip() or None
    except Exception as e:
        log.warning("PARSE_FAIL: %s text=%r", e, text[:200])
        return 0

    if not scar or scar.lower() in ("null", "none"):
        log.info("NO_SCAR prompts=%d tools=%d elapsed=%.2fs", n_p, n_t, time.time() - t_start)
        return 0

    # Append to the matching log file.
    memory_root = (
        Path.home() / ".claude" / "projects"
        / "C--Users-Gaming-PC-Desktop-Claude-Personal-Context" / "memory"
    )
    # Server fallback
    if not memory_root.exists():
        alt = Path.home() / ".kontext" / "memory"
        if alt.exists():
            memory_root = alt
    log_file = pick_log_file(memory_root, events["tools"])
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        line = f"\n[Claude {today}] {scar[:200]}\n"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line)
        log.info("SCAR_WRITTEN file=%s line=%r elapsed=%.2fs",
                 log_file.name, scar[:120], time.time() - t_start)
        print(f"scar: {scar[:120]}", file=sys.stderr)
    except Exception as e:
        log.error("WRITE_FAIL: %s", e)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — never break the hook
        log.exception("UNEXPECTED: %s", exc)
        sys.exit(0)
