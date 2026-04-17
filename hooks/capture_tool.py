#!/usr/bin/env python3
"""
hooks/capture_tool.py - PostToolUse hook: captures state-changing tool events.

Invoked by Claude Code after each tool execution. Writes directly to the
tool_events table. Outputs nothing.

Skip gate: KONTEXT_SKIP_HOOKS=1 env var.
"""
import json
import os
import re
import sys
import time
from pathlib import Path


CAPTURE_TOOLS = {"Edit", "MultiEdit", "Write", "Bash", "NotebookEdit"}
THROTTLE_SECONDS = 20

_READ_ONLY_BASH = re.compile(
    r"^(ls|head|tail|grep|find|pwd|which|env|"
    r"git\s+log|git\s+status|git\s+diff|git\s+show|git\s+branch|"
    r"python\s+-m\s+pytest|pytest)\b",
)
_WRITE_OPERATOR = re.compile(r"(>>?|2>|&>|tee\s+|Set-Content|Add-Content|Out-File)", re.I)


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "global")[:80]


def throttle_file_for(session_id: str, tool_name: str) -> Path:
    """Return the throttle stamp path scoped to both session and tool."""
    return (
        Path.home()
        / ".claude"
        / f".kontext_tool_{_safe_key(session_id)}_{_safe_key(tool_name.lower())}_last"
    )


def should_skip_bash(command: str) -> bool:
    """Skip only plainly read-only Bash commands."""
    stripped = command.strip()
    return bool(_READ_ONLY_BASH.match(stripped)) and not _WRITE_OPERATOR.search(stripped)


def build_summary(tool_name: str, tool_input: dict) -> tuple[str | None, str | None, float]:
    """Return (summary, file_path, grade) or (None, None, 0) to skip."""
    if tool_name == "Edit":
        fp = tool_input.get("file_path", "?")
        old = str(tool_input.get("old_string", ""))[:50].replace("\n", "\\n")
        new = str(tool_input.get("new_string", ""))[:50].replace("\n", "\\n")
        return f"Edited {fp}: '{old}' -> '{new}'", tool_input.get("file_path"), 6.0

    if tool_name == "MultiEdit":
        fp = tool_input.get("file_path", "?")
        n = len(tool_input.get("edits", []))
        return f"Multi-edited {fp}: {n} change(s)", tool_input.get("file_path"), 6.0

    if tool_name == "Write":
        fp = tool_input.get("file_path", "?")
        return f"Created file: {fp}", tool_input.get("file_path"), 5.0

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))[:120]
        if should_skip_bash(cmd):
            return None, None, 0.0
        return f"Ran: {cmd}", None, 5.0

    if tool_name == "NotebookEdit":
        fp = tool_input.get("notebook_path", "?")
        return f"Edited notebook: {fp}", tool_input.get("notebook_path"), 5.0

    return None, None, 0.0


def throttled(session_id: str, tool_name: str, now: float | None = None) -> bool:
    """Return True if this tool/session was captured too recently."""
    now = time.time() if now is None else now
    throttle_file = throttle_file_for(session_id, tool_name)
    try:
        last = float(throttle_file.read_text(encoding="utf-8").strip())
        if now - last < THROTTLE_SECONDS:
            return True
    except (FileNotFoundError, ValueError, OSError):
        pass

    try:
        throttle_file.parent.mkdir(parents=True, exist_ok=True)
        throttle_file.write_text(str(now), encoding="utf-8")
    except OSError:
        pass
    return False


def main() -> int:
    if os.environ.get("KONTEXT_SKIP_HOOKS"):
        return 0

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "")

    if tool_name not in CAPTURE_TOOLS:
        return 0
    if throttled(session_id, tool_name):
        return 0

    summary, file_path, grade = build_summary(tool_name, tool_input)
    if not summary:
        return 0

    kontext_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(kontext_root))

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
        log_path = kontext_root / "_capture_tool.log"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR"
                    f" {type(exc).__name__}: {exc}\n"
                )
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
