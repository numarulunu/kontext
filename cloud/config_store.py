"""Persistent settings for the dashboard (e.g. ANTHROPIC_API_KEY).

Stored as JSON at `/app/data/dashboard_config.json` (same volume as the
SQLite DB, so it survives container restarts). Writes are atomic and
serialized behind a lock. Sits BEHIND the Pangolin SSO gate, so no
in-app auth is required — unauthenticated requests never reach here.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_DEFAULT = Path(os.environ.get("KONTEXT_DATA_DIR", "/app/data")) / "dashboard_config.json"
_lock = threading.Lock()


def _path() -> Path:
    return _DEFAULT


def load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save(cfg: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)


DEFAULT_MODEL = "claude-haiku-4-5"


def get_anthropic_api_key() -> str | None:
    """Read key: config file wins, env var as fallback. Returns None if neither set."""
    cfg = load()
    key = (cfg.get("anthropic_api_key") or "").strip()
    if key:
        return key
    env_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return env_key or None


def get_anthropic_base_url() -> str | None:
    """Custom base URL (e.g. OmniRoute). Empty/None → SDK default (api.anthropic.com)."""
    cfg = load()
    url = (cfg.get("anthropic_base_url") or "").strip()
    if url:
        return url
    env_url = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()
    return env_url or None


def get_anthropic_model() -> str:
    cfg = load()
    model = (cfg.get("anthropic_model") or "").strip()
    return model or DEFAULT_MODEL


def set_anthropic_api_key(key: str | None) -> None:
    _set_scalar("anthropic_api_key", key)


def set_anthropic_base_url(url: str | None) -> None:
    _set_scalar("anthropic_base_url", url)


def set_anthropic_model(model: str | None) -> None:
    _set_scalar("anthropic_model", model)


def _set_scalar(field: str, value: str | None) -> None:
    cfg = load()
    if value and str(value).strip():
        cfg[field] = str(value).strip()
    else:
        cfg.pop(field, None)
    save(cfg)


def mask_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "•" * len(key)
    return f"{key[:7]}…{key[-4:]}"
