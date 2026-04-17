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
