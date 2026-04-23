"""Reciprocal-rank fusion for combining FTS5 and semantic result lists.

FTS5 wins on exact terms ("Melocchi", "ANAF", "D212"); semantic wins on
paraphrase ("I'm stuck" -> blind_spots). RRF gives each retriever one
vote per position and sums the reciprocal of the rank, so an item in
the top-3 of one retriever outranks an item only in the top-20 of both.
"""
from __future__ import annotations


def rrf_merge(fts_results: list[dict], sem_results: list[dict],
              k: int = 60) -> list[dict]:
    """Fuse two ranked entry lists via reciprocal-rank fusion.

    Args:
        fts_results: Ranked entries from FTS5 (each must have an "id" key).
        sem_results: Ranked entries from semantic search (same shape).
        k: RRF damping constant. The canonical value from the original
           paper is 60; larger k flattens the contribution of top ranks.

    Returns:
        Merged list sorted by combined RRF score (descending), with an
        added "rrf_score" key. Each entry id appears at most once. The
        dict preferred is the FTS5 version when an id appears in both,
        since FTS5 rows carry the full entry payload.
    """
    scores: dict[int, float] = {}
    by_id: dict[int, dict] = {}

    for rank, row in enumerate(fts_results):
        eid = row.get("id")
        if eid is None:
            continue
        scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + rank)
        by_id[eid] = row

    for rank, row in enumerate(sem_results):
        eid = row.get("id")
        if eid is None:
            continue
        scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + rank)
        by_id.setdefault(eid, row)

    merged = sorted(scores.items(), key=lambda x: -x[1])
    return [{**by_id[eid], "rrf_score": s} for eid, s in merged]


def rerank(entries: list[dict], lambda_decay: float = 0.02,
           access_weight: float = 0.1) -> list[dict]:
    """Multiply rrf_score by signal already in the schema.

    After RRF fusion, every entry has a pure position-based score that
    ignores grade, recency, and usage history. This is the leak the
    Mastermind council flagged: an entry retrieved 40 times ranks
    identically to one written once and ignored.

    rerank_score = rrf_score
                 * (grade / 10)
                 * exp(-lambda_decay * days_since_accessed)
                 * (1 + access_weight * log1p(access_count))

    Defaults:
      lambda_decay=0.02  → 50% weight at ~35d stale, 10% at ~115d.
      access_weight=0.1  → modest boost; log1p(40) ≈ 3.7 → 1.37× lift.

    Defensive to missing fields — treats absent grade as 5 (neutral),
    absent last_accessed as "now" (no decay), absent access_count as 0.
    Returns a NEW sorted list with `rerank_score` attached.
    """
    import math
    import time as _time
    from datetime import datetime, timezone

    now_s = _time.time()

    def _days_since(ts_str: str | None) -> float:
        if not ts_str:
            return 0.0
        try:
            s = str(ts_str).strip()
            if s.endswith("Z"):
                s = s[:-1]
            s = s.replace("T", " ")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                        "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                    return max(0.0, (now_s - dt.timestamp()) / 86400.0)
                except ValueError:
                    continue
        except Exception:
            pass
        return 0.0

    reranked: list[dict] = []
    for e in entries:
        rrf_s = float(e.get("rrf_score") or 0.0)
        grade = float(e.get("grade") or 5.0)
        ac = int(e.get("access_count") or 0)
        days = _days_since(e.get("last_accessed"))
        grade_w = grade / 10.0
        recency_w = math.exp(-lambda_decay * days)
        access_w = 1.0 + access_weight * math.log1p(ac)
        e["rerank_score"] = rrf_s * grade_w * recency_w * access_w
        reranked.append(e)

    reranked.sort(key=lambda x: -(x.get("rerank_score") or 0.0))
    return reranked
