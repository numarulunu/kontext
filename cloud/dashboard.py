"""Dashboard mount for the Kontext control plane.

Serves the React SPA in `static_dashboard/` at the app root. A dynamic
`/data.js` route is registered before the static mount so the SPA reads
live data from SQLite instead of the bundled mock payload.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Response
from fastapi.staticfiles import StaticFiles

from cloud.dashboard_snapshot import build_snapshot


STATIC_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "static_dashboard"
log = logging.getLogger(__name__)


def register_dashboard(app, db_path: str) -> None:
    """Mount the SPA at the app root and expose a live `/data.js` feed."""

    @app.get("/data.js", include_in_schema=False)
    def data_js() -> Response:
        try:
            payload = build_snapshot(db_path)
        except Exception:
            log.exception("dashboard snapshot failed")
            payload = {"error": "snapshot_failed"}
        body = f"window.KONTEXT_DATA = {json.dumps(payload)};"
        return Response(
            content=body,
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    if STATIC_DASHBOARD_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(STATIC_DASHBOARD_DIR), html=True),
            name="dashboard_ui",
        )
