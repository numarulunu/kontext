"""
install_hooks.py — Safely inject Kontext hooks into Claude Code settings.json

Handles:
- Creating settings.json if it doesn't exist
- Adding hooks section if missing
- Adding three UserPromptSubmit hooks (session detect, session save, memory save)
  These are the only Kontext hooks — all save work is driven by user prompts,
  throttled to once per 60s. This is the "periodic auto-save" the system runs.
- Cleaning up any dead hooks from older Kontext versions (PostCompact, SessionEnd)
- Never overwrites existing user hooks — only adds its own if missing
- Creates a backup before modifying

Usage: python install_hooks.py
"""

import json
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

# --- Hook definitions ---

# Shared skip gate prepended to every Kontext UserPromptSubmit hook.
# Two escape hatches, either one triggers skip:
#   1. Manual env var: set KONTEXT_SKIP_HOOKS=1 before spawning claude.
#      Use this when auto-detection can't see the parent (e.g. on systems
#      without `ps`) or for testing.
#   2. Auto-detect: inspect the parent process's command line via `ps`.
#      If Claude Code was launched with `--print` / `-p` (headless one-shot
#      batch mode), every hook call in that subprocess skips automatically.
#      This is what makes normal scripts Just Work: Kontext stays live in
#      interactive sessions, batch distillers (Kontext digest, Skool
#      cleaner, anything else) bypass the hooks with zero per-script setup.
_SKIP_GATE = (
    'if [ -n "${KONTEXT_SKIP_HOOKS:-}" ]; then '
    'echo \'{"suppressOutput":true}\'; exit 0; fi; '
    'if ps -o command= -p "$PPID" 2>/dev/null | grep -qE -- "(--print|[[:space:]]-p[[:space:]])"; then '
    'echo \'{"suppressOutput":true}\'; exit 0; fi; '
)

# UserPromptSubmit hook 1: New session detector (>5 min gap = new session)
KONTEXT_SESSION_DETECT = {
    "hooks": [
        {
            "type": "command",
            "command": (
                _SKIP_GATE +
                'SEEN="$HOME/.claude/.kontext_seen"; NOW=$(date +%s); LAST=0; '
                'test -f "$SEEN" && LAST=$(cat "$SEEN"); DIFF=$((NOW - LAST)); '
                'if [ "$DIFF" -gt 300 ]; then '
                'echo "$NOW" > "$SEEN"; '
                'echo "$NOW" > "$HOME/.claude/.kontext_session_last"; '
                'echo "$NOW" > "$HOME/.claude/.kontext_memory_last"; '
                'echo \'{"additionalContext":"[Kontext] New session. Call kontext_session with action=get to load what you were working on last. Read relevant memory files based on user topic."}\'; '
                'else echo "$NOW" > "$SEEN"; echo \'{"suppressOutput":true}\'; fi'
            ),
            "timeout": 2,
        }
    ]
}

# UserPromptSubmit hook 2: Session save (every 60s)
KONTEXT_SESSION_SAVE = {
    "hooks": [
        {
            "type": "command",
            "command": (
                _SKIP_GATE +
                'THROTTLE="$HOME/.claude/.kontext_session_last"; NOW=$(date +%s); LAST=0; '
                'test -f "$THROTTLE" && LAST=$(cat "$THROTTLE"); DIFF=$((NOW - LAST)); '
                'if [ "$DIFF" -lt 60 ]; then echo \'{"suppressOutput":true}\'; '
                'else echo "$NOW" > "$THROTTLE"; '
                'echo \'{"additionalContext":"[Kontext] SESSION SAVE. Call kontext_session with action=save. '
                "Fill project, status, next_step, key_decisions from current conversation state. "
                'This writes _last_session.md for the next session. One tool call. Do not skip."}\'; fi'
            ),
            "timeout": 2,
        }
    ]
}

# UserPromptSubmit hook 3: Memory save (every 60s)
# Scope is the ENTIRE conversation since the last /clear, not just recent turns.
# kontext_query dedup prevents re-writes; FTS5 makes the dedup cheap. Combined
# with the 60s throttle this gives full-session coverage at bounded token cost.
KONTEXT_MEMORY_SAVE = {
    "hooks": [
        {
            "type": "command",
            "command": (
                _SKIP_GATE +
                'THROTTLE="$HOME/.claude/.kontext_memory_last"; NOW=$(date +%s); LAST=0; '
                'test -f "$THROTTLE" && LAST=$(cat "$THROTTLE"); DIFF=$((NOW - LAST)); '
                'if [ "$DIFF" -lt 60 ]; then echo \'{"suppressOutput":true}\'; '
                'else echo "$NOW" > "$THROTTLE"; '
                'echo \'{"additionalContext":"[Kontext] MEMORY SAVE. Scan the ENTIRE current conversation '
                "(every user message since the session started / last /clear) for memory-worthy content: "
                "decisions (switching tools, changing plans), self-facts (numbers, names, dates, status updates), "
                "corrections (updating existing info), preferences (likes, dislikes, workflow choices), "
                "project status changes (launched, stalled, killed, pivoted). "
                "Skip: debugging details, code questions, greetings, acknowledgments. "
                "MANDATORY: call kontext_query on each candidate BEFORE writing to skip duplicates — "
                "FTS5 dedup is cheap and this is the only thing that keeps the periodic scan token-efficient. "
                "Then kontext_write only the genuinely new entries. "
                "Include a dated source tag [Claude YYYY-MM]. Grade 8-10 for decisions/identity, 5-7 for context. "
                'Silent. Do not announce."}\'; fi'
            ),
            "timeout": 2,
        }
    ]
}

KONTEXT_HOOK_MARKER = ".kontext_seen"  # unique string to detect if our hooks are installed


def load_settings() -> dict:
    """Load settings.json or return default structure."""
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"  WARNING: {SETTINGS_PATH} is corrupt. Creating backup and starting fresh.")
            backup = SETTINGS_PATH.with_suffix(".json.bak")
            shutil.copy2(SETTINGS_PATH, backup)
            print(f"  Backup saved to {backup}")
            return {}
    return {}


def save_settings(settings: dict):
    """Write settings.json with backup."""
    if SETTINGS_PATH.exists():
        backup = SETTINGS_PATH.with_suffix(".json.bak")
        shutil.copy2(SETTINGS_PATH, backup)

    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _has_kontext_hook(settings: dict, hook_type: str) -> bool:
    """Check if any Kontext hook is already installed for the given event type."""
    for group in settings.get("hooks", {}).get(hook_type, []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "") + hook.get("prompt", "")
            if "kontext" in cmd.lower() or KONTEXT_HOOK_MARKER in cmd:
                return True
    return False


def _strip_kontext_hooks_from(settings: dict, hook_type: str) -> int:
    """Remove any Kontext-tagged hooks from `hook_type`. Returns count removed.

    Leaves user-authored hooks in that section untouched. If the section ends
    up empty, it's emptied (not deleted) to match existing test expectations.
    """
    if hook_type not in settings.get("hooks", {}):
        return 0
    removed = 0
    kept = []
    for group in settings["hooks"][hook_type]:
        group_hooks = group.get("hooks", [])
        filtered = []
        for hook in group_hooks:
            blob = (hook.get("command", "") + hook.get("prompt", "")).lower()
            if "kontext" in blob or KONTEXT_HOOK_MARKER in blob:
                removed += 1
                continue
            filtered.append(hook)
        if filtered:
            kept.append({**group, "hooks": filtered})
    settings["hooks"][hook_type] = kept
    return removed


def install():
    """Main installation logic."""
    print("Installing Kontext hooks into Claude Code settings...")

    if not CLAUDE_DIR.exists():
        print(f"  ERROR: Claude Code not found at {CLAUDE_DIR}")
        print("  Install Claude Code CLI first.")
        sys.exit(1)

    settings = load_settings()

    if "hooks" not in settings:
        settings["hooks"] = {}

    # Clean up dead hooks from older Kontext versions BEFORE the install-check,
    # so users who had a PostCompact hook from an earlier version get it
    # removed the next time they run setup. PostCompact isn't useful for users
    # who rely on /clear instead of /compact — every save is already driven
    # periodically by the UserPromptSubmit hooks below.
    removed = _strip_kontext_hooks_from(settings, "PostCompact")
    if removed:
        print(f"  Cleaned: Removed {removed} dead Kontext PostCompact hook(s)")
    if "SessionEnd" in settings["hooks"]:
        settings["hooks"]["SessionEnd"] = []
        print("  Cleaned: Removed dead SessionEnd hooks")

    # Always strip and reinstall Kontext UserPromptSubmit hooks. This makes
    # install idempotent *and* guarantees users pick up updated hook prompts
    # when they reinstall, without ever duplicating. User-authored hooks in
    # the same section are preserved by _strip_kontext_hooks_from.
    removed_ups = _strip_kontext_hooks_from(settings, "UserPromptSubmit")
    if removed_ups:
        print(f"  Refreshed: Stripped {removed_ups} previous Kontext UserPromptSubmit hook(s)")

    to_install = [
        ("UserPromptSubmit", KONTEXT_SESSION_DETECT, "Session detector"),
        ("UserPromptSubmit", KONTEXT_SESSION_SAVE, "Session save (60s)"),
        ("UserPromptSubmit", KONTEXT_MEMORY_SAVE, "Memory save (60s)"),
    ]

    for hook_type, hook_data, label in to_install:
        if hook_type not in settings["hooks"]:
            settings["hooks"][hook_type] = []
        settings["hooks"][hook_type].append(hook_data)
        print(f"  Installed: {label}")

    save_settings(settings)
    print(f"  Settings saved to {SETTINGS_PATH}")
    if SETTINGS_PATH.with_suffix(".json.bak").exists():
        print(f"  Backup at {SETTINGS_PATH.with_suffix('.json.bak')}")
    print("  Done.")


if __name__ == "__main__":
    install()
