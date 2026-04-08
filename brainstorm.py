"""
brainstorm.py — Memory health report.

Reads kontext.db and prints a plain-language status summary used by
the /kontext status and /kontext brainstorm skill commands.
"""

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

from db import KontextDB

TOKEN_CEILING = 3000
STALE_DAYS = 60
LOG = Path(__file__).parent / "_brainstorm.log"

import logging.handlers
_log = logging.getLogger("kontext.brainstorm")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(LOG), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)


def log(msg: str) -> None:
    _log.info(msg)


def approx_tokens(text: str) -> int:
    # Rough heuristic: ~4 chars per token.
    return max(1, len(text) // 4)


def days_since(iso_ts: str) -> int:
    if not iso_ts:
        return 0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def main() -> int:
    try:
        db = KontextDB()
    except Exception as e:
        print(f"ERROR: Could not open kontext.db — {e}")
        log(f"open failed: {e}")
        return 1

    try:
        files = db.list_files()  # {filename: count}
        all_entries = db.get_entries()
        recent = db.get_recent_changes(hours=24)
        pending_conflicts = db.get_pending_conflicts()

        # Tier breakdown
        tiers = {"active": 0, "historical": 0, "cold": 0}
        for e in all_entries:
            tiers[e.get("tier", "active")] = tiers.get(e.get("tier", "active"), 0) + 1

        # Per-file token estimates and stale counts
        file_stats = []
        stale_total = 0
        bloated = []
        for fname, count in files.items():
            entries = [e for e in all_entries if e["file"] == fname]
            tokens = sum(approx_tokens(e["fact"]) for e in entries)
            stale = sum(
                1
                for e in entries
                if e.get("tier") == "active"
                and days_since(e.get("last_accessed", "")) >= STALE_DAYS
            )
            stale_total += stale
            file_stats.append((fname, count, tokens, stale))
            if tokens > TOKEN_CEILING:
                bloated.append((fname, tokens))

        file_stats.sort(key=lambda x: x[2], reverse=True)

        print("=" * 60)
        print(" KONTEXT HEALTH REPORT")
        print("=" * 60)
        print(f" Files tracked:       {len(files)}")
        print(f" Total entries:       {len(all_entries)}")
        print(f"   Active:            {tiers['active']}")
        print(f"   Historical:        {tiers['historical']}")
        print(f"   Cold:              {tiers['cold']}")
        print(f" Pending conflicts:   {len(pending_conflicts)}")
        print(f" Updated last 24h:    {len(recent)}")
        print(f" Stale active (>{STALE_DAYS}d): {stale_total}")
        print(f" Bloated files (>{TOKEN_CEILING} tok): {len(bloated)}")
        print()
        print(" Top files by size:")
        print(f" {'file':<40} {'entries':>8} {'~tokens':>9} {'stale':>6}")
        print(" " + "-" * 64)
        for fname, count, tokens, stale in file_stats[:15]:
            mark = "  !" if tokens > TOKEN_CEILING else "   "
            print(f" {fname[:40]:<40} {count:>8} {tokens:>9}{mark} {stale:>3}")

        if bloated:
            print()
            print(" Files over token ceiling — consider splitting:")
            for fname, tokens in bloated:
                print(f"   - {fname} ({tokens} tokens)")

        if pending_conflicts:
            print()
            print(" Pending conflicts:")
            for c in pending_conflicts[:10]:
                print(f"   #{c['id']} in {c['file']}")

        print("=" * 60)
        log(
            f"ok files={len(files)} entries={len(all_entries)} "
            f"conflicts={len(pending_conflicts)} bloated={len(bloated)}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
