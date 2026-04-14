#!/usr/bin/env python3
"""
hooks/capture_tool.py — PostToolUse hook: captures state-changing tool events.

Invoked by Claude Code after each tool execution. Writes directly to
tool_events table. Outputs nothing — fully silent.

Throttled: 1 write per tool type per 20 seconds (avoids DB spam in loops).
Skip gate: KONTEXT_SKIP_HOOKS=1 env var.
"""
import sys
import json
import os
import re
import time
from pathlib import Path

# --- Skip gate ---
if os.environ.get("KONTEXT_SKIP_HOOKS"):
    sys.exit(0)

# --- Read hook payload from stdin ---
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)

tool_name = data.get("tool_name", "")
tool_input = data.get("tool_input", {})
session_id = data.get("session_id", "")

# --- Only capture state-changing tools ---
CAPTURE_TOOLS = {"Edit", "MultiEdit", "Write", "Bash", "NotebookEdit"}
if tool_name not in CAPTURE_TOOLS:
    sys.exit(0)

# --- Throttle: max 1 capture per tool type per 20s ---
throttle_dir = Path.home() / ".claude"
throttle_file = throttle_dir / f".kontext_tool_{tool_name.lower()}_last"
now = time.time()
try:
    last = float(throttle_file.read_text(encoding="utf-8").strip())
    if now - last < 20:
        sys.exit(0)
except (FileNotFoundError, ValueError, OSError):
    pass
try:
    throttle_file.write_text(str(now), encoding="utf-8")
except OSError:
    pass


# --- Build summary from tool input ---
_READ_ONLY_BASH = re.compile(
    r"^(ls|cat|head|tail|grep|find|echo|pwd|which|env|"
    r"git\s+log|git\s+status|git\s+diff|git\s+show|git\s+branch|"
    r"python\s+-m\s+pytest|pytest|python\s+-c)",
)


def _build_summary() -> tuple[str | None, str | None, float]:
    """Return (summary, file_path, grade) or (None, None, 0) to skip."""
    if tool_name == "Edit":
        fp = tool_input.get("file_path", "?")
        old = str(tool_input.get("old_string", ""))[:50].replace("\n", "↵")
        new = str(tool_input.get("new_string", ""))[:50].replace("\n", "↵")
        return f"Edited {fp}: '{old}' → '{new}'", tool_input.get("file_path"), 6.0

    if tool_name == "MultiEdit":
        fp = tool_input.get("file_path", "?")
        n = len(tool_input.get("edits", []))
        return f"Multi-edited {fp}: {n} change(s)", tool_input.get("file_path"), 6.0

    if tool_name == "Write":
        fp = tool_input.get("file_path", "?")
        return f"Created file: {fp}", tool_input.get("file_path"), 5.0

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))[:120]
        if _READ_ONLY_BASH.match(cmd.strip()):
            return None, None, 0.0
        return f"Ran: {cmd}", None, 5.0

    if tool_name == "NotebookEdit":
        fp = tool_input.get("notebook_path", "?")
        return f"Edited notebook: {fp}", tool_input.get("notebook_path"), 5.0

    return None, None, 0.0


summary, file_path, grade = _build_summary()
if not summary:
    sys.exit(0)

# --- Write to DB ---
KONTEXT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KONTEXT_ROOT))

# Throttle is written before the DB write — if the DB write fails, the event is
# dropped and won't be retried for 20s. Acceptable: summaries are low-stakes.
try:
    from db import KontextDB
    db = KontextDB()
    try:
        db.add_tool_event(
            session_id=session_id,
            tool_name=tool_name,
            summary=summary,
            file_path=file_path,
            grade=grade,
        )
    finally:
        db.close()
except Exception as exc:
    log_path = KONTEXT_ROOT / "_capture_tool.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR"
                f" {type(exc).__name__}: {exc}\n"
            )
    except OSError:
        pass

sys.exit(0)
