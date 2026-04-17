"""One-shot: queue a history_op for every pre-link entry so sync pushes them.

Safe to re-run. Uses id-deterministic op-ids so duplicate runs are no-ops.
"""
import hashlib
import sys
from db import KontextDB
from cloud.codec import pack_payload


def _utc_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(db_path: str = "kontext.db") -> int:
    db = KontextDB(db_path)
    state = db._load_cloud_link_state()
    if not state:
        print("not linked — run link_workspace first", file=sys.stderr)
        return 1

    ws = state["workspace_id"]
    dev = state["device_id"]
    now = _utc_ts()

    entries = db.execute(
        "SELECT id, file, fact, source, grade, tier FROM entries ORDER BY id ASC"
    ).fetchall()

    queued = 0
    skipped = 0
    for row in entries:
        e = dict(row)
        op_id = "op-backfill-" + hashlib.sha256(
            f"{ws}|{dev}|entry|{e['id']}".encode("utf-8")
        ).hexdigest()[:24]
        payload = pack_payload({
            "file": e["file"],
            "fact": e["fact"],
            "source": e["source"] or "",
            "grade": e["grade"],
            "tier": e["tier"],
        })
        inserted = db.append_history_op(
            op_id=op_id,
            workspace_id=ws,
            device_id=dev,
            op_kind="entry.written",
            entity_type="entry",
            entity_id=str(e["id"]),
            payload=payload,
            created_at=now,
        )
        if inserted:
            queued += 1
        else:
            skipped += 1

    print(f"queued: {queued}  skipped(existing): {skipped}  total entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "kontext.db"))
