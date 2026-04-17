"""Uvicorn entrypoint for the Kontext cloud control plane.

Run with: `python -m cloud.server`

Environment variables:
    KONTEXT_DB_PATH   Path to the SQLite database (default: /app/data/kontext.db)
    KONTEXT_HOST      Bind address (default: 0.0.0.0)
    KONTEXT_PORT      Bind port (default: 8080)
    KONTEXT_LOG_LEVEL Uvicorn log level (default: info)
"""
import os
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.api import build_app
from db import KontextDB


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def main() -> int:
    db_path = _env("KONTEXT_DB_PATH", "/app/data/kontext.db")
    host = _env("KONTEXT_HOST", "0.0.0.0")
    port = int(_env("KONTEXT_PORT", "8080"))
    log_level = _env("KONTEXT_LOG_LEVEL", "info").lower()

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with KontextDB(db_path) as db:
        app = build_app(db)

    print(f"[kontext.server] db={db_path} bind={host}:{port} log_level={log_level}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
