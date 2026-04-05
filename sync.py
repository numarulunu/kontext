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
from pathlib import Path
from datetime import datetime

_LOG_FILE = Path(__file__).parent / "_kontext.log"
logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("kontext.sync")


def sync(memory_dir: Path = None, dry_run: bool = False) -> dict:
    """Compare flat files with DB, import any entries that exist in files but not in DB.
    Returns {synced: int, skipped: int, files_checked: int}."""
    from db import KontextDB
    from migrate import parse_memory_file
    from mcp_server import find_memory_dir

    if memory_dir is None:
        memory_dir = find_memory_dir()
        if not memory_dir:
            log.warning("SYNC: No memory directory found")
            return {"synced": 0, "skipped": 0, "files_checked": 0}

    db = KontextDB()
    synced = 0
    skipped = 0
    files_checked = 0

    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "MEMORY.md" or md_file.name.startswith("_"):
            continue

        files_checked += 1
        entries = parse_memory_file(md_file)

        for entry in entries:
            # Check if this fact already exists in DB
            existing = db.search_entries(entry["fact"][:50])
            already_in_db = any(
                e["file"] == md_file.name and e["fact"] == entry["fact"]
                for e in existing
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

    db.close()
    return {"synced": synced, "skipped": skipped, "files_checked": files_checked}


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    result = sync(dry_run=dry_run)
    print(f"Sync complete: {result['synced']} imported, {result['skipped']} skipped, {result['files_checked']} files checked")
