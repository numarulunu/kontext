# dream.py
"""
Kontext Dream — automated memory consolidation.

Runs periodically to clean, deduplicate, normalize, and compress
the memory database. Modeled after the brain's consolidation during sleep.

Phases:
  1. Dedup     — merge near-duplicate entries (same file, similar fact)
  2. Normalize — convert relative dates to absolute, clean formatting
  3. Resolve   — auto-resolve stale conflicts (>7 days, same file, one is newer)
  4. Compress  — summarize cold-tier entries into compact one-liners
  5. Purge     — delete entries with grade <= 1 that haven't been accessed in 120+ days

Usage:
    python dream.py              # Full consolidation
    python dream.py --dry-run    # Report what would change, don't modify
    python dream.py --phase dedup  # Run a single phase
"""

import sys
import os
import re
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher

from db import KontextDB

__version__ = "1.0"

LOG = Path(__file__).parent / "_dream.log"
LOCKFILE = Path(__file__).parent / "_dream.lock"

import logging.handlers
_log = logging.getLogger("kontext.dream")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(LOG), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def similarity(a: str, b: str) -> float:
    """Ratio of similarity between two strings (0.0 to 1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def days_since(iso_ts: str) -> int:
    # Null timestamp = treat as just-accessed. Returning a huge number here was
    # causing phase_purge to delete entries that had never been touched since
    # insertion (e.g. bulk-imported rows with no last_accessed).
    if not iso_ts:
        return 0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def extract_source_date(source: str) -> str | None:
    """Extract YYYY-MM from source tags like '[Claude 2026-04]'."""
    m = re.search(r"(\d{4}-\d{2})", source or "")
    return m.group(1) if m else None


def acquire_lock() -> bool:
    """Prevent concurrent dream runs."""
    if LOCKFILE.exists():
        age = days_since(LOCKFILE.read_text(encoding="utf-8").strip())
        if age < 1:
            return False
        # Stale lock — remove it
    LOCKFILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return True


def release_lock():
    try:
        LOCKFILE.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Phase 1: Dedup
# ---------------------------------------------------------------------------

def _prefix_bucket(fact: str, prefix_len: int = 20) -> str:
    """Generate a normalized prefix for bucketing similar entries."""
    return fact.lower().strip()[:prefix_len]


def phase_dedup(db: KontextDB, dry_run: bool = False) -> dict:
    """Merge near-duplicate entries within the same file.

    Two entries are considered duplicates if:
    - Same file
    - Similarity ratio >= 0.85
    - Keep the one with higher grade (or newer if tied)

    Optimization: bucket entries by normalized prefix to reduce O(n^2)
    comparisons. Only entries with overlapping prefix buckets are compared.
    """
    stats = {"pairs_found": 0, "merged": 0}
    files = db.list_files()

    for filename in files:
        entries = db.get_entries(file=filename)
        if len(entries) < 2:
            continue

        # Bucket by prefix — entries that start very differently can't be 85% similar
        # unless they're short. For entries < 25 chars, use full text as bucket key.
        buckets = {}
        for e in entries:
            key = _prefix_bucket(e["fact"])
            buckets.setdefault(key, []).append(e)

        # Compare within buckets (fast path) + cross-bucket for short entries
        merged_ids = set()
        compared = set()

        # Within-bucket comparisons (most duplicates live here)
        for bucket_entries in buckets.values():
            for i, e1 in enumerate(bucket_entries):
                if e1["id"] in merged_ids:
                    continue
                for e2 in bucket_entries[i + 1:]:
                    if e2["id"] in merged_ids:
                        continue
                    pair_key = (min(e1["id"], e2["id"]), max(e1["id"], e2["id"]))
                    if pair_key in compared:
                        continue
                    compared.add(pair_key)
                    sim = similarity(e1["fact"], e2["fact"])
                    if sim >= 0.85:
                        stats["pairs_found"] += 1
                        if e1["grade"] > e2["grade"]:
                            keep, drop = e1, e2
                        elif e2["grade"] > e1["grade"]:
                            keep, drop = e2, e1
                        elif (e1.get("updated_at") or "") >= (e2.get("updated_at") or ""):
                            keep, drop = e1, e2
                        else:
                            keep, drop = e2, e1

                        _log.info(f"DEDUP: keep #{keep['id']} ({sim:.0%} similar), drop #{drop['id']} in {filename}")
                        _log.info(f"  KEEP: {keep['fact'][:80]}")
                        _log.info(f"  DROP: {drop['fact'][:80]}")

                        if not dry_run:
                            db.delete_entry(drop["id"])
                            merged_ids.add(drop["id"])
                            stats["merged"] += 1

        # Cross-bucket for short entries (< 30 chars can match across prefixes).
        # Rebuild *after* the within-bucket loop so merged entries are already excluded.
        short_entries = [e for e in entries if len(e["fact"]) < 30 and e["id"] not in merged_ids]
        for i, e1 in enumerate(short_entries):
            # NOTE: no `if e1["id"] in merged_ids` guard — short_entries was just
            # rebuilt with the up-to-date merged_ids, so e1 cannot already be merged.
            for e2 in short_entries[i + 1:]:
                if e2["id"] in merged_ids:
                    continue
                pair_key = (min(e1["id"], e2["id"]), max(e1["id"], e2["id"]))
                if pair_key in compared:
                    continue
                compared.add(pair_key)
                sim = similarity(e1["fact"], e2["fact"])
                if sim >= 0.85:
                    stats["pairs_found"] += 1
                    if e1["grade"] > e2["grade"]:
                        keep, drop = e1, e2
                    elif e2["grade"] > e1["grade"]:
                        keep, drop = e2, e1
                    elif (e1.get("updated_at") or "") >= (e2.get("updated_at") or ""):
                        keep, drop = e1, e2
                    else:
                        keep, drop = e2, e1

                    _log.info(f"DEDUP: keep #{keep['id']} ({sim:.0%} similar), drop #{drop['id']} in {filename}")
                    if not dry_run:
                        db.delete_entry(drop["id"])
                        merged_ids.add(drop["id"])
                        stats["merged"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 2: Normalize
# ---------------------------------------------------------------------------

_RELATIVE_DATE_PATTERNS = [
    (re.compile(r"\blast\s+month\b", re.I), "relative date: last month"),
    (re.compile(r"\blast\s+week\b", re.I), "relative date: last week"),
    (re.compile(r"\byesterday\b", re.I), "relative date: yesterday"),
    (re.compile(r"\btoday\b", re.I), "relative date: today"),
    (re.compile(r"\btomorrow\b", re.I), "relative date: tomorrow"),
    (re.compile(r"\bnext\s+month\b", re.I), "relative date: next month"),
    (re.compile(r"\bnext\s+week\b", re.I), "relative date: next week"),
    (re.compile(r"\brecently\b", re.I), "relative date: recently"),
    (re.compile(r"\ba\s+few\s+days\s+ago\b", re.I), "relative date: a few days ago"),
]


def phase_normalize(db: KontextDB, dry_run: bool = False) -> dict:
    """Flag entries with relative dates that should be absolute.

    Doesn't rewrite facts (that would need LLM judgment), but appends
    a source-date anchor so the relative reference has a fixed point.
    """
    stats = {"flagged": 0, "anchored": 0}
    entries = db.get_entries()

    for e in entries:
        fact = e["fact"]
        source = e.get("source", "")
        source_date = extract_source_date(source)

        for pattern, label in _RELATIVE_DATE_PATTERNS:
            if pattern.search(fact):
                stats["flagged"] += 1
                # If we have a source date, append anchor context
                if source_date and "(as of " not in fact:
                    anchored = f"{fact} (as of {source_date})"
                    _log.info(f"NORMALIZE: #{e['id']} {label} → anchored to {source_date}")
                    if not dry_run:
                        db.update_entry(e["id"], fact=anchored)
                        stats["anchored"] += 1
                break  # One anchor per entry

    return stats


# ---------------------------------------------------------------------------
# Phase 3: Auto-resolve stale conflicts
# ---------------------------------------------------------------------------

def phase_resolve(db: KontextDB, dry_run: bool = False) -> dict:
    """Auto-resolve conflicts that are >7 days old.

    Resolution logic:
    - If one entry has a newer source date, keep the newer one
    - If one entry has a higher grade, keep the higher-grade one
    - Otherwise, leave for manual review
    """
    stats = {"pending": 0, "auto_resolved": 0, "skipped": 0}
    conflicts = db.get_pending_conflicts()
    stats["pending"] = len(conflicts)

    for c in conflicts:
        age = days_since(c.get("created_at", ""))
        if age < 7:
            stats["skipped"] += 1
            continue

        # Exact-match lookup by (file, fact). Previously this used a LIKE-based
        # search_entries() with the first 50 chars as the pattern — unsafe for facts
        # containing %/_ and wasteful (O(n) post-filter in Python).
        entry_a = db.get_entry_by_fact(c["file"], c["entry_a"])
        entry_b = db.get_entry_by_fact(c["file"], c["entry_b"])

        if not entry_a or not entry_b:
            # One side was already deleted — auto-resolve
            resolution = "Auto-resolved: one entry no longer exists"
            _log.info(f"RESOLVE: conflict #{c['id']} — entry missing, resolving")
            if not dry_run:
                db.resolve_conflict(c["id"], resolution)
            stats["auto_resolved"] += 1
            continue

        date_a = extract_source_date(entry_a.get("source", ""))
        date_b = extract_source_date(entry_b.get("source", ""))

        # Newer source date wins
        if date_a and date_b and date_a != date_b:
            if date_a > date_b:
                winner, loser = entry_a, entry_b
            else:
                winner, loser = entry_b, entry_a
            resolution = f"Auto-resolved: kept newer entry (source {extract_source_date(winner.get('source', ''))}), demoted older"
            _log.info(f"RESOLVE: conflict #{c['id']} — newer date wins")
            if not dry_run:
                db.resolve_conflict(c["id"], resolution)
                db.update_entry(loser["id"], tier="historical")
            stats["auto_resolved"] += 1
            continue

        # Higher grade wins
        if abs(entry_a["grade"] - entry_b["grade"]) >= 2:
            if entry_a["grade"] > entry_b["grade"]:
                winner, loser = entry_a, entry_b
            else:
                winner, loser = entry_b, entry_a
            resolution = f"Auto-resolved: kept higher-grade entry (grade {winner['grade']}), demoted lower"
            _log.info(f"RESOLVE: conflict #{c['id']} — higher grade wins")
            if not dry_run:
                db.resolve_conflict(c["id"], resolution)
                db.update_entry(loser["id"], tier="historical")
            stats["auto_resolved"] += 1
            continue

        stats["skipped"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 4: Compress cold entries
# ---------------------------------------------------------------------------

def phase_compress(db: KontextDB, dry_run: bool = False) -> dict:
    """Trim cold-tier entries to compact one-liners.

    Rules:
    - Only touch cold-tier entries
    - Strip source tags from fact text (keep in source field)
    - Truncate to 120 chars if longer
    - Remove parenthetical asides
    """
    stats = {"candidates": 0, "compressed": 0}
    cold_entries = db.get_entries(tier="cold")
    stats["candidates"] = len(cold_entries)

    # Only strip parentheticals that look like boilerplate (source tags, "see also",
    # "formerly", "as of"). Previously this stripped *any* parenthetical >=20 chars,
    # which destroyed meaningful numeric/historical context.
    paren_re = re.compile(
        r"\s*\((?:see also|formerly|as of|previously|source:|ref:)[^)]{0,200}\)",
        re.IGNORECASE,
    )

    for e in cold_entries:
        fact = e["fact"]
        original = fact

        # Strip inline source tags like [Claude 2026-04]
        fact = re.sub(r"\[Claude \d{4}-\d{2}\]\s*", "", fact).strip()
        # Remove long parentheticals
        fact = paren_re.sub("", fact).strip()
        # Truncate with ellipsis
        if len(fact) > 120:
            fact = fact[:117].rsplit(" ", 1)[0] + "..."

        if fact != original:
            _log.info(f"COMPRESS: #{e['id']} {len(original)}→{len(fact)} chars")
            if not dry_run:
                db.update_entry(e["id"], fact=fact)
                stats["compressed"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 5: Purge dead entries
# ---------------------------------------------------------------------------

def phase_purge(db: KontextDB, dry_run: bool = False) -> dict:
    """Delete entries with grade <= 1 that haven't been accessed in 120+ days."""
    stats = {"candidates": 0, "purged": 0}
    # Push grade + last_accessed filters into SQL — full table fetch was wasteful
    # at scale (most entries are grade > 1 and never qualify).
    rows = db.conn.execute("""
        SELECT id, grade, fact, last_accessed FROM entries
        WHERE grade <= 1 AND last_accessed < datetime('now', '-120 days')
    """).fetchall()
    entries = [dict(r) for r in rows]

    for e in entries:
        if e["grade"] <= 1 and days_since(e.get("last_accessed", "")) >= 120:
            stats["candidates"] += 1
            _log.info(f"PURGE: #{e['id']} grade={e['grade']} last_accessed={e.get('last_accessed', 'never')} — {e['fact'][:60]}")
            if not dry_run:
                db.delete_entry(e["id"])
                stats["purged"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 6: SCAR auto-promotion (self-improvement loop v0.1)
# ---------------------------------------------------------------------------

_SCAR_LINE_RE = re.compile(
    r"^\[(?P<source>[^\]]+)\]\s+SCAR:\s*(?P<text>.*?)(?:\.\s*Grade:\s*(?P<grade>\d+))?\s*$"
)
_DATE_IN_SOURCE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_scar_entries(path: Path) -> list[dict]:
    """Parse SCAR entries from a project_*_log.md file.

    Returns list of dicts with keys: source, date, text, grade, file, line.
    Lines that don't match the SCAR pattern are skipped (ARCH/EVO/OPEN/PERF are ignored).
    """
    entries: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return entries

    for lineno, line in enumerate(raw.splitlines(), start=1):
        m = _SCAR_LINE_RE.match(line.strip())
        if not m:
            continue
        source = m.group("source").strip()
        text = m.group("text").strip().rstrip(".")
        grade_s = m.group("grade")
        grade = int(grade_s) if grade_s else 0
        dm = _DATE_IN_SOURCE_RE.search(source)
        date = dm.group(1) if dm else ""
        entries.append({
            "source": source,
            "date": date,
            "text": text,
            "grade": grade,
            "file": str(path),
            "line": lineno,
        })
    return entries


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

PHASES = {
    "dedup": phase_dedup,
    "normalize": phase_normalize,
    "resolve": phase_resolve,
    "compress": phase_compress,
    "purge": phase_purge,
}


def dream(db: KontextDB, dry_run: bool = False, phase: str = None) -> dict:
    """Run the full dream cycle (or a single phase).

    Wraps the full phase loop in a single transaction so a crash mid-phase
    rolls back everything the earlier phases committed. Dry-run mode stays
    outside any transaction since no writes happen.
    """
    results = {}

    if phase:
        if phase not in PHASES:
            print(f"ERROR: Unknown phase '{phase}'. Options: {', '.join(PHASES)}")
            return results
        _log.info(f"DREAM START phase={phase} dry_run={dry_run}")
        if dry_run:
            results[phase] = PHASES[phase](db, dry_run)
        else:
            with db.transaction():
                results[phase] = PHASES[phase](db, dry_run)
    else:
        _log.info(f"DREAM START full dry_run={dry_run}")
        if dry_run:
            for name, fn in PHASES.items():
                results[name] = fn(db, dry_run)
        else:
            with db.transaction():
                for name, fn in PHASES.items():
                    results[name] = fn(db, dry_run)

    _log.info(f"DREAM END results={results}")
    return results


def print_report(results: dict, dry_run: bool):
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"{'=' * 50}")
    print(f" KONTEXT DREAM REPORT ({mode})")
    print(f"{'=' * 50}")

    for phase_name, stats in results.items():
        print(f"\n [{phase_name.upper()}]")
        for key, value in stats.items():
            print(f"   {key}: {value}")

    total_actions = sum(
        v for stats in results.values() for k, v in stats.items()
        if k in ("merged", "anchored", "auto_resolved", "compressed", "purged")
    )
    print(f"\n Total actions: {total_actions}")
    print(f"{'=' * 50}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Kontext Dream — memory consolidation")
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't modify")
    parser.add_argument("--phase", type=str, choices=list(PHASES.keys()), help="Run a single phase")
    args = parser.parse_args()

    if not acquire_lock():
        print("Dream is already running (lock file exists). Skipping.")
        return 0

    try:
        db = KontextDB()
        results = dream(db, dry_run=args.dry_run, phase=args.phase)
        print_report(results, args.dry_run)

        # Re-export affected files after modifications
        if not args.dry_run and any(
            v for stats in results.values() for k, v in stats.items()
            if k in ("merged", "anchored", "auto_resolved", "compressed", "purged") and v > 0
        ):
            from export import export_all, export_memory_index
            from mcp_server import find_memory_dir
            mem_dir = find_memory_dir()
            if mem_dir:
                export_all(db, mem_dir)
                export_memory_index(db, mem_dir)
                print(f" Exported updated files to {mem_dir}")

        db.close()
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
