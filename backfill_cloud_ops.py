"""One-shot: queue history_ops for every row across all 7 sync-eligible tables.

Preserves each row's real created_at / updated_at so the cloud reflects true
longevity and ordering, not backfill time.

Safe to re-run — op ids are deterministic per (workspace, device, kind, id),
so duplicate runs are no-ops at the history_ops unique index.
"""
import hashlib
import sys
from db import KontextDB
from cloud.codec import pack_payload


def _normalize_ts(value: str | None) -> str | None:
    """Convert SQLite 'YYYY-MM-DD HH:MM:SS' to ISO 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if "T" in v:
        # already ISO — ensure trailing Z
        return v if v.endswith("Z") else (v.rstrip("Z") + "Z")
    if " " in v:
        return v.replace(" ", "T") + "Z"
    return v


def _emit(db, ws, dev, kind, entity_type, entity_id, payload_dict, created_at) -> bool:
    # v2 prefix: invalidates v1 op ids so old entry backfill (with stale
    # now-timestamps) gets superseded by a fresh emission carrying the row's
    # real created_at.
    op_id = "op-backfill-v2-" + hashlib.sha256(
        f"{ws}|{dev}|{entity_type}|{entity_id}".encode("utf-8")
    ).hexdigest()[:24]
    return bool(db.append_history_op(
        op_id=op_id,
        workspace_id=ws,
        device_id=dev,
        op_kind=kind,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload=pack_payload(payload_dict),
        created_at=created_at,
    ))


def main(db_path: str = "kontext.db") -> int:
    db = KontextDB(db_path)
    state = db._load_cloud_link_state()
    if not state:
        print("not linked — run link_workspace first", file=sys.stderr)
        return 1

    ws = state["workspace_id"]
    dev = state["device_id"]

    totals: dict[str, tuple[int, int]] = {}

    # --- entries ---
    rows = db.conn.execute(
        "SELECT id, file, fact, source, grade, tier, created_at "
        "FROM entries ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "entry.written", "entry", e["id"], {
            "file": e["file"],
            "fact": e["fact"],
            "source": e["source"] or "",
            "grade": e["grade"],
            "tier": e["tier"],
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["entries"] = (q, s)

    # --- relations ---
    rows = db.conn.execute(
        "SELECT id, entity_a, relation, entity_b, confidence, source, created_at "
        "FROM relations ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "relation.written", "relation", e["id"], {
            "entity_a": e["entity_a"],
            "relation": e["relation"],
            "entity_b": e["entity_b"],
            "confidence": e["confidence"],
            "source": e["source"] or "",
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["relations"] = (q, s)

    # --- conflicts ---
    rows = db.conn.execute(
        "SELECT id, file, entry_a, entry_b, created_at "
        "FROM conflicts ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "conflict.detected", "conflict", e["id"], {
            "file": e["file"],
            "entry_a": e["entry_a"],
            "entry_b": e["entry_b"],
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["conflicts"] = (q, s)

    # --- sessions ---
    rows = db.conn.execute(
        "SELECT id, workspace, project, status, next_step, key_decisions, "
        "summary, files_touched, created_at FROM sessions ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "session.saved", "session", e["id"], {
            "project": e["project"] or "",
            "status": e["status"] or "",
            "next_step": e["next_step"] or "",
            "key_decisions": e["key_decisions"] or "",
            "summary": e["summary"] or "",
            "files_touched": e["files_touched"] or "",
            "workspace": e["workspace"] or "",
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["sessions"] = (q, s)

    # --- tool_events ---
    rows = db.conn.execute(
        "SELECT id, session_id, tool_name, summary, file_path, grade, created_at "
        "FROM tool_events ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "tool.logged", "tool_event", e["id"], {
            "session_id": e["session_id"] or "",
            "tool_name": e["tool_name"],
            "summary": e["summary"],
            "file_path": e["file_path"],
            "grade": e["grade"],
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["tool_events"] = (q, s)

    # --- user_prompts ---
    rows = db.conn.execute(
        "SELECT id, session_id, content, created_at "
        "FROM user_prompts ORDER BY id ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "prompt.logged", "prompt", e["id"], {
            "session_id": e["session_id"] or "",
            "content": e["content"],
        }, _normalize_ts(e["created_at"]))
        if ok: q += 1
        else: s += 1
    totals["user_prompts"] = (q, s)

    # --- file_meta (keyed by filename, uses updated_at) ---
    rows = db.conn.execute(
        "SELECT filename, file_type, description, updated_at "
        "FROM file_meta ORDER BY filename ASC"
    ).fetchall()
    q = s = 0
    for r in rows:
        e = dict(r)
        ok = _emit(db, ws, dev, "file_meta.upserted", "file_meta", e["filename"], {
            "filename": e["filename"],
            "file_type": e["file_type"] or "user",
            "description": e["description"] or "",
        }, _normalize_ts(e["updated_at"]))
        if ok: q += 1
        else: s += 1
    totals["file_meta"] = (q, s)

    print(f"{'table':<15} {'queued':>8} {'skipped':>8}")
    print("-" * 33)
    total_q = total_s = 0
    for name, (q, s) in totals.items():
        print(f"{name:<15} {q:>8} {s:>8}")
        total_q += q
        total_s += s
    print("-" * 33)
    print(f"{'TOTAL':<15} {total_q:>8} {total_s:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "kontext.db"))
