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

# File metadata (type and description) — stored in db eventually, hardcoded for now
_FILE_META = {
    "user_identity.md": ("user", "Core bio — name, age, location, tech, business"),
    "user_psychology.md": ("user", "How their mind works, patterns, trauma, coping"),
    "user_strengths.md": ("user", "Verified strengths and capabilities"),
    "user_blind_spots.md": ("user", "Patterns that cost time and quality"),
    "project_goals.md": ("project", "Active projects, statuses, benchmarks"),
    "user_vocal_expertise.md": ("user", "Singing, teaching, method, curriculum"),
    "project_vocality_brand.md": ("project", "Brand identity, aesthetic, messaging"),
    "project_vocality_content.md": ("project", "Skool, YouTube, content pipeline"),
    "user_financial_architecture.md": ("user", "Taxes, PFA, accounting, investments"),
    "user_health_protocols.md": ("user", "Sleep, training, diet, skin, nervous system"),
    "user_influences.md": ("user", "Mental models, copywriting, influences"),
    "user_behavioral_intelligence.md": ("user", "Decision patterns, validation, drivers"),
    "user_mother_situation.md": ("user", "No-contact, history, boundaries"),
    "user_luiza_dynamic.md": ("user", "Relationship, attachment, friction"),
    "user_peer_dynamic.md": ("user", "Friends, group dynamics"),
    "feedback_ai_interaction.md": ("feedback", "Communication rules, tone, protocols"),
    "design_principles.md": ("reference", "Build rules, patterns, feature ideas"),
    "tool_registry.md": ("project", "All tools with version, location, status"),
    "feedback_conflict_patterns.md": ("feedback", "Learned conflict resolution patterns"),
}


def export_file(db: KontextDB, filename: str) -> str:
    """Generate markdown content for a single file from database entries."""
    file_type, description = _FILE_META.get(filename, ("user", ""))
    name = filename.replace(".md", "").replace("_", " ").title()

    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"type: {file_type}",
        "---",
        "",
    ]

    active = db.get_entries(file=filename, tier="active")
    historical = db.get_entries(file=filename, tier="historical")
    cold = db.get_entries(file=filename, tier="cold")

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


def export_memory_index(db: KontextDB, output_dir: Path):
    """Generate MEMORY.md index from database."""
    files = db.list_files()
    lines = ["# Memory Index", ""]

    for filename in sorted(files.keys()):
        _, description = _FILE_META.get(filename, ("", filename))
        count = files[filename]
        name = filename.replace(".md", "").replace("_", " ").title()
        lines.append(f"- [{name}]({filename}) — {description} ({count} entries)")

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
    files = db.list_files()
    print(f"Exported {len(files)} files to {output_dir}")
    db.close()


if __name__ == "__main__":
    main()
