#!/usr/bin/env python3
"""
hooks/log_prompt.py — UserPromptSubmit hook: silently logs each user prompt.

No stdout output. Writes to user_prompts table for searchable session history.
Skip gate: KONTEXT_SKIP_HOOKS=1 env var.
"""
import sys
import json
import os
import time
from pathlib import Path

if os.environ.get("KONTEXT_SKIP_HOOKS"):
    sys.exit(0)

try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)

prompt = data.get("prompt", "").strip()
session_id = data.get("session_id", "")

if not prompt:
    sys.exit(0)

KONTEXT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KONTEXT_ROOT))

try:
    from db import KontextDB
    db = KontextDB()
    try:
        db.add_user_prompt(session_id=session_id, content=prompt)
    finally:
        db.close()
except Exception as exc:
    log_path = KONTEXT_ROOT / "_log_prompt.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR"
                f" {type(exc).__name__}: {exc}\n"
            )
    except OSError:
        pass

sys.exit(0)
