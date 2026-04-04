"""
extract.py — Golden nugget extractor for Kontext intake.

This is the main script users run. It:
1. Scans intake/ for new files (checks against _processed.json manifest)
2. Parses each file using parsers.py
3. Grades each message using grading.py
4. Chunks using chunker.py
5. Writes chunks to _chunks/ as numbered .md files
6. Writes a _processing-ready flag with metadata

The actual AI processing happens when Claude reads the chunks.
This script is the "dumb extractor" — same pattern as memory-sync.py.

Usage:
    cd Kontext/
    python extract.py

Python 3.10+, stdlib only.
"""

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Local imports
from parsers import detect_and_parse
from chunker import chunk_messages, estimate_tokens
from grading import grade_messages


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
INTAKE_DIR = SCRIPT_DIR / "intake"
CHUNKS_DIR = SCRIPT_DIR / "_chunks"
MANIFEST_PATH = SCRIPT_DIR / "_processed.json"
READY_FLAG = SCRIPT_DIR / "_processing-ready"


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    """Load the processed-files manifest, or return empty structure."""
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # BUG FIX: validate manifest structure — corrupt JSON shouldn't crash pipeline
            if not isinstance(data, dict) or "files" not in data:
                print("[WARN] Manifest has unexpected structure, resetting.")
                return {"files": {}}
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("[WARN] Corrupt manifest file, resetting.")
            return {"files": {}}
    return {"files": {}}


def save_manifest(manifest: dict) -> None:
    """Write the manifest to disk."""
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def file_hash(path: Path) -> str:
    """SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def find_new_files(manifest: dict) -> list[Path]:
    """
    Scan intake/ for files not yet processed (or changed since last processing).
    Skips .gitkeep and hidden files.
    """
    if not INTAKE_DIR.exists():
        # BUG FIX: create intake/ instead of hard exit — more graceful for first run
        print(f"[INFO] intake/ not found, creating {INTAKE_DIR}")
        INTAKE_DIR.mkdir(parents=True, exist_ok=True)
        return []

    new_files = []
    # Walk intake/ recursively — supports subfolders for organization
    # e.g. intake/chatgpt/conversations.json, intake/whatsapp/chat.txt
    supported_extensions = {".json", ".zip", ".txt", ".md", ".pdf"}
    for item in sorted(INTAKE_DIR.rglob("*")):
        # Skip directories, hidden files, .gitkeep
        if item.is_dir() or item.name.startswith(".") or item.name == ".gitkeep":
            continue

        # Skip unsupported file types
        if item.suffix.lower() not in supported_extensions:
            print(f"  [SKIP] {item.relative_to(INTAKE_DIR)} — unsupported format ({item.suffix})")
            continue

        # Use relative path as the manifest key (handles subfolders)
        rel_path = str(item.relative_to(INTAKE_DIR))
        current_hash = file_hash(item)
        recorded = manifest["files"].get(rel_path)

        if recorded and recorded.get("hash") == current_hash:
            print(f"  [SKIP] {rel_path} — already processed, hash matches")
            continue

        if recorded:
            print(f"  [UPDATE] {rel_path} — hash changed, will reprocess")
        else:
            print(f"  [NEW] {rel_path}")

        new_files.append(item)

    return new_files


def clear_chunks() -> None:
    """Remove old chunks before writing new ones."""
    if CHUNKS_DIR.exists():
        shutil.rmtree(CHUNKS_DIR)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)


def write_chunks(chunks: list[dict]) -> None:
    """Write each chunk as a numbered .md file in _chunks/."""
    for chunk in chunks:
        num = chunk["chunk_number"]
        total = chunk["total_chunks"]
        filename = f"chunk_{num:03d}_of_{total:03d}.md"
        filepath = CHUNKS_DIR / filename

        # Build the chunk file with metadata header
        header_lines = [
            f"# Chunk {num} of {total}",
            f"**Source:** {chunk['source_file']}",
        ]

        dr_start = chunk.get("date_range_start")
        dr_end = chunk.get("date_range_end")
        if dr_start and dr_end:
            header_lines.append(f"**Date range:** {dr_start} to {dr_end}")
        elif dr_start:
            header_lines.append(f"**Date:** {dr_start}")

        header_lines.append(f"**Estimated tokens:** {chunk['token_estimate']:,}")
        header_lines.append("")
        header_lines.append("---")
        header_lines.append("")

        content = "\n".join(header_lines) + chunk["text"]

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)


def write_ready_flag(stats: dict) -> None:
    """Write the _processing-ready flag with metadata."""
    with open(READY_FLAG, "w", encoding="utf-8") as f:
        f.write("Kontext intake extraction complete.\n\n")
        f.write(f"Extracted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Files processed: {stats['file_count']}\n")
        f.write(f"Total messages: {stats['total_messages']:,}\n")
        f.write(f"Messages after grading (score >= 5): {stats['graded_messages']:,}\n")
        f.write(f"Total chunks: {stats['chunk_count']}\n")
        f.write(f"Estimated total tokens: {stats['total_tokens']:,}\n")
        f.write("\nChunks are in _chunks/. Say 'process intake' in Claude Code to synthesize.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Kontext Intake Extractor")
    print("=" * 60)
    print()

    # Load manifest
    manifest = load_manifest()
    print(f"[1/5] Scanning intake/ for new files...")
    new_files = find_new_files(manifest)

    if not new_files:
        print("\nNo new files to process. Done.")
        return

    print(f"\n[2/5] Parsing {len(new_files)} file(s)...")

    all_chunks: list[dict] = []
    total_messages = 0
    graded_messages = 0

    for filepath in new_files:
        rel_path = str(filepath.relative_to(INTAKE_DIR))
        print(f"\n  Parsing: {rel_path}")

        # Parse
        messages = detect_and_parse(filepath)
        print(f"    Messages extracted: {len(messages):,}")

        if not messages:
            print(f"    [WARN] No messages extracted from {rel_path}")
            continue

        # Grade
        print(f"  [3/5] Grading messages...")
        messages = grade_messages(messages)

        # Filter: only keep messages with grade >= 5
        high_value = [m for m in messages if m.get("grade", 0) >= 5]
        print(f"    High-value messages (grade >= 5): {len(high_value):,}")
        print(f"    Filtered out: {len(messages) - len(high_value):,}")

        total_messages += len(messages)
        graded_messages += len(high_value)

        # Chunk the high-value messages
        print(f"  [4/5] Chunking...")
        chunks = chunk_messages(high_value, source_file=rel_path)
        print(f"    Chunks created: {len(chunks)}")

        all_chunks.extend(chunks)

        # Update manifest
        manifest["files"][rel_path] = {
            "hash": file_hash(filepath),
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "chunks": len(chunks),
            "messages": len(messages),
        }

    # Re-number chunks sequentially across all files
    for i, chunk in enumerate(all_chunks, 1):
        chunk["chunk_number"] = i
        chunk["total_chunks"] = len(all_chunks)

    # Write everything
    print(f"\n[5/5] Writing {len(all_chunks)} chunks to _chunks/...")
    clear_chunks()
    write_chunks(all_chunks)

    # Save manifest
    save_manifest(manifest)

    # Calculate total tokens
    total_tokens = sum(c["token_estimate"] for c in all_chunks)

    # Write ready flag
    stats = {
        "file_count": len(new_files),
        "total_messages": total_messages,
        "graded_messages": graded_messages,
        "chunk_count": len(all_chunks),
        "total_tokens": total_tokens,
    }
    write_ready_flag(stats)

    # Summary
    print()
    print("=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Files processed:    {stats['file_count']}")
    print(f"  Total messages:     {stats['total_messages']:,}")
    print(f"  High-value kept:    {stats['graded_messages']:,}")
    print(f"  Chunks written:     {stats['chunk_count']}")
    print(f"  Estimated tokens:   {stats['total_tokens']:,}")
    print()
    print("Next step: say 'process intake' in Claude Code to synthesize.")


if __name__ == "__main__":
    main()
