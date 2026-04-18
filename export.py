# export.py
"""
Export Kontext database to flat markdown files.

Generates backward-compatible memory files that Claude can read via
the existing MEMORY.md + file loading system. The database is the
source of truth; these files are regenerated on every export.

Usage:
    python export.py                        # Export to auto-detected memory dir
    python export.py --output /path/to/dir  # Explicit output
"""

import sys
import argparse
from pathlib import Path
from db import KontextDB

# File metadata fallback — primary source is file_meta table in database, this dict is the fallback.
# Sane defaults for common memory categories. Add your own by calling db.set_file_meta(...).
_FILE_META = {
    "user_identity.md": ("user", "Core bio — name, location, role"),
    "user_psychology.md": ("user", "How the user thinks, patterns, coping"),
    "user_strengths.md": ("user", "Verified strengths and capabilities"),
    "user_blind_spots.md": ("user", "Patterns that cost time and quality"),
    "project_goals.md": ("project", "Active projects, statuses, benchmarks"),
    "feedback_ai_interaction.md": ("feedback", "Communication rules, tone, protocols"),
    "design_principles.md": ("reference", "Build rules, patterns, feature ideas"),
    "tool_registry.md": ("project", "Tools with version, location, status"),
}


def export_file(db: KontextDB, filename: str) -> str:
    """Generate markdown content for a single file from database entries."""
    meta = db.get_file_meta(filename)
    file_type = meta["file_type"]
    description = meta["description"]
    # Fallback to hardcoded meta if DB has no description
    if not description and filename in _FILE_META:
        file_type, description = _FILE_META[filename]
    name = filename.replace(".md", "").replace("_", " ").title()

    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"type: {file_type}",
        "---",
        "",
    ]

    # One query, split in Python — avoids 3 separate SQL round-trips per file
    # (which compounded in export_all to 3×N queries across all memory files).
    all_rows = db.get_entries(file=filename)
    active, historical, cold = [], [], []
    for e in all_rows:
        if e["tier"] == "active":
            active.append(e)
        elif e["tier"] == "historical":
            historical.append(e)
        elif e["tier"] == "cold":
            cold.append(e)

    if active:
        lines.append("## Active (Grade 8-10)")
        lines.append("")
        for e in active:
            grade_str = f" Grade: {int(e['grade'])}" if e['grade'] else ""
            source = e['source'] + " " if e['source'] else ""
            lines.append(f"{source}{e['fact']}{grade_str}")
        lines.append("")

    if historical:
        lines.append("## Historical (Grade 5-7)")
        lines.append("")
        for e in historical:
            grade_str = f" Grade: {int(e['grade'])}" if e['grade'] else ""
            source = e['source'] + " " if e['source'] else ""
            lines.append(f"{source}{e['fact']}{grade_str}")
        lines.append("")

    if cold:
        lines.append("## Cold (Compressed)")
        lines.append("")
        for e in cold:
            source = e['source'] + " " if e['source'] else ""
            lines.append(f"- {source}{e['fact']}")
        lines.append("")

    return "\n".join(lines)


def export_all(db: KontextDB, output_dir: Path):
    """Export all files from database to markdown."""
    files = db.list_files()
    for filename in files:
        content = export_file(db, filename)
        (output_dir / filename).write_text(content, encoding="utf-8")


_CORE_VOCAB = [
    "Vocality", "Melocchi", "Vázquez", "PFA", "ANAF", "Stripe", "Preply",
    "Skool", "Medical Noir", "Void Strategy", "Luiza", "Palazu",
    "Claude Max x20", "Kontext", "Mastermind", "SMAC", "Hetzner",
    "Coolify", "Pangolin", "Minio", "n8n",
]


def _top_entries(db: KontextDB, file: str, min_grade: int, limit: int) -> list[dict]:
    """Return active, non-superseded entries from `file` at or above min_grade,
    sorted by grade desc. Best-effort — missing files return []."""
    rows = db.get_entries(file=file, tier="active", min_grade=min_grade)
    out = []
    for r in rows:
        if r.get("superseded_by"):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def compile_user_core(db: KontextDB, output_dir: Path) -> None:
    """Dynamic compile of user_core.md from highest-grade identity entries.

    Regenerated on every export. Dynamic by construction — as grades shift,
    entries decay, or new facts land via kontext_write, the next export
    refreshes the core. No hand editing.
    """
    identity   = _top_entries(db, "user_identity.md", min_grade=10, limit=12)
    projects   = _top_entries(db, "project_goals.md", min_grade=9, limit=20)
    psych      = _top_entries(db, "user_psychology.md", min_grade=9, limit=12)
    blind      = _top_entries(db, "user_blind_spots.md", min_grade=9, limit=12)
    comms      = _top_entries(db, "feedback_ai_interaction.md", min_grade=10, limit=10)
    strengths  = _top_entries(db, "user_strengths.md", min_grade=8, limit=5)

    def _dedup(entries: list[dict]) -> list[dict]:
        # Projects + patterns get listed twice in source files when the
        # same fact was captured from different conversations. Keep the
        # highest-grade instance only.
        seen: set[str] = set()
        out: list[dict] = []
        for e in entries:
            key = " ".join(e["fact"].lower().split())[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    projects = _dedup(projects)[:10]
    patterns = _dedup(sorted(psych + blind, key=lambda e: -float(e["grade"])))[:8]

    def section(title: str, entries: list[dict]) -> list[str]:
        if not entries:
            return []
        out = [f"## {title}", ""]
        for e in entries:
            out.append(f"- {e['fact']}")
        out.append("")
        return out

    lines = [
        "---",
        "name: User Core",
        "description: Always-on identity block — compiled from database on every export. "
        "Describes who you're talking to, not how to style answers. Never cite, never "
        "frame responses around it, never explain his own patterns back to him.",
        "type: user",
        "---",
        "",
    ]
    lines += section("Identity", identity)
    lines += section("What he's building", projects)
    lines += section("Load-bearing patterns", patterns)
    lines += section("Strengths", strengths)
    lines += section("Communication", comms)
    lines += ["## Vocabulary — never define", "", ", ".join(_CORE_VOCAB), ""]

    (output_dir / "user_core.md").write_text("\n".join(lines), encoding="utf-8")


def export_memory_index(db: KontextDB, output_dir: Path):
    """Generate MEMORY.md index from database.

    Static shape: title + description only. Entry counts were previously
    appended ("(83 entries)") but that made the file change on every
    write, invalidating the MEMORY.md-containing prompt cache. Counts are
    available via db.list_files() if needed for diagnostics.
    """
    files = db.list_files()
    lines = ["# Memory Index", ""]

    for filename in sorted(files.keys()):
        # DB file_meta is primary; hardcoded _FILE_META is fallback only.
        meta = db.get_file_meta(filename)
        description = meta["description"]
        if not description:
            description = _FILE_META.get(filename, ("", filename))[1]
        name = filename.replace(".md", "").replace("_", " ").title()
        lines.append(f"- [{name}]({filename}) — {description}")

    lines.append("")
    (output_dir / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Export Kontext database to markdown files")
    parser.add_argument("--db", type=str, default=None, help="Database path")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    db = KontextDB(args.db)

    if args.output:
        output_dir = Path(args.output)
    else:
        from mcp_server import find_memory_dir
        output_dir = find_memory_dir()
        if not output_dir:
            print("ERROR: No memory directory found.", file=sys.stderr)
            sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    export_all(db, output_dir)
    export_memory_index(db, output_dir)
    compile_user_core(db, output_dir)
    files = db.list_files()
    print(f"Exported {len(files)} files to {output_dir}")
    db.close()


if __name__ == "__main__":
    main()
