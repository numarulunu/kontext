#!/usr/bin/env python3
"""Read the router's `_loaded_files.jsonl` log and bump `access_count` for
every entry in every file that was auto-loaded.

Why this exists: `kontext_route.py` auto-loads memory files at session
start + per-prompt via hooks. `access_count` was designed to track "how
often has this entry been surfaced to Claude's context" — but the only
caller of `bump_access_count` is the MCP's `kontext_query` tool path,
which is rarely invoked (auto-routing covers most retrievals). Result:
87% of entries sit at access_count=0 forever, making the rerank()
multiplier (`log1p(access_count)`) a no-op for them.

This sweep treats every auto-load as a shared-grade access for the
entries in that file. It reads the router's log (one JSONL line per
route() call that loaded files), groups by filename, and issues
`bump_access_count_batch` per file.

Usage:
    python tools/bump_access_from_routing_log.py              # sweep all new entries since last run
    python tools/bump_access_from_routing_log.py --dry-run    # report what would change
    python tools/bump_access_from_routing_log.py --all        # force re-sweep entire log (for recovery)

State: last-processed offset is stored in `_loaded_files.cursor` next
to the log. Re-running is idempotent — only new lines since the cursor
bump access counts.

Call site: wire into cron (every 10-15 min) OR into a Stop hook. One
call per day is enough to pick up session-end signal; more often just
tightens feedback-loop latency on the rerank.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path


# Defaults. Override via env vars or CLI.
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_LOG = DEFAULT_CLAUDE_DIR / "_loaded_files.jsonl"
DEFAULT_CURSOR = DEFAULT_CLAUDE_DIR / "_loaded_files.cursor"
DEFAULT_DB = None  # resolved via find_db()


def find_db() -> Path:
    """Locate the Kontext DB. Checks XDG, Roaming (Windows), and the
    KONTEXT_DB_PATH env var (highest priority)."""
    env = os.environ.get("KONTEXT_DB_PATH")
    if env and Path(env).exists():
        return Path(env)
    candidates = [
        Path.home() / "AppData" / "Roaming" / "Kontext" / "kontext.db",
        Path.home() / ".config" / "kontext" / "kontext.db",
        Path.home() / ".kontext" / "server.db",
        Path("/app/data/kontext.db"),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise SystemExit("kontext DB not found — set KONTEXT_DB_PATH env var")


def load_cursor(cursor_path: Path) -> int:
    if not cursor_path.exists():
        return 0
    try:
        return int(cursor_path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def save_cursor(cursor_path: Path, offset: int) -> None:
    tmp = cursor_path.with_suffix(".tmp")
    tmp.write_text(str(offset), encoding="utf-8")
    tmp.replace(cursor_path)


def sweep(log_path: Path, cursor_path: Path, db_path: Path,
          dry_run: bool = False, force_all: bool = False) -> dict:
    if not log_path.exists():
        return {"loaded_events": 0, "entries_bumped": 0, "skipped_files": 0,
                "new_offset": 0, "note": "no log file"}

    start_offset = 0 if force_all else load_cursor(cursor_path)
    file_hits: Counter = Counter()
    events = 0

    with log_path.open("rb") as f:
        f.seek(start_offset)
        for raw in f:
            try:
                row = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            events += 1
            for fn in row.get("files") or []:
                file_hits[fn] += 1
        new_offset = f.tell()

    if not file_hits:
        if not dry_run:
            save_cursor(cursor_path, new_offset)
        return {"loaded_events": events, "entries_bumped": 0,
                "skipped_files": 0, "new_offset": new_offset,
                "note": "no file hits since cursor"}

    conn = sqlite3.connect(str(db_path))
    total_bumped = 0
    skipped = 0
    try:
        for fn, hits in file_hits.items():
            # Fire one UPDATE per file — bump every active entry by the
            # hit count this sweep. Cold/historical entries are left
            # alone; they're retired for a reason and shouldn't be
            # resurrected by an auto-load event.
            cur = conn.execute(
                "UPDATE entries SET access_count = access_count + ?, "
                "last_accessed = datetime('now') "
                "WHERE file = ? AND tier = 'active'",
                (hits, fn),
            )
            if cur.rowcount == 0:
                skipped += 1
            total_bumped += cur.rowcount
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            save_cursor(cursor_path, new_offset)
    finally:
        conn.close()

    return {
        "loaded_events": events,
        "files_touched": len(file_hits),
        "entries_bumped": total_bumped,
        "skipped_files": skipped,
        "new_offset": new_offset,
        "note": "dry-run — nothing persisted" if dry_run else "ok",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--cursor", default=str(DEFAULT_CURSOR))
    ap.add_argument("--db", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="re-sweep entire log (ignores cursor)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else find_db()
    result = sweep(
        log_path=Path(args.log),
        cursor_path=Path(args.cursor),
        db_path=db_path,
        dry_run=args.dry_run,
        force_all=args.all,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
