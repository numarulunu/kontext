"""Dashboard routes for the single-admin Kontext control plane.

No auth — single-user, obscure URL. Reads straight from the local DB.
Overview / Entries / Devices / Ops pages. Entries page is the quality
check — shows file, fact, grade, tier so you can eyeball whether the
library is being written sensibly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db import KontextDB


def _parse_utc(ts) -> datetime | None:
    """Parse a stored SQLite/ISO timestamp as UTC-aware."""
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1]
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _reltime(ts) -> str:
    dt = _parse_utc(ts)
    if not dt:
        return "—"
    now = datetime.now(timezone.utc)
    s = int((now - dt).total_seconds())
    if s < 0:
        return dt.astimezone().strftime("%b %d %H:%M")
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    if s < 86400 * 30:
        return f"{s // 86400}d ago"
    return dt.astimezone().strftime("%b %d %Y")


def _localtime(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    dt = _parse_utc(ts)
    if not dt:
        return "—"
    return dt.astimezone().strftime(fmt)


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "dashboard"


LOCAL_WORKSPACE_ID = "local"


def _resolve_workspace(db) -> str:
    """Return the workspace the dashboard should render for.

    Preference order: any cloud-linked workspace > a synthetic "local" workspace.
    If neither exists, auto-create the local workspace so the dashboard works
    out of the box on a fresh install with no cloud sync configured.
    """
    row = db.conn.execute(
        "SELECT id FROM workspaces "
        "WHERE id != ? ORDER BY created_at ASC LIMIT 1",
        (LOCAL_WORKSPACE_ID,),
    ).fetchone()
    if row:
        return row["id"]
    existing_local = db.conn.execute(
        "SELECT id FROM workspaces WHERE id = ?", (LOCAL_WORKSPACE_ID,),
    ).fetchone()
    if existing_local:
        return existing_local["id"]
    db.create_workspace(
        workspace_id=LOCAL_WORKSPACE_ID,
        name="Local",
        recovery_key_id="local",
    )
    return LOCAL_WORKSPACE_ID


SCORE_TARGETS = {
    "breadth_files": 20,         # distinct library files
    "depth_entries": 2000,       # total entries
    "recency_active_days": 30,   # active days (tool_events+prompts) in last 30
    "longevity_days": 365,       # 1 year = maxed
    "linkage_ratio": 1.0,        # 1 relation per entry
}

SCORE_WEIGHTS = {
    "breadth": 0.25,
    "depth": 0.25,
    "recency": 0.20,
    "longevity": 0.15,
    "linkage": 0.15,
}

LEVELS = [
    (0, "Stranger"),
    (20, "Acquaintance"),
    (40, "Familiar"),
    (60, "Close"),
    (80, "Trusted"),
]

NUDGES = {
    "breadth": "Write about more topics — hit library files you haven't touched.",
    "depth": "Feed more chats, notes, or transcripts to grow raw entry count.",
    "recency": "Quiet stretch — code, chat, or capture to bump active-day count.",
    "longevity": "This one just takes time. It grows on its own.",
    "linkage": "Relations are sparse — run /kontext brainstorm to link entities.",
}


def _level_for(score: int) -> str:
    name = LEVELS[0][1]
    for threshold, label in LEVELS:
        if score >= threshold:
            name = label
    return name


def _compute_score(conn) -> dict:
    total = conn.execute("SELECT count(*) AS n FROM entries").fetchone()["n"] or 0
    distinct_files = conn.execute("SELECT count(DISTINCT file) AS n FROM entries").fetchone()["n"] or 0
    first_row = conn.execute("SELECT min(created_at) AS first FROM entries").fetchone()
    first_at = first_row["first"] if first_row else None
    age_row = conn.execute(
        "SELECT CAST(julianday('now') - julianday(min(created_at)) AS INT) AS d FROM entries"
    ).fetchone()
    age_days = age_row["d"] if age_row and age_row["d"] is not None else 0
    relation_count = conn.execute("SELECT count(*) AS n FROM relations").fetchone()["n"] or 0

    # Recency = distinct days with ANY activity (tool_events or user_prompts)
    # in the last 30 days. This reflects coding/chat engagement, not just
    # memory writes — tool_events + prompts are the raw capture streams.
    active_days = conn.execute(
        """
        SELECT count(*) AS n FROM (
            SELECT substr(created_at, 1, 10) AS day FROM tool_events
             WHERE created_at > datetime('now', '-30 days')
            UNION
            SELECT substr(created_at, 1, 10) AS day FROM user_prompts
             WHERE created_at > datetime('now', '-30 days')
        )
        """
    ).fetchone()["n"] or 0

    breadth = min(distinct_files / SCORE_TARGETS["breadth_files"], 1.0)
    depth = min(total / SCORE_TARGETS["depth_entries"], 1.0)
    recency = min(active_days / SCORE_TARGETS["recency_active_days"], 1.0)
    longevity = min(age_days / SCORE_TARGETS["longevity_days"], 1.0)
    linkage = min((relation_count / total) if total > 0 else 0.0, SCORE_TARGETS["linkage_ratio"])

    dims = {
        "breadth": round(breadth * 100),
        "depth": round(depth * 100),
        "recency": round(recency * 100),
        "longevity": round(longevity * 100),
        "linkage": round(linkage * 100),
    }
    score = round(sum(dims[k] * SCORE_WEIGHTS[k] for k in dims))
    actionable = {k: v for k, v in dims.items() if k != "longevity"}
    weakest = min(actionable, key=lambda k: actionable[k])
    return {
        "score": score,
        "level": _level_for(score),
        "dimensions": dims,
        "known_since": first_at,
        "age_days": age_days,
        "distinct_files": distinct_files,
        "relation_count": relation_count,
        "active_days_30d": active_days,
        "weakest": weakest,
        "nudge": NUDGES[weakest],
    }


def _compute_activity(conn) -> dict:
    """Activity stats — what's been captured today and recently."""
    tool_today = conn.execute(
        "SELECT count(*) AS n FROM tool_events "
        "WHERE created_at > datetime('now', '-1 day')"
    ).fetchone()["n"] or 0
    prompt_today = conn.execute(
        "SELECT count(*) AS n FROM user_prompts "
        "WHERE created_at > datetime('now', '-1 day')"
    ).fetchone()["n"] or 0
    entry_today = conn.execute(
        "SELECT count(*) AS n FROM entries "
        "WHERE created_at > datetime('now', '-1 day')"
    ).fetchone()["n"] or 0
    tool_total = conn.execute("SELECT count(*) AS n FROM tool_events").fetchone()["n"] or 0
    prompt_total = conn.execute("SELECT count(*) AS n FROM user_prompts").fetchone()["n"] or 0
    last_capture_at = conn.execute(
        """
        SELECT max(ts) AS t FROM (
            SELECT max(created_at) AS ts FROM tool_events
            UNION ALL
            SELECT max(created_at) AS ts FROM user_prompts
            UNION ALL
            SELECT max(updated_at) AS ts FROM entries
        )
        """
    ).fetchone()["t"]
    return {
        "tool_today": tool_today,
        "prompt_today": prompt_today,
        "entry_today": entry_today,
        "tool_total": tool_total,
        "prompt_total": prompt_total,
        "last_capture_at": last_capture_at,
    }


def register_dashboard(app, db_path: str) -> None:
    """Mount the dashboard routes on `app`."""
    app.include_router(_build_router(db_path))


def _build_router(db_path: str) -> APIRouter:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["reltime"] = _reltime
    templates.env.filters["localtime"] = _localtime
    router = APIRouter()

    @router.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/dashboard", status_code=307)

    @router.get("/dashboard", response_class=HTMLResponse)
    def overview(request: Request):
        with KontextDB(db_path) as req_db:
            conn = req_db.conn
            workspace_id = _resolve_workspace(req_db)
            devices = conn.execute(
                """
                SELECT id, label, device_class, enrolled_at, revoked_at
                FROM devices WHERE workspace_id = ?
                ORDER BY enrolled_at ASC
                """,
                (workspace_id,),
            ).fetchall()
            history_count = conn.execute(
                "SELECT count(*) AS n FROM history_ops WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()["n"]
            canonical_count = conn.execute(
                "SELECT count(*) AS n FROM canonical_revisions r "
                "JOIN canonical_objects o ON o.id = r.object_id "
                "WHERE o.workspace_id = ?",
                (workspace_id,),
            ).fetchone()["n"]
            last_op = conn.execute(
                "SELECT created_at FROM history_ops WHERE workspace_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            entries_count = conn.execute(
                "SELECT count(*) AS n FROM entries"
            ).fetchone()["n"]
            per_file = conn.execute(
                """
                SELECT file, count(*) AS n FROM entries
                GROUP BY file ORDER BY n DESC LIMIT 8
                """
            ).fetchall()
            tier_dist = conn.execute(
                "SELECT tier, count(*) AS n FROM entries GROUP BY tier"
            ).fetchall()
            score = _compute_score(conn)
            activity = _compute_activity(conn)
        active_devices = [d for d in devices if d["revoked_at"] is None]
        return templates.TemplateResponse(
            request,
            "overview.html",
            {
                "workspace_id": workspace_id,
                "active_devices": len(active_devices),
                "total_devices": len(devices),
                "history_count": history_count,
                "canonical_count": canonical_count,
                "entries_count": entries_count,
                "last_op_at": last_op["created_at"] if last_op else None,
                "per_file": [dict(r) for r in per_file],
                "tier_dist": {r["tier"]: r["n"] for r in tier_dist},
                "score": score,
                "activity": activity,
                "nav_active": "overview",
            },
        )

    @router.get("/dashboard/entries", response_class=HTMLResponse)
    def entries_page(request: Request, limit: int = 50, q: str = ""):
        limit = max(1, min(limit, 500))
        with KontextDB(db_path) as req_db:
            conn = req_db.conn
            workspace_id = _resolve_workspace(req_db)
            if q.strip():
                like = f"%{q.strip()}%"
                rows = conn.execute(
                    """
                    SELECT id, file, fact, source, grade, tier,
                           created_at, updated_at, last_accessed
                    FROM entries
                    WHERE fact LIKE ? OR file LIKE ? OR source LIKE ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, file, fact, source, grade, tier,
                           created_at, updated_at, last_accessed
                    FROM entries
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            total = conn.execute("SELECT count(*) AS n FROM entries").fetchone()["n"]
            grade_buckets = conn.execute(
                """
                SELECT
                    CASE
                        WHEN grade >= 8 THEN 'A (8-10)'
                        WHEN grade >= 6 THEN 'B (6-8)'
                        WHEN grade >= 4 THEN 'C (4-6)'
                        ELSE 'D (<4)'
                    END AS bucket,
                    count(*) AS n
                FROM entries GROUP BY bucket ORDER BY bucket ASC
                """
            ).fetchall()
            tier_dist = conn.execute(
                "SELECT tier, count(*) AS n FROM entries GROUP BY tier"
            ).fetchall()
        return templates.TemplateResponse(
            request,
            "entries.html",
            {
                "workspace_id": workspace_id,
                "entries": [dict(r) for r in rows],
                "total": total,
                "limit": limit,
                "q": q,
                "grade_buckets": [dict(r) for r in grade_buckets],
                "tier_dist": {r["tier"]: r["n"] for r in tier_dist},
                "nav_active": "entries",
            },
        )

    @router.get("/dashboard/devices", response_class=HTMLResponse)
    def devices_page(request: Request):
        with KontextDB(db_path) as req_db:
            workspace_id = _resolve_workspace(req_db)
            rows = req_db.conn.execute(
                """
                SELECT d.id, d.label, d.device_class, d.enrolled_at, d.revoked_at,
                       (SELECT max(created_at) FROM history_ops h
                         WHERE h.workspace_id = d.workspace_id
                           AND h.device_id = d.id) AS last_op_at
                FROM devices d
                WHERE d.workspace_id = ?
                ORDER BY d.enrolled_at ASC
                """,
                (workspace_id,),
            ).fetchall()
        return templates.TemplateResponse(
            request,
            "devices.html",
            {
                "workspace_id": workspace_id,
                "devices": [dict(r) for r in rows],
                "nav_active": "devices",
            },
        )

    @router.post("/dashboard/devices/{device_id}/revoke")
    def revoke_device_route(device_id: str):
        with KontextDB(db_path) as req_db:
            workspace_id = _resolve_workspace(req_db)
            req_db.revoke_device(workspace_id, device_id)
        return RedirectResponse(url="/dashboard/devices", status_code=303)

    @router.post("/dashboard/devices/{device_id}/delete")
    def delete_device_route(device_id: str):
        with KontextDB(db_path) as req_db:
            workspace_id = _resolve_workspace(req_db)
            req_db.delete_device(workspace_id, device_id)
        return RedirectResponse(url="/dashboard/devices", status_code=303)

    @router.get("/dashboard/ops", response_class=HTMLResponse)
    def ops_page(request: Request, limit: int = 50):
        limit = max(1, min(limit, 200))
        with KontextDB(db_path) as req_db:
            workspace_id = _resolve_workspace(req_db)
            rows = req_db.conn.execute(
                """
                SELECT h.id, h.op_kind, h.entity_type, h.entity_id,
                       h.created_at, h.applied_at,
                       d.label AS device_label
                FROM history_ops h
                LEFT JOIN devices d ON d.id = h.device_id
                WHERE h.workspace_id = ?
                ORDER BY h.rowid DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return templates.TemplateResponse(
            request,
            "ops.html",
            {
                "workspace_id": workspace_id,
                "ops": [dict(r) for r in rows],
                "limit": limit,
                "nav_active": "ops",
            },
        )

    return router
