"""
Memory Health Report & Brainstorm/Cleanup Tool

Scans all memory files and generates a health report:
- File sizes and token estimates
- Staleness (when each file was last modified)
- Entry counts per file
- Entries with lowest grades (candidates for archival)
- Historical section sizes vs active section sizes
- Pending conflicts count

Run this before a brainstorm session to see the state of memory.

Usage:
    python brainstorm.py                    # Full health report
    python brainstorm.py --file user_profile.md  # One file only
"""

import os
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path


def find_memory_dir() -> Path:
    """Find the memory directory in Claude Code projects."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        print("ERROR: Claude Code projects not found.", file=sys.stderr)
        sys.exit(1)

    # Find project dirs with a memory/ subfolder — prefer the one with most files
    candidates = []
    for project_dir in claude_dir.iterdir():
        if project_dir.is_dir():
            mem = project_dir / "memory"
            if mem.exists() and (mem / "MEMORY.md").exists():
                file_count = len(list(mem.glob("*.md")))
                candidates.append((mem, file_count))

    if not candidates:
        print("ERROR: No memory directory found with MEMORY.md.", file=sys.stderr)
        sys.exit(1)

    # Return the memory dir with the most files (richest library)
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def analyze_file(filepath: Path) -> dict:
    """Analyze a single memory file."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Parse frontmatter
    name = filepath.stem
    description = ""
    file_type = ""
    in_frontmatter = False
    frontmatter_end = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                frontmatter_end = i + 1
                break
        if in_frontmatter:
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
            elif line.startswith("type:"):
                file_type = line.split(":", 1)[1].strip()

    body = "\n".join(lines[frontmatter_end:])

    # Count sections
    headings = [l for l in lines if l.startswith("## ") or l.startswith("### ")]

    # Find Historical section
    historical_start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{1,3}\s+Historical", line, re.IGNORECASE):
            historical_start = i
            break

    active_content = body if historical_start is None else "\n".join(lines[frontmatter_end:historical_start])
    historical_content = "" if historical_start is None else "\n".join(lines[historical_start:])

    # Last modified
    try:
        mtime = filepath.stat().st_mtime
        last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
        days_since = (datetime.now(timezone.utc) - last_modified).days
    except OSError:
        last_modified = None
        days_since = None

    # Tier analysis: look for date patterns and count entries per section
    date_pattern = re.compile(r"\[(?:20\d{2}(?:-\d{2})?(?:-\d{2})?|undated)\]")
    active_lines = lines[frontmatter_end:historical_start] if historical_start else lines[frontmatter_end:]
    historical_lines = lines[historical_start:] if historical_start else []

    active_entry_count = sum(1 for l in active_lines if l.strip().startswith("- "))
    historical_entry_count = sum(1 for l in historical_lines if l.strip().startswith("- "))

    # Find dated entries and their ages for demotion candidates
    dated_entries_active = []
    for l in active_lines:
        matches = date_pattern.findall(l)
        for m in matches:
            clean = m.strip("[]")
            dated_entries_active.append(clean)

    dated_entries_historical = []
    for l in historical_lines:
        matches = date_pattern.findall(l)
        for m in matches:
            clean = m.strip("[]")
            dated_entries_historical.append(clean)

    # Count entries potentially needing demotion (rough: dates older than 60 days)
    now = datetime.now(timezone.utc)
    demotion_candidates = 0
    for d in dated_entries_active:
        try:
            parts = d.split("-")
            if len(parts) >= 2:
                year, month = int(parts[0]), int(parts[1])
                day = int(parts[2]) if len(parts) >= 3 else 15
                entry_date = datetime(year, month, day, tzinfo=timezone.utc)
                if (now - entry_date).days > 60:
                    demotion_candidates += 1
        except (ValueError, IndexError):
            pass

    compression_candidates = 0
    for d in dated_entries_historical:
        try:
            parts = d.split("-")
            if len(parts) >= 2:
                year, month = int(parts[0]), int(parts[1])
                day = int(parts[2]) if len(parts) >= 3 else 15
                entry_date = datetime(year, month, day, tzinfo=timezone.utc)
                if (now - entry_date).days > 120:
                    compression_candidates += 1
        except (ValueError, IndexError):
            pass

    return {
        "filename": filepath.name,
        "name": name,
        "description": description,
        "type": file_type,
        "total_tokens": estimate_tokens(content),
        "active_tokens": estimate_tokens(active_content),
        "historical_tokens": estimate_tokens(historical_content),
        "sections": len(headings),
        "lines": len(lines),
        "has_historical": historical_start is not None,
        "last_modified": last_modified,
        "days_since_modified": days_since,
        "active_entry_count": active_entry_count,
        "historical_entry_count": historical_entry_count,
        "demotion_candidates": demotion_candidates,
        "compression_candidates": compression_candidates,
    }


def generate_report(memory_dir: Path, target_file: str = None) -> str:
    """Generate the memory health report."""
    lines = []
    lines.append("# Memory Health Report")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Memory directory:** `{memory_dir}`")
    lines.append("")

    # Collect all memory files
    files = sorted(memory_dir.glob("*.md"))
    # Exclude special files
    skip = {"MEMORY.md", "_conflicts.md"}
    files = [f for f in files if f.name not in skip]

    if target_file:
        files = [f for f in files if target_file.lower() in f.name.lower()]
        if not files:
            return f"No memory file matching '{target_file}'"

    analyses = [analyze_file(f) for f in files]

    # Summary stats
    total_tokens = sum(a["total_tokens"] for a in analyses)
    total_active = sum(a["active_tokens"] for a in analyses)
    total_historical = sum(a["historical_tokens"] for a in analyses)
    files_with_historical = sum(1 for a in analyses if a["has_historical"])

    lines.append(f"**Total files:** {len(analyses)}")
    lines.append(f"**Total tokens:** {total_tokens:,} (active: {total_active:,}, historical: {total_historical:,})")
    lines.append(f"**Files with Historical section:** {files_with_historical}")
    # BUG FIX: division by zero when no analysis files found
    avg_tokens = (total_tokens // len(analyses)) if analyses else 0
    lines.append(f"**Loading 6 files costs:** ~{avg_tokens * 6:,} tokens")
    lines.append("")

    # Token budget visualization
    ceiling = 3000
    lines.append("## File Sizes (3,000 token ceiling)")
    lines.append("")
    lines.append("| File | Tokens | Capacity | Status |")
    lines.append("|---|---|---|---|")

    for a in sorted(analyses, key=lambda x: x["total_tokens"], reverse=True):
        pct = (a["total_tokens"] / ceiling) * 100
        bar_len = min(20, int(pct / 5))
        bar = "#" * bar_len + "." * (20 - bar_len)

        if pct > 100:
            status = "OVER CEILING"
        elif pct > 80:
            status = "Near ceiling"
        elif a["days_since_modified"] and a["days_since_modified"] > 30:
            status = f"Stale ({a['days_since_modified']}d)"
        else:
            status = "OK"

        lines.append(f"| `{a['filename']}` | {a['total_tokens']:,} | `{bar}` {pct:.0f}% | {status} |")

    lines.append("")

    # Staleness report
    lines.append("## Freshness")
    lines.append("")
    lines.append("| File | Last Modified | Days Ago |")
    lines.append("|---|---|---|")

    for a in sorted(analyses, key=lambda x: x["days_since_modified"] or 0, reverse=True):
        mod = a["last_modified"].strftime("%Y-%m-%d") if a["last_modified"] else "unknown"
        days = a["days_since_modified"] if a["days_since_modified"] is not None else "?"
        lines.append(f"| `{a['filename']}` | {mod} | {days} |")

    lines.append("")

    # Conflicts check
    conflicts_file = memory_dir / "_conflicts.md"
    if conflicts_file.exists():
        content = conflicts_file.read_text(encoding="utf-8")
        pending = content.count("**Status:** PENDING")
        lines.append(f"## Conflicts: {pending} pending")
    else:
        lines.append("## Conflicts: none")

    lines.append("")

    # Tier Health
    lines.append("## Tier Health")
    lines.append("")
    lines.append("| File | Active Entries | Historical Entries | Demotion Candidates (>60d) | Compression Candidates (>120d) |")
    lines.append("|---|---|---|---|---|")

    total_demotions = 0
    total_compressions = 0
    for a in sorted(analyses, key=lambda x: x["demotion_candidates"] + x["compression_candidates"], reverse=True):
        total_demotions += a["demotion_candidates"]
        total_compressions += a["compression_candidates"]
        if a["demotion_candidates"] > 0 or a["compression_candidates"] > 0 or a["active_entry_count"] > 0:
            lines.append(
                f"| `{a['filename']}` | {a['active_entry_count']} | {a['historical_entry_count']} "
                f"| {a['demotion_candidates']} | {a['compression_candidates']} |"
            )

    lines.append("")
    if total_demotions > 0 or total_compressions > 0:
        lines.append(f"**Summary:** {total_demotions} active entries may need demotion, {total_compressions} historical entries may need compression.")
    else:
        lines.append("**Summary:** All tiers healthy. No demotions or compressions needed.")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")

    over_ceiling = [a for a in analyses if a["total_tokens"] > ceiling]
    if over_ceiling:
        for a in over_ceiling:
            lines.append(f"- `{a['filename']}` is over the 3,000 token ceiling ({a['total_tokens']:,} tokens). Compress lowest-graded entries or move to Historical.")

    stale = [a for a in analyses if a["days_since_modified"] and a["days_since_modified"] > 30]
    if stale:
        for a in stale:
            lines.append(f"- `{a['filename']}` hasn't been updated in {a['days_since_modified']} days. Review for relevance.")

    no_historical = [a for a in analyses if not a["has_historical"] and a["total_tokens"] > 2000]
    if no_historical:
        for a in no_historical:
            lines.append(f"- `{a['filename']}` is large ({a['total_tokens']:,} tokens) with no Historical section. Consider archiving older entries.")

    if not over_ceiling and not stale and not no_historical:
        lines.append("- Memory is healthy. No action needed.")

    lines.append("")
    lines.append("---")
    lines.append("## For Claude (brainstorm mode)")
    lines.append("")
    lines.append("Present this report to the user. Then ask:")
    lines.append("1. Any files that feel outdated? I can move entries to Historical.")
    lines.append("2. Any topics missing that should have their own file?")
    lines.append("3. Any conflicts to resolve?")
    lines.append("4. Want me to compress the largest files?")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Memory health report and brainstorm prep")
    parser.add_argument("--file", type=str, default=None, help="Analyze a specific memory file")
    parser.add_argument("--output", type=str, default=None, help="Write report to file")
    args = parser.parse_args()

    memory_dir = find_memory_dir()
    report = generate_report(memory_dir, args.file)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report: {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
