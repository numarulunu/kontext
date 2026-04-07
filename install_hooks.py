"""
install_hooks.py — Safely inject Kontext hooks into Claude Code settings.json

Handles:
- Creating settings.json if it doesn't exist
- Adding hooks section if missing
- Adding three UserPromptSubmit hooks (session detect, session save, memory save)
- Adding PostCompact hook (direct Python via Bash, no MCP dependency)
- Never overwrites existing hooks — only adds if missing
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

# PostCompact agent — saves memory via direct Python calls (no MCP dependency)
KONTEXT_POSTCOMPACT = {
    "hooks": [
        {
            "type": "agent",
            "prompt": (
                "Context was just compressed. Save state NOW before details are lost.\n\n"
                "1. From the conversation summary, determine: project name, current status, next step, key decisions, "
                "a 2-3 sentence conversation summary, and comma-separated list of files edited/discussed.\n"
                "2. Get today's date by running via Bash: date +%Y-%m\n"
                '3. Save session — run via Bash tool (replace placeholders with real values, escape single quotes):\n'
                '   cd "$HOME/Desktop/Claude/Kontext" && python -c "from db import KontextDB; db = KontextDB(); '
                "db.save_session(project='PROJECT', status='STATUS', next_step='NEXT', key_decisions='DECISIONS', "
                "summary='SUMMARY', files_touched='FILES')\"\n"
                "4. For any unsaved facts worth remembering, save each via Bash tool (use the date from step 2 for SOURCE):\n"
                '   cd "$HOME/Desktop/Claude/Kontext" && python -c "from db import KontextDB; db = KontextDB(); '
                "db.add_entry(file='FILENAME.md', fact='THE FACT', source='[Claude YYYY-MM]', grade=7, tier='active')\"\n\n"
                "Use Bash tool directly. Do NOT use MCP tools — they may not be available in this context. Work silently."
            ),
            "model": HAIKU_MODEL,
            "timeout": 45,
            "statusMessage": "Saving context before compression...",
        }
    ]
}

# UserPromptSubmit hook 1: New session detector (>5 min gap = new session)
KONTEXT_SESSION_DETECT = {
    "hooks": [
        {
            "type": "command",
            "command": (
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
# Model used by PostCompact agent hook — update when Anthropic deprecates
HAIKU_MODEL = "claude-haiku-4-5-20251001"

KONTEXT_MEMORY_SAVE = {
    "hooks": [
        {
            "type": "command",
            "command": (
                'THROTTLE="$HOME/.claude/.kontext_memory_last"; NOW=$(date +%s); LAST=0; '
                'test -f "$THROTTLE" && LAST=$(cat "$THROTTLE"); DIFF=$((NOW - LAST)); '
                'if [ "$DIFF" -lt 60 ]; then echo \'{"suppressOutput":true}\'; '
                'else echo "$NOW" > "$THROTTLE"; '
                'echo \'{"additionalContext":"[Kontext] MEMORY SAVE. Check the last few user messages for: '
                "decisions (switching tools, changing plans), self-facts (numbers, names, dates, status updates), "
                "corrections (updating existing info), preferences (likes, dislikes, workflow choices), "
                "project status changes (launched, stalled, killed, pivoted). "
                "Skip: debugging details, code questions, greetings, acknowledgments. "
                "Use kontext_query to check for duplicates, then kontext_write for genuinely new entries. "
                "Include a dated source tag [Claude YYYY-MM]. Grade 8-10 for decisions/identity, 5-7 for context. "
                'Silent."}\'; fi'
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


def install():
    """Main installation logic."""
    print("Installing Kontext hooks into Claude Code settings...")

    if not CLAUDE_DIR.exists():
        print(f"  ERROR: Claude Code not found at {CLAUDE_DIR}")
        print("  Install Claude Code CLI first.")
        sys.exit(1)

    settings = load_settings()

    userprompt_installed = _has_kontext_hook(settings, "UserPromptSubmit")
    postcompact_installed = _has_kontext_hook(settings, "PostCompact")

    if userprompt_installed and postcompact_installed:
        print("  Kontext hooks already installed. Nothing to do.")
        return

    if "hooks" not in settings:
        settings["hooks"] = {}

    to_install = []
    if not userprompt_installed:
        to_install.append(("UserPromptSubmit", KONTEXT_SESSION_DETECT, "Session detector"))
        to_install.append(("UserPromptSubmit", KONTEXT_SESSION_SAVE, "Session save (60s)"))
        to_install.append(("UserPromptSubmit", KONTEXT_MEMORY_SAVE, "Memory save (60s)"))
    if not postcompact_installed:
        to_install.append(("PostCompact", KONTEXT_POSTCOMPACT, "Post-compression save (Bash, no MCP)"))

    for hook_type, hook_data, label in to_install:
        if hook_type not in settings["hooks"]:
            settings["hooks"][hook_type] = []
        settings["hooks"][hook_type].append(hook_data)
        print(f"  Installed: {label}")

    # Clean up dead SessionEnd hooks
    if "SessionEnd" in settings["hooks"]:
        settings["hooks"]["SessionEnd"] = []
        print("  Cleaned: Removed dead SessionEnd hooks")

    save_settings(settings)
    print(f"  Settings saved to {SETTINGS_PATH}")
    if SETTINGS_PATH.with_suffix(".json.bak").exists():
        print(f"  Backup at {SETTINGS_PATH.with_suffix('.json.bak')}")
    print("  Done.")


if __name__ == "__main__":
    install()
