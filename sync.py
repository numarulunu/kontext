"""
Kontext Sync — Import flat-file edits that bypassed the database.

Runs at session start. Compares flat markdown files with database entries.
If a file was edited directly (not through kontext_write), imports the changes.

Usage:
    python sync.py              # Sync from auto-detected memory dir
    python sync.py --dry-run    # Report what would change
"""

import sys
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timezone

_LOG_FILE = Path(__file__).parent / "_kontext.log"
log = logging.getLogger("kontext.sync")
if not log.handlers:
    log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(_LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_h)


_DREAM_STAMP = Path(__file__).parent / "_dream_last"


def _maybe_dream(db) -> int:
    """Run dream consolidation if >24h since last run. Returns action count."""
    now = datetime.now(timezone.utc)
    if _DREAM_STAMP.exists():
        try:
            last = datetime.fromisoformat(_DREAM_STAMP.read_text(encoding="utf-8").strip())
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < 86400:
                return 0
        except (ValueError, OSError):
            pass

    try:
        from dream import dream as run_dream
        results = run_dream(db)
        total = sum(
            v for stats in results.values() for k, v in stats.items()
            if k in ("merged", "anchored", "auto_resolved", "compressed", "purged")
        )
        if total > 0:
            log.info(f"DREAM: {total} actions — {results}")
            # Re-export after changes. If exports fail, do NOT stamp — next run retries.
            from export import export_all, export_memory_index
            from mcp_server import find_memory_dir
            mem_dir = find_memory_dir()
            if mem_dir:
                export_all(db, mem_dir)
                export_memory_index(db, mem_dir)
        # Stamp only after dream + exports both succeed. Previously the stamp
        # was written before export_memory_index, so a half-exported dream run
        # could leave the stamp advanced and prevent a retry next session.
        _DREAM_STAMP.write_text(now.isoformat(), encoding="utf-8")
        return total
    except Exception as e:
        log.warning(f"DREAM: failed — {e}")
        return 0


def _run_decay(db) -> int:
    """Run score decay. Returns number of entries affected."""
    before = db.conn.execute("SELECT COUNT(*) FROM entries WHERE grade > 1").fetchone()[0]
    db.decay_scores()  # defaults: 60 days, -0.5 grade
    after = db.conn.execute("SELECT COUNT(*) FROM entries WHERE grade > 1").fetchone()[0]
    decayed = before - after
    if decayed > 0:
        log.info(f"DECAY: {decayed} entries dropped to grade 1 (cold)")
    return decayed


def sync(memory_dir: Path = None, dry_run: bool = False, db=None) -> dict:
    """Compare flat files with DB, import any entries that exist in files but not in DB.
    Returns {synced: int, skipped: int, files_checked: int}.

    Args:
        db: Optional KontextDB instance. If None, uses production DB.
    """
    from db import KontextDB
    from migrate import parse_memory_file
    from mcp_server import find_memory_dir

    if memory_dir is None:
        memory_dir = find_memory_dir()
        if not memory_dir:
            log.warning("SYNC: No memory directory found")
            return {"synced": 0, "skipped": 0, "files_checked": 0}

    owns_db = db is None
    if owns_db:
        db = KontextDB()
    synced = 0
    skipped = 0
    files_checked = 0

    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "MEMORY.md" or md_file.name.startswith("_"):
            continue

        files_checked += 1
        entries = parse_memory_file(md_file)

        # Cache DB facts for this file — avoids re-querying per entry
        db_facts_rows = db.conn.execute(
            "SELECT fact FROM entries WHERE file = ?", (md_file.name,)
        ).fetchall()
        db_facts = [row[0] for row in db_facts_rows]
        db_facts_lower = [f.lower() for f in db_facts]
        db_facts_set = set(db_facts)  # For exact match (O(1))

        for entry in entries:
            # Dedup: exact match first (fast O(1)), then fuzzy 85% similarity.
            if entry["fact"] in db_facts_set:
                already_in_db = True
            else:
                # Fuzzy check against cached facts
                from difflib import SequenceMatcher
                entry_lower = entry["fact"].lower()
                already_in_db = any(
                    SequenceMatcher(None, entry_lower, f).ratio() >= 0.85
                    for f in db_facts_lower
                )

            if not already_in_db:
                if dry_run:
                    print(f"  WOULD SYNC: [{md_file.name}] {entry['fact'][:80]}")
                else:
                    db.add_entry(
                        file=md_file.name,
                        fact=entry["fact"],
                        source=entry.get("source", "[file-sync]"),
                        grade=entry.get("grade", 5),
                        tier=entry.get("tier", "active"),
                    )
                synced += 1
            else:
                skipped += 1

    if synced > 0 and not dry_run:
        log.info(f"SYNC: imported {synced} entries from {files_checked} files ({skipped} already in DB)")
    elif synced == 0:
        log.info(f"SYNC: all {skipped} entries already in DB, nothing to import")

    # Decay and dream are writes. Skip on dry-run so `--dry-run` is truly read-only.
    if dry_run:
        decayed = 0
        dreamed = 0
    else:
        decayed = _run_decay(db)
        dreamed = _maybe_dream(db)

    if owns_db:
        db.close()
    return {"synced": synced, "skipped": skipped, "files_checked": files_checked, "decayed": decayed, "dreamed": dreamed}


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    result = sync(dry_run=dry_run)
    print(f"Sync complete: {result['synced']} imported, {result['skipped']} skipped, {result['files_checked']} files checked")
