"""Build the JSON payload consumed by the SPA at /data.js.

The SPA expects `window.KONTEXT_DATA` with a specific shape (see
static_dashboard/data.js for the mock reference). This module queries the
real SQLite library and returns the same shape populated from the live DB.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cloud.dashboard_synth import synthesize_entries


_SNAPSHOT_CACHE: dict[str, Any] = {"built_at": 0.0, "payload": None}
_CACHE_TTL_SEC = 30.0


def _parse_ts_ms(ts: str | None, fallback_ms: int) -> int:
    if not ts:
        return fallback_ms
    s = str(ts).strip()
    if not s:
        return fallback_ms
    if s.endswith("Z"):
        s = s[:-1]
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return fallback_ms


def _tier_from_grade(grade: float | None) -> str:
    g = grade or 5.0
    if g >= 8.5:
        return "S"
    if g >= 7.0:
        return "A"
    if g >= 5.0:
        return "B"
    return "C"


def _role_from_class(device_class: str | None) -> str:
    if device_class == "interactive":
        return "primary"
    if device_class == "server":
        return "replica"
    return "replica"


def build_snapshot(db_path: str) -> dict[str, Any]:
    """Return the dashboard payload. Cached in-process for _CACHE_TTL_SEC
    because the relations derivation is O(entities × facts).
    """
    now = time.time()
    cached = _SNAPSHOT_CACHE.get("payload")
    if cached is not None and (now - _SNAPSHOT_CACHE["built_at"]) < _CACHE_TTL_SEC:
        return cached
    payload = _build_snapshot_uncached(db_path)
    _SNAPSHOT_CACHE["payload"] = payload
    _SNAPSHOT_CACHE["built_at"] = now
    return payload


def _build_snapshot_uncached(db_path: str) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    day_ms = 86_400_000
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Entries: one SPA entry per memory file, aggregated from entries+file_meta.
        file_rows = conn.execute(
            """
            SELECT fm.filename AS file,
                   COALESCE(fm.file_type, 'user') AS type,
                   COALESCE(fm.description, '') AS desc,
                   COUNT(e.id) AS fact_count,
                   AVG(e.grade) AS avg_grade,
                   COALESCE(SUM(e.access_count), 0) AS uses,
                   MAX(e.last_accessed) AS last_used,
                   MIN(e.created_at) AS created,
                   GROUP_CONCAT(e.fact, ' · ') AS body
            FROM file_meta fm
            LEFT JOIN entries e ON e.file = fm.filename
            GROUP BY fm.filename
            ORDER BY fm.filename
            """
        ).fetchall()

        # Cover entries that exist without a file_meta row AND auto-create
        # a stub file_meta entry so the next snapshot JOIN picks them up
        # naturally. Prevents "orphan" files from rendering with empty
        # description forever — kontext_write doesn't currently insert
        # file_meta for new files, and we don't want to hold that contract
        # open. Inferred type from filename prefix; description falls back
        # to the first fact (first 70 chars).
        orphan_rows = conn.execute(
            """
            SELECT e.file AS file,
                   'user' AS type,
                   '' AS desc,
                   COUNT(e.id) AS fact_count,
                   AVG(e.grade) AS avg_grade,
                   COALESCE(SUM(e.access_count), 0) AS uses,
                   MAX(e.last_accessed) AS last_used,
                   MIN(e.created_at) AS created,
                   GROUP_CONCAT(e.fact, ' · ') AS body
            FROM entries e
            LEFT JOIN file_meta fm ON fm.filename = e.file
            WHERE fm.filename IS NULL
            GROUP BY e.file
            """
        ).fetchall()

        def _infer_type(fname: str) -> str:
            for prefix, t in [("user_", "user"), ("project_", "project"),
                              ("feedback_", "feedback")]:
                if fname.startswith(prefix):
                    return t
            return "user"

        for r in orphan_rows:
            first = conn.execute(
                "SELECT fact FROM entries WHERE file=? ORDER BY created_at ASC LIMIT 1",
                (r["file"],),
            ).fetchone()
            desc = ""
            if first and first["fact"]:
                desc = first["fact"].strip().split("\n")[0][:70]
            conn.execute(
                "INSERT OR IGNORE INTO file_meta (filename, file_type, description) VALUES (?, ?, ?)",
                (r["file"], _infer_type(r["file"]), desc),
            )
        if orphan_rows:
            conn.commit()

        all_rows = list(file_rows) + list(orphan_rows)

        # Derive file-to-file relations from the entities table. `relations`
        # stores (entity_a, entity_b) by concept name ("Melocchi", "Bass"),
        # not by filename. To turn that into file-level edges: find which
        # files contain each entity (by scanning fact text), then for each
        # relation (A,B) add an edge between every file in files(A) × files(B).
        rel_rows = conn.execute(
            "SELECT entity_a, entity_b, confidence FROM relations"
        ).fetchall()
        fact_rows = conn.execute(
            "SELECT file, fact FROM entries WHERE fact IS NOT NULL"
        ).fetchall()
        file_text: dict[str, str] = defaultdict(str)
        file_facts: dict[str, list[str]] = defaultdict(list)
        for fr in fact_rows:
            fact = fr["fact"] or ""
            file_text[fr["file"]] += fact.lower() + " · "
            if fact.strip():
                file_facts[fr["file"]].append(fact)

        entity_names: set[str] = set()
        for r in rel_rows:
            if r["entity_a"]:
                entity_names.add(r["entity_a"])
            if r["entity_b"]:
                entity_names.add(r["entity_b"])

        # entity → set of files whose concatenated fact text contains it
        entity_to_files: dict[str, list[str]] = {}
        for name in entity_names:
            needle = name.lower()
            if len(needle) < 3:
                continue  # skip tiny tokens that match everything
            hit = [f for f, txt in file_text.items() if needle in txt]
            if hit:
                entity_to_files[name] = hit

        edge_weight: dict[tuple[str, str], float] = defaultdict(float)
        for r in rel_rows:
            fs_a = entity_to_files.get(r["entity_a"], [])
            fs_b = entity_to_files.get(r["entity_b"], [])
            if not fs_a or not fs_b:
                continue
            w = float(r["confidence"] or 0.5)
            for fa in fs_a:
                for fb in fs_b:
                    if fa == fb:
                        continue
                    key = (fa, fb) if fa < fb else (fb, fa)
                    edge_weight[key] += w

        relations_by_file: dict[str, list[str]] = defaultdict(list)
        # Sort edges by descending weight so top-N truncation keeps the strongest.
        sorted_edges = sorted(edge_weight.items(), key=lambda kv: -kv[1])
        for (fa, fb), _w in sorted_edges:
            if len(relations_by_file[fa]) < 6:
                relations_by_file[fa].append(fb)
            if len(relations_by_file[fb]) < 6:
                relations_by_file[fb].append(fa)

        file_to_id: dict[str, str] = {r["file"]: f"e{i+1:03d}" for i, r in enumerate(all_rows)}

        # LLM synthesis: `why` + `body` for each entry. Cache hits are free;
        # misses hit Haiku 4.5 in parallel and persist to dashboard_synth_cache.
        synth_inputs = [
            (r["file"], r["type"] or "user", file_facts.get(r["file"], []))
            for r in all_rows
        ]
        synth_results = synthesize_entries(db_path, synth_inputs)

        entries: list[dict[str, Any]] = []
        for i, r in enumerate(all_rows):
            last_ms = _parse_ts_ms(r["last_used"], now_ms)
            created_ms = _parse_ts_ms(r["created"], now_ms)
            days_since = max(0.0, (now_ms - last_ms) / day_ms)
            decay = round(1 - math.exp(-days_since * 0.03), 3)
            body_fallback = r["body"] or ""
            if len(body_fallback) > 600:
                body_fallback = body_fallback[:600] + "…"
            rels = relations_by_file.get(r["file"], [])
            rel_ids = [file_to_id[f] for f in rels if f in file_to_id][:6]
            synth = synth_results.get(r["file"], {})
            entries.append({
                "id": file_to_id[r["file"]],
                "file": r["file"],
                "type": r["type"] or "user",
                "tier": _tier_from_grade(r["avg_grade"]),
                "desc": r["desc"] or f"{r['fact_count']} facts",
                "decay": decay,
                "lastUsed": last_ms,
                "created": created_ms,
                "uses": int(r["uses"] or 0),
                "relations": rel_ids,
                "why": synth.get("why") or f"{r['fact_count']} facts captured",
                "body": synth.get("body") or body_fallback,
            })

        # Devices
        device_rows = conn.execute(
            """
            SELECT d.id, d.label, d.device_class, d.revoked_at,
                   MAX(h.created_at) AS last_op,
                   SUM(CASE WHEN h.created_at > datetime('now', '-1 day') THEN 1 ELSE 0 END) AS cap_24h
            FROM devices d
            LEFT JOIN history_ops h ON h.device_id = d.id
            GROUP BY d.id
            ORDER BY d.enrolled_at ASC
            """
        ).fetchall()
        devices = []
        for d in device_rows:
            last_ms = _parse_ts_ms(d["last_op"], now_ms)
            status = "offline" if d["revoked_at"] else ("online" if (now_ms - last_ms) < 10 * 60_000 else "idle")
            devices.append({
                "id": d["id"][:12],
                "label": d["label"],
                "role": _role_from_class(d["device_class"]),
                "last": last_ms,
                "captures24h": int(d["cap_24h"] or 0),
                "status": status,
            })

        # Per-day counts for the 14-day history chart
        tool_counts = {row["d"]: row["n"] for row in conn.execute(
            "SELECT DATE(created_at) AS d, COUNT(*) AS n FROM tool_events "
            "WHERE created_at > datetime('now', '-15 days') GROUP BY DATE(created_at)"
        ).fetchall()}
        prompt_counts = {row["d"]: row["n"] for row in conn.execute(
            "SELECT DATE(created_at) AS d, COUNT(*) AS n FROM user_prompts "
            "WHERE created_at > datetime('now', '-15 days') GROUP BY DATE(created_at)"
        ).fetchall()}

        total_files = len(entries)
        total_entries = sum(int(r["fact_count"] or 0) for r in all_rows)
        files_active_30d = sum(1 for e in entries if e["decay"] < 0.6)
        relation_count = len(rel_rows)

        # Honest `known_since` — first load-bearing fact, not first stub.
        # A fact is "load-bearing" when it's been retrieved into Claude's
        # context multiple times AND graded as useful AND still active.
        # This makes the dashboard's "known you for X days" a falsifiable
        # claim derived from actual use, not a count of rows-since-import.
        #
        # Three-tier fallback so the value never disappears on a fresh DB:
        #   1. Load-bearing fact (grade >= 7, active, used >= 3x)
        #   2. Any high-grade active fact (grade >= 7)
        #   3. Oldest entry in the table (equivalent to the old behavior)
        known_since = conn.execute("""
            SELECT MIN(created_at) AS t FROM entries
            WHERE grade >= 7 AND tier = 'active' AND access_count >= 3
        """).fetchone()["t"]
        if not known_since:
            known_since = conn.execute("""
                SELECT MIN(created_at) AS t FROM entries
                WHERE grade >= 7 AND tier = 'active'
            """).fetchone()["t"]
        if not known_since:
            known_since = conn.execute(
                "SELECT MIN(created_at) AS t FROM entries"
            ).fetchone()["t"]
        known_since_ms = _parse_ts_ms(known_since, now_ms)
        age_days = max(1, (now_ms - known_since_ms) // day_ms)

        breadth = min(100, int(total_files / 40 * 100))
        depth = min(100, int(total_entries / 200 * 100))
        recency = min(100, int(files_active_30d / max(total_files, 1) * 100))
        longevity = min(100, int(age_days / 180 * 100))
        linkage = min(100, int(relation_count / max(total_files, 1) * 50))

        history = []
        for d_off in range(13, -1, -1):
            ts = now_ms - d_off * day_ms
            date_str = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
            history.append({
                "t": ts,
                "breadth": breadth,
                "depth": depth,
                "recency": recency,
                "longevity": longevity,
                "linkage": linkage,
                "captures": int(tool_counts.get(date_str, 0)),
                "prompts": int(prompt_counts.get(date_str, 0)),
            })

        weights = {"breadth": 0.18, "depth": 0.22, "recency": 0.25, "longevity": 0.15, "linkage": 0.20}
        dims_current = history[-1]
        dims_prev = history[-8]
        score = round(sum(dims_current[k] * w for k, w in weights.items()))
        prev_score = round(sum(dims_prev[k] * w for k, w in weights.items()))

        # Feed: recent history_ops
        feed_rows = conn.execute(
            """
            SELECT h.created_at, h.op_kind, h.entity_type, h.entity_id,
                   d.label AS device_label, d.id AS device_id
            FROM history_ops h
            LEFT JOIN devices d ON d.id = h.device_id
            ORDER BY h.created_at DESC LIMIT 40
            """
        ).fetchall()
        feed = []
        for f in feed_rows:
            ev = {
                "create": "capture",
                "update": "capture",
                "promote": "promote",
                "decay": "decay",
                "link": "link",
                "sync": "sync",
            }.get(str(f["op_kind"]).lower(), str(f["op_kind"]).lower() or "capture")
            feed.append({
                "t": _parse_ts_ms(f["created_at"], now_ms),
                "ev": ev,
                "file": (f["entity_id"] or "—")[:60],
                "action": f["op_kind"] or "—",
                "device": (f["device_id"] or "—")[:12],
                "source": f["entity_type"] or "",
            })

        history_ops_total = conn.execute(
            "SELECT COUNT(*) AS n FROM history_ops"
        ).fetchone()["n"]
        canonical = sum(1 for e in entries if e["tier"] in ("S", "A"))
        tool_events_24h = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_events WHERE created_at > datetime('now', '-1 day')"
        ).fetchone()["n"]
        prompts_24h = conn.execute(
            "SELECT COUNT(*) AS n FROM user_prompts WHERE created_at > datetime('now', '-1 day')"
        ).fetchone()["n"]
        entries_touched_24h = conn.execute(
            "SELECT COUNT(DISTINCT file) AS n FROM entries WHERE last_accessed > datetime('now', '-1 day')"
        ).fetchone()["n"]
        last_capture = conn.execute(
            "SELECT MAX(created_at) AS t FROM history_ops"
        ).fetchone()["t"]
        last_capture_ms = _parse_ts_ms(last_capture, now_ms)

        return {
            "now": now_ms,
            "entries": entries,
            "devices": devices,
            "history": history,
            "feed": feed,
            "score": score,
            "prevScore": prev_score,
            "dimensions": dims_current,
            "prevDimensions": dims_prev,
            "totals": {
                "entries": total_entries,
                "devices": len(devices),
                "histOps": int(history_ops_total),
                "canonical": canonical,
            },
            "activity24h": {
                "toolEvents": int(tool_events_24h),
                "prompts": int(prompts_24h),
                "entriesTouched": int(entries_touched_24h),
                "lastCaptureAgo": max(0, now_ms - last_capture_ms),
            },
            # Honest "known you for X days" anchor — see known_since query above.
            "knownSinceMs": known_since_ms,
            "ageDays": int(age_days),
        }
    finally:
        conn.close()
