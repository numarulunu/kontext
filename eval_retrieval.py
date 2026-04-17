"""Kontext Retrieval Evaluation Harness.

Runs a labeled query set against the local Kontext DB and reports
recall@3, recall@5, and MRR over memory files. Produces a timestamped
CSV for regression tracking.

Designed to be idempotent and side-effect-free against the DB: no
writes, no embedding index rebuild. Semantic mode reuses the cached
embeddings already in `entries.embedding`.

Usage:
    python -m eval_retrieval                                  # semantic=True, default out path
    python -m eval_retrieval --semantic=false                 # FTS5 only
    python -m eval_retrieval --out docs/eval-baselines/x.csv  # explicit out
    python -m eval_retrieval --top-k 10                       # expand retrieval window
    python -m eval_retrieval --queries eval_retrieval.yaml    # explicit query file

Log:
    _eval_retrieval.log (next to this script)

Exit codes:
    0 success, 1 query file missing / unparseable, 2 DB / model failure.
"""
from __future__ import annotations

import argparse
import csv
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0"

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "_eval_retrieval.log"
DEFAULT_QUERIES = ROOT / "eval_retrieval.yaml"
DEFAULT_OUT_DIR = ROOT / "docs" / "eval-baselines"

_log = logging.getLogger("kontext.eval_retrieval")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)


# ---------------------------------------------------------------------------
# Minimal YAML subset parser — avoids adding a runtime dependency for a
# schema this shallow. Handles exactly the shape used by eval_retrieval.yaml:
#   - list of dicts
#   - scalar values (quoted or bare strings, bools, ints)
#   - inline flow lists for expected_files: [a.md, b.md]
# ---------------------------------------------------------------------------

def _parse_scalar(raw: str):
    s = raw.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    try:
        return int(s)
    except ValueError:
        return s


def _parse_inline_list(raw: str) -> list:
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise ValueError(f"expected inline list, got: {raw!r}")
    body = inner[1:-1].strip()
    if not body:
        return []
    return [_parse_scalar(p) for p in body.split(",")]


def load_queries(path: Path) -> list[dict]:
    """Parse the constrained YAML schema used by eval_retrieval.yaml."""
    if not path.exists():
        raise FileNotFoundError(f"Query file not found: {path}")

    records: list[dict] = []
    current: dict | None = None

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if line.lstrip().startswith("- "):
            if current is not None:
                records.append(current)
            current = {}
            line = line.lstrip()[2:]  # strip "- "
            if ":" not in line:
                raise ValueError(f"line {lineno}: bad list-start: {raw!r}")
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            current[key] = _parse_scalar(val) if val else ""
            continue

        if current is None:
            raise ValueError(f"line {lineno}: value outside list entry: {raw!r}")

        if ":" not in line:
            raise ValueError(f"line {lineno}: expected key:value, got: {raw!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()

        if val.startswith("["):
            current[key] = _parse_inline_list(val)
        elif val == "":
            current[key] = None
        else:
            current[key] = _parse_scalar(val)

    if current is not None:
        records.append(current)

    # Validate required keys
    for i, r in enumerate(records):
        for req in ("query", "expected_files", "category"):
            if req not in r:
                raise ValueError(f"record #{i+1} missing required key: {req!r}")
        r.setdefault("held_out", False)

    return records


# ---------------------------------------------------------------------------
# Retrieval -- rank unique files from entry-level results
# ---------------------------------------------------------------------------

def _rank_files(entries: list[dict]) -> list[str]:
    """Collapse a ranked entry list into a ranked unique file list (first-seen order)."""
    seen: list[str] = []
    for e in entries:
        f = e.get("file")
        if f and f not in seen:
            seen.append(f)
    return seen


def run_query(db, query: str, semantic: bool, top_k: int, model) -> tuple[list[str], int]:
    """Execute one query. Returns (ranked_files, latency_ms).

    Mirrors mcp_server.py's kontext_query flow: semantic when flagged + model
    available, else FTS5 via search_entries. Pull a wider entry window
    (top_k * 5) so the file-level top-k has enough distinct candidates.
    """
    entry_limit = max(top_k * 5, 20)
    t0 = time.perf_counter()

    if semantic and model is not None:
        try:
            vec = model.encode(query)
            if hasattr(vec, "tolist"):
                vec = vec.tolist()
            entries = db.semantic_search(list(vec), limit=entry_limit)
        except Exception as e:
            _log.warning(f"SEMANTIC_FALLBACK query={query!r} err={type(e).__name__}: {e}")
            entries = db.search_entries(query, limit=entry_limit)
    else:
        entries = db.search_entries(query, limit=entry_limit)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return _rank_files(entries), latency_ms


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def recall_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
    if not expected:
        return 0.0
    topk = set(retrieved[:k])
    hits = sum(1 for f in expected if f in topk)
    return hits / len(expected)


def mrr(expected: list[str], retrieved: list[str]) -> float:
    expected_set = set(expected)
    for i, f in enumerate(retrieved, start=1):
        if f in expected_set:
            return 1.0 / i
    return 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _bucketed_summary(rows: list[dict]) -> str:
    """Pretty-print mean recall@3 / recall@5 / MRR per category x (train|held-out)."""
    from collections import defaultdict
    buckets: dict[tuple[str, bool], list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r["category"], r["held_out"])].append(r)

    lines = []
    header = f"{'category':<16}{'split':<10}{'n':>4}  {'r@3':>6}  {'r@5':>6}  {'mrr':>6}  {'latency_ms':>12}"
    lines.append(header)
    lines.append("-" * len(header))
    for (cat, ho), bucket in sorted(buckets.items()):
        split = "held-out" if ho else "train"
        lines.append(
            f"{cat:<16}{split:<10}{len(bucket):>4}  "
            f"{_mean([b['recall_at_3'] for b in bucket]):>6.3f}  "
            f"{_mean([b['recall_at_5'] for b in bucket]):>6.3f}  "
            f"{_mean([b['mrr'] for b in bucket]):>6.3f}  "
            f"{_mean([b['latency_ms'] for b in bucket]):>12.1f}"
        )

    # Overall
    lines.append("-" * len(header))
    for ho in (False, True):
        bucket = [r for r in rows if r["held_out"] == ho]
        split = "held-out" if ho else "train"
        lines.append(
            f"{'OVERALL':<16}{split:<10}{len(bucket):>4}  "
            f"{_mean([b['recall_at_3'] for b in bucket]):>6.3f}  "
            f"{_mean([b['recall_at_5'] for b in bucket]):>6.3f}  "
            f"{_mean([b['mrr'] for b in bucket]):>6.3f}  "
            f"{_mean([b['latency_ms'] for b in bucket]):>12.1f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eval_retrieval",
        description="Evaluate Kontext retrieval quality (recall@3, recall@5, MRR).",
    )
    parser.add_argument(
        "--semantic", default="true", choices=["true", "false"],
        help="Use semantic (embedding) search. Default: true.",
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="File-level top-k for metrics reporting (default: 5).",
    )
    parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES),
        help=f"Path to labeled query YAML (default: {DEFAULT_QUERIES.name}).",
    )
    parser.add_argument(
        "--out", default=None,
        help=f"Output CSV path (default: {DEFAULT_OUT_DIR}/eval_results_<ts>.csv).",
    )
    parser.add_argument(
        "--db", default=None,
        help="Override KONTEXT_DB_PATH for this run.",
    )
    args = parser.parse_args()

    semantic = args.semantic.lower() == "true"

    # Prepare output path
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.out:
        out_path = Path(args.out).expanduser()
    else:
        mode_tag = "sem" if semantic else "fts5"
        out_path = DEFAULT_OUT_DIR / f"eval_results_{mode_tag}_{ts}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load queries
    try:
        queries = load_queries(Path(args.queries))
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR loading queries: {e}", file=sys.stderr)
        _log.error(f"QUERIES_LOAD_FAIL: {e}")
        return 1

    _log.info(f"START semantic={semantic} top_k={args.top_k} queries={len(queries)} out={out_path}")

    # Connect DB + optionally load model
    if args.db:
        os.environ["KONTEXT_DB_PATH"] = args.db
    try:
        from db import KontextDB  # noqa: E402
        db = KontextDB()
    except Exception as e:
        print(f"ERROR opening DB: {e}", file=sys.stderr)
        _log.error(f"DB_OPEN_FAIL: {e}")
        return 2

    model = None
    if semantic:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            print(
                f"WARNING: semantic requested but sentence-transformers unavailable "
                f"({type(e).__name__}: {e}). Falling back to FTS5 for all queries.",
                file=sys.stderr,
            )
            _log.warning(f"MODEL_LOAD_FAIL: {e}")
            semantic = False

    # Run queries
    rows: list[dict] = []
    for i, q in enumerate(queries, start=1):
        ranked, latency_ms = run_query(db, q["query"], semantic, args.top_k, model)
        r3 = recall_at_k(q["expected_files"], ranked, 3)
        r5 = recall_at_k(q["expected_files"], ranked, 5)
        m = mrr(q["expected_files"], ranked)

        rows.append({
            "query": q["query"],
            "category": q["category"],
            "held_out": bool(q["held_out"]),
            "expected_files": "|".join(q["expected_files"]),
            "top_files": "|".join(ranked[:args.top_k]),
            "recall_at_3": r3,
            "recall_at_5": r5,
            "mrr": m,
            "latency_ms": latency_ms,
        })

        flag = "*" if q["held_out"] else " "
        print(f"{i:>2}.{flag} [{q['category']:<16}] r@3={r3:.2f} r@5={r5:.2f} "
              f"mrr={m:.2f} {latency_ms:>4}ms  {q['query'][:60]}")

    # Write CSV
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    print()
    print(_bucketed_summary(rows))
    print()
    print(f"CSV written: {out_path}")
    _log.info(f"END rows={len(rows)} out={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
