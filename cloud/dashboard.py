"""Dashboard routes for the single-admin Kontext control plane.

Workspace-token gated. Read-only in v1: overview, devices, ops feed.
Cookie-based session via itsdangerous, 30-day TTL, HttpOnly + Secure.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from cloud.auth import verify_workspace_token
from db import KontextDB


class NotAuthenticated(Exception):
    """Raised by dashboard auth dependency to signal a redirect to /dashboard/login."""


SESSION_COOKIE = "kontext_session"
SESSION_MAX_AGE_SEC = 60 * 60 * 24 * 30  # 30 days
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "dashboard"


def _require_secret() -> str:
    secret = os.environ.get("KONTEXT_SESSION_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "KONTEXT_SESSION_SECRET is required to run the dashboard"
        )
    return secret


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_require_secret(), salt="kontext.dashboard")


def _sign_session(workspace_id: str) -> str:
    return _signer().dumps({"workspace_id": workspace_id})


def _load_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = _signer().loads(token, max_age=SESSION_MAX_AGE_SEC)
        return data.get("workspace_id")
    except (BadSignature, SignatureExpired):
        return None


def _valid_workspace_token(db, workspace_id: str, token: str) -> bool:
    record = db.get_workspace_token_record(workspace_id)
    if not record:
        return False
    return bool(verify_workspace_token(token, record["salt"], record["hash"]))


def _current_workspace(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> str:
    workspace_id = _load_session(session)
    if not workspace_id:
        raise NotAuthenticated()
    return workspace_id


def register_dashboard(app, db_path: str) -> None:
    """Mount the dashboard routes + NotAuthenticated redirect handler on `app`."""
    _require_secret()

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request, exc):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    app.include_router(_build_router(db_path))


def _build_router(db_path: str) -> APIRouter:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = APIRouter()

    @router.get("/dashboard/login", response_class=HTMLResponse)
    def login_form(request: Request, error: str | None = None):
        return templates.TemplateResponse(
            request, "login.html", {"error": error},
        )

    @router.post("/dashboard/login")
    def login_submit(
        workspace_id: str = Form(...),
        workspace_token: str = Form(...),
    ):
        with KontextDB(db_path) as req_db:
            if not _valid_workspace_token(req_db, workspace_id.strip(), workspace_token.strip()):
                return RedirectResponse(
                    url="/dashboard/login?error=invalid",
                    status_code=303,
                )
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=_sign_session(workspace_id.strip()),
            max_age=SESSION_MAX_AGE_SEC,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @router.post("/dashboard/logout")
    def logout():
        response = RedirectResponse(url="/dashboard/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/dashboard", response_class=HTMLResponse)
    def overview(
        request: Request,
        workspace_id: str = Depends(_current_workspace),
    ):
        with KontextDB(db_path) as req_db:
            conn = req_db.conn
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
                "last_op_at": last_op["created_at"] if last_op else None,
                "nav_active": "overview",
            },
        )

    @router.get("/dashboard/devices", response_class=HTMLResponse)
    def devices_page(
        request: Request,
        workspace_id: str = Depends(_current_workspace),
    ):
        with KontextDB(db_path) as req_db:
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
    def ops_page(
        request: Request,
        workspace_id: str = Depends(_current_workspace),
        limit: int = 50,
    ):
        limit = max(1, min(limit, 200))
        with KontextDB(db_path) as req_db:
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
