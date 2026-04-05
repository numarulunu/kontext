# migrate.py
"""
Migrate flat markdown memory files into SQLite database.

Parses each .md file in the memory directory, extracts entries with
source tags, grades, and tier (active/historical), and inserts them
into the Kontext database.

Usage:
    python migrate.py                     # Migrate from auto-detected memory dir
    python migrate.py --memory-dir /path  # Explicit path
    python migrate.py --dry-run           # Parse and report, don't write
"""

import re
import sys
import argparse
from pathlib import Path
from db import KontextDB


# Pattern: [Source YYYY-MM] Fact text. Grade: N
_ENTRY_PATTERN = re.compile(
    r"^\[([^\]]+)\]\s+(.+?)(?:\s+Grade:\s*(\d+(?:\.\d+)?))?$"
)

# Section headers that indicate tier
_ACTIVE_HEADERS = {"active", "active (grade 8-10)", "active projects", "from digests", "from chunks"}
_HISTORICAL_HEADERS = {"historical", "historical (grade 5-7)"}


def parse_memory_file(filepath: Path) -> list[dict]:
    """Parse a single memory file into a list of entry dicts."""
    content = filepath.read_text(encoding="utf-8")

    # Strip YAML frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            content = content[end + 3:]

    entries = []
    current_tier = "active"  # Default tier

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Detect section headers
        if stripped.startswith("##"):
            header_text = stripped.lstrip("#").strip().lower()
            # Remove formatting like (Grade 8-10)
            header_clean = re.sub(r"\(.*?\)", "", header_text).strip()
            if any(h in header_clean for h in _ACTIVE_HEADERS):
                current_tier = "active"
            elif any(h in header_clean for h in _HISTORICAL_HEADERS):
                current_tier = "historical"
            continue

        # Skip non-entry lines (headers, sub-headers, blank formatting)
        if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("|"):
            continue

        # Try to parse as an entry with [Source] prefix
        match = _ENTRY_PATTERN.match(stripped)
        if match:
            source = f"[{match.group(1)}]"
            fact = match.group(2).strip()
            grade = float(match.group(3)) if match.group(3) else (8 if current_tier == "active" else 6)
            entries.append({
                "fact": fact,
                "source": source,
                "grade": grade,
                "tier": current_tier,
            })
            continue

        # Lines starting with - that contain content (bullet entries without source tags)
        if stripped.startswith("- ") and len(stripped) > 10:
            fact = stripped[2:].strip()
            # Check for inline grade
            grade_match = re.search(r"Grade:\s*(\d+)", fact)
            grade = float(grade_match.group(1)) if grade_match else (8 if current_tier == "active" else 6)
            fact = re.sub(r"\s*Grade:\s*\d+\s*$", "", fact).strip()
            if fact:
                entries.append({
                    "fact": fact,
                    "source": "",
                    "grade": grade,
                    "tier": current_tier,
                })

    return entries


def migrate_all(memory_dir: Path, db: KontextDB) -> int:
    """Migrate all .md files in memory_dir into the database. Returns total entries added."""
    total = 0

    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "MEMORY.md" or md_file.name.startswith("_"):
            continue

        entries = parse_memory_file(md_file)
        for entry in entries:
            db.add_entry(
                file=md_file.name,
                fact=entry["fact"],
                source=entry["source"],
                grade=entry["grade"],
                tier=entry["tier"],
            )
            total += 1

    return total


def main():
    parser = argparse.ArgumentParser(description="Migrate flat memory files to SQLite")
    parser.add_argument("--memory-dir", type=str, help="Path to memory directory")
    parser.add_argument("--db", type=str, default=None, help="Database path (default: kontext.db)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, don't write")
    args = parser.parse_args()

    if args.memory_dir:
        memory_dir = Path(args.memory_dir)
    else:
        # Auto-detect
        from mcp_server import find_memory_dir
        memory_dir = find_memory_dir()
        if not memory_dir:
            print("ERROR: No memory directory found.", file=sys.stderr)
            sys.exit(1)

    print(f"Memory dir: {memory_dir}")

    if args.dry_run:
        total = 0
        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md" or md_file.name.startswith("_"):
                continue
            entries = parse_memory_file(md_file)
            print(f"  {md_file.name}: {len(entries)} entries")
            total += len(entries)
        print(f"\nTotal: {total} entries (dry run, nothing written)")
        return

    db = KontextDB(args.db)
    count = migrate_all(memory_dir, db)
    print(f"Migrated {count} entries into {db.db_path}")
    db.close()


if __name__ == "__main__":
    main()
