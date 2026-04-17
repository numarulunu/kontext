#!/usr/bin/env python3
"""
hooks/session_summary.py - Stop hook: generates structured session summary.

Fires when Claude stops responding. Reads tool_events accumulated for this
hook session and writes a structured summary to the sessions table.

Skip gate: KONTEXT_SKIP_HOOKS=1 env var.
"""
import json
import os
import sys
import time
from pathlib import Path


def build_summary_fields(events: list[dict]) -> dict[str, str]:
    """Build session summary fields from newest-first tool events."""
    files_touched = list(dict.fromkeys(
        e["file_path"] for e in events if e.get("file_path")
    ))[:20]

    investigated = " | ".join(files_touched[:10]) if files_touched else ""
    learned_events = [e for e in events if e.get("grade", 0) >= 6.0]
    learned_events = sorted(
        enumerate(learned_events),
        key=lambda item: (-float(item[1].get("grade", 0)), item[0]),
    )
    learned = " | ".join(e["summary"] for _, e in learned_events[:5])
    auto_summary = " -> ".join(e["summary"] for e in events[:3])

    return {
        "investigated": investigated,
        "learned": learned,
        "files_touched": ",".join(files_touched),
        "summary": auto_summary,
    }


def main() -> int:
    if os.environ.get("KONTEXT_SKIP_HOOKS"):
        return 0

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        return 0

    kontext_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(kontext_root))

    try:
        from db import KontextDB
        db = KontextDB()
        try:
            events = db.get_tool_events(session_id=session_id, limit=200)
            if not events:
                return 0

            fields = build_summary_fields(events)
            db.upsert_session_summary(
                hook_session_id=session_id,
                investigated=fields["investigated"],
                learned=fields["learned"],
                files_touched=fields["files_touched"],
                summary=fields["summary"],
            )
        finally:
            db.close()

    except Exception as exc:
        log_path = kontext_root / "_session_summary.log"
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
