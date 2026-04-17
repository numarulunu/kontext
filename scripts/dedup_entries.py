"""Detect (and optionally mark) duplicate rows in the entries table.

Dedup strategy: hash the NFC-normalized, case-folded, whitespace-stripped
`fact` text. For each dup group, keep the oldest id (lowest id preserves
any existing foreign-key references) and mark the rest as superseded via
a new nullable column `superseded_by INTEGER REFERENCES entries(id)`.
Never hard-deletes on this pass -- superseded rows become invisible to
search_entries / semantic_search but remain in the DB for recovery.

Usage:
    python -m scripts.dedup_entries --dry-run    # default, prints report only
    python -m scripts.dedup_entries --apply      # actually mark dups
    python -m scripts.dedup_entries --samples 10 # show N sample groups

Log: _dedup_entries.log next to this script.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import logging.handlers
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# Windows terminals default to CP1252; force UTF-8 so sample facts with
# arrows/em-dashes don't crash the print path.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

__version__ = "1.0"

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "_dedup_entries.log"

_log = logging.getLogger("kontext.dedup_entries")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text.strip().lower())


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def find_duplicate_groups(db) -> dict[str, list[dict]]:
    """Return {hash: [rows_in_group]} for groups of size >= 2."""
    rows = db.conn.execute(
        "SELECT id, file, fact, source, grade, tier, created_at "
        "FROM entries ORDER BY id ASC"
    ).fetchall()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        h = _hash(r["fact"])
        groups[h].append(dict(r))
    return {h: rs for h, rs in groups.items() if len(rs) > 1}


def _print_samples(dup_groups: dict[str, list[dict]], n: int) -> None:
    items = list(dup_groups.items())[:n]
    if not items:
        return
    print(f"\n--- {min(n, len(items))} sample duplicate groups ---")
    for i, (h, rows) in enumerate(items, 1):
        keeper = rows[0]
        print(f"\n[{i}] hash={h[:12]}...  size={len(rows)}  keep id={keeper['id']} (file={keeper['file']})")
        preview = (keeper["fact"][:110] + "...") if len(keeper["fact"]) > 110 else keeper["fact"]
        print(f"    fact: {preview}")
        for r in rows[1:]:
            print(f"    drop id={r['id']} file={r['file']} created={r['created_at']}")


def apply_dedup(db, dup_groups: dict[str, list[dict]]) -> int:
    """Mark non-oldest rows in each group with superseded_by=<keeper id>.
    Returns count of marked rows."""
    marked = 0
    for rows in dup_groups.values():
        keeper_id = rows[0]["id"]
        for r in rows[1:]:
            db.conn.execute(
                "UPDATE entries SET superseded_by = ? WHERE id = ?",
                (keeper_id, r["id"]),
            )
            marked += 1
    db.conn.commit()
    return marked


def _has_column(db, table: str, column: str) -> bool:
    cols = db.conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == column for c in cols)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dedup_entries",
        description="Find and optionally mark duplicate entries rows.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True,
                       help="Report only, no writes. Default.")
    group.add_argument("--apply", action="store_true",
                       help="Actually mark duplicates via superseded_by.")
    parser.add_argument("--samples", type=int, default=10,
                        help="Number of sample dup groups to print (default: 10).")
    parser.add_argument("--db", default=None,
                        help="Override KONTEXT_DB_PATH for this run.")
    args = parser.parse_args()

    try:
        if args.db:
            import os
            os.environ["KONTEXT_DB_PATH"] = args.db
        from db import KontextDB
        db = KontextDB()
    except Exception as e:
        print(f"ERROR opening DB: {e}", file=sys.stderr)
        _log.error(f"DB_OPEN_FAIL: {e}")
        return 2

    total = db.conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    dup_groups = find_duplicate_groups(db)
    dup_row_count = sum(len(rs) - 1 for rs in dup_groups.values())

    pct = (dup_row_count / total * 100) if total else 0.0
    print(f"Total entries: {total}")
    print(f"Duplicate groups: {len(dup_groups)}")
    print(f"Rows that would be marked superseded: {dup_row_count}  ({pct:.1f}% of total)")

    _print_samples(dup_groups, args.samples)

    if args.apply:
        if not _has_column(db, "entries", "superseded_by"):
            print(
                "\nERROR: entries.superseded_by column missing. "
                "Run Migration 15 first (import db; KontextDB() applies migrations).",
                file=sys.stderr,
            )
            _log.error("MISSING_COLUMN: entries.superseded_by")
            return 2
        marked = apply_dedup(db, dup_groups)
        print(f"\nMarked {marked} rows as superseded.")
        _log.info(f"APPLY dup_groups={len(dup_groups)} marked={marked}")
    else:
        print("\n(Dry-run. Pass --apply to actually mark rows.)")
        _log.info(f"DRY_RUN dup_groups={len(dup_groups)} rows_would_mark={dup_row_count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
