"""
ai_grader.py - Haiku-powered chunk grading for Kontext intake.

Replaces regex-based grading with AI judgment. Haiku reads each chunk
and scores it 1-10 for personal signal value:
  8-10: High signal - decisions, identity, preferences, relationships
  5-7:  Medium signal - useful context, soft patterns
  1-4:  Low signal - noise, debugging, pleasantries

Chunks scoring 5+ proceed to Sonnet extraction.
Chunks scoring 1-4 are skipped.

Usage:
    python ai_grader.py                 # Grade all chunks in _chunks/
    python ai_grader.py --threshold 6   # Only pass chunks scoring 6+
    python ai_grader.py --dry-run       # Show scores without writing manifest

Output: _chunks/_graded.json with chunk scores and pass/skip status.

Python 3.10+, requires `claude` CLI on PATH.
"""

import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

__version__ = "1.0"

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
CHUNKS_DIR = PROJECT_DIR / "_chunks"
GRADED_MANIFEST = CHUNKS_DIR / "_graded.json"
LOG_FILE = PROJECT_DIR / "_ai_grader.log"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_THRESHOLD = 5
MAX_WORKERS = 4  # Parallel Haiku calls

# Logging setup
_log = logging.getLogger("kontext.ai_grader")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)

# Grading prompt for Haiku
_GRADE_PROMPT = """Score this conversation chunk for PERSONAL SIGNAL value.

You're grading content from someone's chat history (ChatGPT, Gemini, WhatsApp).
Your job: identify chunks worth extracting memories from.

HIGH SIGNAL (8-10):
- Decisions ("I decided to...", "going with X", "final choice")
- Identity facts (profession, skills, where they live, relationships)
- Strong preferences ("I hate...", "always do X", "never do Y")
- Financial data (income, rates, costs, business metrics)
- Relationship dynamics (who they talk to, how, conflicts, closeness)
- Emotional processing with conclusions
- AI interaction feedback ("don't ask me...", "stop doing X")

MEDIUM SIGNAL (5-7):
- Soft preferences mentioned once
- Project context without clear outcomes
- Historical background
- Emotional venting without resolution

LOW SIGNAL (1-4):
- Debugging sessions, code dumps, error logs
- "Hi" / "Thanks" / pleasantries
- Generic questions with no personal context
- Task-specific instructions unlikely to recur
- Media references ("<Media omitted>", "sent a photo")

Output ONLY a single integer 1-10. Nothing else.

---
CHUNK:
{chunk_text}
---

Score (1-10):"""


def _get_empty_mcp_config() -> Path:
    """Create/return path to empty MCP config for subprocess isolation."""
    empty_mcp = Path(tempfile.gettempdir()) / "kontext-empty-mcp.json"
    if not empty_mcp.exists():
        empty_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    return empty_mcp


def grade_chunk_with_haiku(chunk_path: Path) -> dict:
    """
    Send a chunk to Haiku for scoring.

    Returns: {"path": str, "score": int, "error": str|None}
    """
    result = {"path": str(chunk_path.name), "score": 0, "error": None}

    try:
        content = chunk_path.read_text(encoding="utf-8", errors="replace")
        # Truncate if too long (Haiku context is limited, and we just need a score)
        if len(content) > 40000:
            content = content[:40000] + "\n\n[...truncated for grading...]"

        prompt = _GRADE_PROMPT.format(chunk_text=content)

        cli = shutil.which("claude")
        if not cli:
            result["error"] = "claude CLI not found"
            return result

        empty_mcp = _get_empty_mcp_config()
        env = os.environ.copy()
        env["KONTEXT_SKIP_HOOKS"] = "1"

        # Use stdin to avoid Windows command line length limits (~8K chars)
        # Claude CLI reads from stdin when given --print flag
        proc = subprocess.run(
            [
                cli, "--print",
                "--model", HAIKU_MODEL,
                "--strict-mcp-config",
                "--mcp-config", str(empty_mcp),
            ],
            input=prompt,
            capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="replace",
            env=env, cwd=tempfile.gettempdir(),
        )

        if proc.returncode != 0:
            result["error"] = f"CLI error: {proc.stderr[:100]}"
            return result

        # Parse the score
        out = (proc.stdout or "").strip()
        # Extract first number from response
        for line in out.split("\n"):
            line = line.strip()
            if line.isdigit():
                score = int(line)
                result["score"] = max(1, min(10, score))
                return result

        # Try to find a number anywhere
        import re
        match = re.search(r"\b([1-9]|10)\b", out)
        if match:
            result["score"] = int(match.group(1))
            return result

        result["error"] = f"Could not parse score from: {out[:50]}"
        result["score"] = 5  # Default to medium if parsing fails

    except subprocess.TimeoutExpired:
        result["error"] = "Timeout"
        result["score"] = 5
    except Exception as e:
        result["error"] = str(e)
        result["score"] = 5

    return result


def grade_all_chunks(threshold: int = DEFAULT_THRESHOLD, dry_run: bool = False) -> dict:
    """
    Grade all chunks in _chunks/ directory.

    Returns: {
        "total": int,
        "passed": int,
        "skipped": int,
        "errors": int,
        "chunks": [{"path": str, "score": int, "pass": bool, "error": str|None}, ...]
    }
    """
    if not CHUNKS_DIR.exists():
        print(f"ERROR: Chunks directory not found: {CHUNKS_DIR}")
        return {"total": 0, "passed": 0, "skipped": 0, "errors": 0, "chunks": []}

    chunk_files = sorted(CHUNKS_DIR.glob("chunk_*.md"))
    if not chunk_files:
        print("No chunks found to grade.")
        return {"total": 0, "passed": 0, "skipped": 0, "errors": 0, "chunks": []}

    print(f"Grading {len(chunk_files)} chunks with Haiku (threshold: {threshold})...")
    print(f"Using {MAX_WORKERS} parallel workers...")

    results = []
    passed = 0
    skipped = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_chunk = {
            executor.submit(grade_chunk_with_haiku, chunk): chunk
            for chunk in chunk_files
        }

        for i, future in enumerate(as_completed(future_to_chunk), 1):
            chunk = future_to_chunk[future]
            try:
                result = future.result()
                result["pass"] = result["score"] >= threshold

                if result["error"]:
                    errors += 1
                    _log.warning(f"GRADE_ERROR: {result['path']} - {result['error']}")

                if result["pass"]:
                    passed += 1
                    status = "PASS"
                else:
                    skipped += 1
                    status = "SKIP"

                results.append(result)
                print(f"  [{i}/{len(chunk_files)}] {result['path']}: {result['score']}/10 -> {status}")
                _log.info(f"GRADE: {result['path']} score={result['score']} pass={result['pass']}")

            except Exception as e:
                errors += 1
                results.append({
                    "path": str(chunk.name),
                    "score": 5,
                    "pass": True,  # On error, default to passing
                    "error": str(e),
                })
                _log.error(f"GRADE_EXCEPTION: {chunk.name} - {e}")

    summary = {
        "total": len(chunk_files),
        "passed": passed,
        "skipped": skipped,
        "errors": errors,
        "threshold": threshold,
        "chunks": sorted(results, key=lambda x: x["path"]),
    }

    if not dry_run:
        GRADED_MANIFEST.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"\nWrote grading manifest to {GRADED_MANIFEST}")

    print(f"\n{'='*50}")
    print(f"GRADING COMPLETE")
    print(f"{'='*50}")
    print(f"  Total chunks:    {summary['total']}")
    print(f"  Passed (>={threshold}):   {summary['passed']}")
    print(f"  Skipped (<{threshold}):   {summary['skipped']}")
    print(f"  Errors:          {summary['errors']}")
    print(f"  Pass rate:       {100*passed/len(chunk_files):.1f}%")

    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="AI-powered chunk grading with Haiku")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Minimum score to pass (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show scores without writing manifest")
    args = parser.parse_args()

    grade_all_chunks(threshold=args.threshold, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
