"""Dashboard routes for the single-admin Kontext control plane.

No auth — single-user, obscure URL. Reads straight from the local DB.
Overview / Entries / Devices / Ops pages. Entries page is the quality
check — shows file, fact, grade, tier so you can eyeball whether the
library is being written sensibly.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from db import KontextDB


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "dashboard"


def _resolve_workspace(db) -> str:
    row = db.conn.execute(
        "SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    return row["id"] if row else ""


def register_dashboard(app, db_path: str) -> None:
    """Mount the dashboard routes on `app`."""
    app.include_router(_build_router(db_path))


def _build_router(db_path: str) -> APIRouter:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = APIRouter()

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
                SELECT id, label, device_class, enrolled_at, revoked_at
                FROM devices WHERE workspace_id = ?
                ORDER BY enrolled_at ASC
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
