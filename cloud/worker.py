"""Background scheduler: runs dream + digest phases on a loop.

Shares the same SQLite volume as cloud.server so canonical revisions produced
by dream (dedup/normalize/resolve/compress/purge) and imports produced by
digest are picked up on the next sync by every enrolled device.

Run with: `python -m cloud.worker`

Environment variables:
    KONTEXT_DB_PATH           Path to the SQLite database (default: /app/data/kontext.db)
    DREAM_INTERVAL_HOURS      Hours between dream runs (default: 24, 0 disables)
    DIGEST_INTERVAL_HOURS     Hours between digest runs (default: 6, 0 disables)
    DREAM_DRY_RUN             1 to run dream in dry-run mode (default: 0)
    WORKER_STARTUP_DELAY_SEC  Sleep before first cycle (default: 30)
"""
import os
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import KontextDB


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[kontext.worker {_now()}] {msg}", flush=True)


def run_dream(db_path: str, dry_run: bool) -> None:
    from dream import dream

    _log(f"dream start dry_run={dry_run}")
    with KontextDB(db_path) as db:
        results = dream(db, dry_run=dry_run)
    _log(f"dream done phases={list(results)}")


def run_digest(db_path: str) -> None:
    from digest import process_digests

    _log("digest start")
    results = process_digests(auto=True, dry_run=False)
    _log(
        f"digest done files={results.get('files_processed', 0)} "
        f"candidates={results.get('candidates_found', 0)} "
        f"imported={results.get('imported', 0)}"
    )


def main() -> int:
    db_path = _env("KONTEXT_DB_PATH", "/app/data/kontext.db")
    dream_interval_h = _env_float("DREAM_INTERVAL_HOURS", 24.0)
    digest_interval_h = _env_float("DIGEST_INTERVAL_HOURS", 6.0)
    dream_dry_run = _env("DREAM_DRY_RUN", "0") in ("1", "true", "yes")
    startup_delay = _env_int("WORKER_STARTUP_DELAY_SEC", 30)

    _log(
        f"starting db={db_path} dream_interval={dream_interval_h}h "
        f"digest_interval={digest_interval_h}h dream_dry_run={dream_dry_run}"
    )

    if startup_delay > 0:
        _log(f"startup delay {startup_delay}s")
        time.sleep(startup_delay)

    last_dream = 0.0
    last_digest = 0.0
    tick = 60.0

    while True:
        now = time.time()

        if dream_interval_h > 0 and (now - last_dream) >= dream_interval_h * 3600:
            try:
                run_dream(db_path, dry_run=dream_dry_run)
            except Exception as exc:
                _log(f"dream failed: {exc}")
                traceback.print_exc()
            last_dream = time.time()

        if digest_interval_h > 0 and (now - last_digest) >= digest_interval_h * 3600:
            try:
                run_digest(db_path)
            except Exception as exc:
                _log(f"digest failed: {exc}")
                traceback.print_exc()
            last_digest = time.time()

        time.sleep(tick)


if __name__ == "__main__":
    sys.exit(main())
