#!/usr/bin/env python3
"""
hooks/session_summary.py — Stop hook: generates structured session summary.

Fires when Claude stops responding. Reads tool_events accumulated in this
session and writes a structured summary to the sessions table.
No AI calls — template-based. Fast and silent.

Summary fields updated:
  - investigated: pipe-delimited list of files touched
  - learned:      pipe-delimited list of high-grade (>=6) event summaries
  - files_touched: comma-delimited unique file paths
  - summary:      last 3 event summaries as an arrow chain (only if empty)

Skip gate: KONTEXT_SKIP_HOOKS=1 env var.
"""
import sys
import json
import os
import time
from pathlib import Path

if os.environ.get("KONTEXT_SKIP_HOOKS"):
    sys.exit(0)

try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)

session_id = data.get("session_id", "")

KONTEXT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KONTEXT_ROOT))

try:
    from db import KontextDB
    db = KontextDB()
    try:
        events = db.get_tool_events(session_id=session_id, since_hours=4, limit=200)
        if not events:
            sys.exit(0)

        # Files touched (unique, max 20, preserve insertion order)
        files_touched = list(dict.fromkeys(
            e["file_path"] for e in events if e.get("file_path")
        ))[:20]

        investigated = " | ".join(files_touched[:10]) if files_touched else ""
        learned_parts = [e["summary"] for e in events if e.get("grade", 0) >= 6.0][:5]
        learned = " | ".join(learned_parts)
        recent = [e["summary"] for e in reversed(events)][:3]
        auto_summary = " -> ".join(recent)

        latest_id = db.get_latest_session_id()
        if latest_id is not None:
            db._execute(
                """
                UPDATE sessions
                   SET investigated = ?,
                       learned      = ?,
                       files_touched = ?,
                       summary = CASE WHEN summary = '' THEN ? ELSE summary END
                 WHERE id = ?
                """,
                (investigated, learned, ",".join(files_touched), auto_summary, latest_id),
            )
    finally:
        db.close()

except Exception as exc:
    log_path = KONTEXT_ROOT / "_session_summary.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR"
                f" {type(exc).__name__}: {exc}\n"
            )
    except OSError:
        pass

sys.exit(0)
