"""
install_hooks.py — Safely inject Kontext hooks into Claude Code settings.json

Handles:
- Creating settings.json if it doesn't exist
- Adding hooks section if missing
- Adding PreToolUse hook for cross-session sync
- Never overwrites existing hooks — only adds if missing
- Creates a backup before modifying

Usage: python install_hooks.py
"""

import json
import os
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

# PostCompact agent — saves memory before context compression erases it
KONTEXT_POSTCOMPACT = {
    "hooks": [
        {
            "type": "agent",
            "prompt": (
                "Context was just compressed. Scan the conversation summary for facts, decisions, "
                "preferences, or project status changes that should be in memory. Read MEMORY.md, "
                "write new info as atomic facts with date tags. After writing, broadcast changed "
                "files to ~/.claude/projects/_memory_broadcast. Log to ~/.claude/.kontext_agent.log: "
                "[timestamp] PostCompact: X entries saved to Y files. If nothing is memory-worthy, "
                "do nothing and do not log. Work silently."
            ),
            "model": "claude-haiku-4-5-20251001",
            "timeout": 30,
            "statusMessage": "Saving context before compression...",
        }
    ]
}

# SessionEnd agent — full pipeline: save, quality check, split, conflict scan, log
KONTEXT_SESSIONEND = {
    "hooks": [
        {
            "type": "agent",
            "prompt": (
                "This session is ending. Run this full pipeline:\n\n"
                "1. SAVE: Review conversation for facts, decisions, preferences, project statuses "
                "NOT yet in memory. Read MEMORY.md index, write missing info as atomic facts with "
                "date tags [YYYY-MM].\n\n"
                "2. CONFLICTS: Scan files you changed. Does any new entry contradict an existing "
                "entry? If yes, log to the memory directory's _conflicts.md.\n\n"
                "3. QUALITY: Grade each entry in files you touched 1-10. Delete grade 1-4. "
                "Move grade 5-7 from Active to Historical section.\n\n"
                "4. SPLIT: Check if any memory file exceeds 12,000 characters (~3000 tokens). "
                "If so, create a NEW file for the second-biggest sub-topic with proper frontmatter, "
                "move entries there, add to MEMORY.md index.\n\n"
                "5. REFRESH: Update MEMORY.md descriptions for changed files.\n\n"
                "6. BROADCAST: Echo changed filenames to ~/.claude/projects/_memory_broadcast.\n\n"
                "7. LOG: Append to ~/.claude/.kontext_agent.log: [timestamp] SessionEnd: "
                "X files changed, Y added, Z deleted, W split, C conflicts.\n\n"
                "Work silently."
            ),
            "model": "claude-haiku-4-5-20251001",
            "timeout": 90,
            "statusMessage": "Saving session memories...",
        }
    ]
}

# The UserPromptSubmit hook for cross-session memory sync
# Fires once per user message (not per tool call — no performance impact)
KONTEXT_HOOK = {
    "hooks": [
        {
            "type": "command",
            "command": (
                'SEEN="$HOME/.claude/.kontext_seen"; NOW=$(date +%s); '
                'if [ ! -f "$SEEN" ]; then '
                'echo "$NOW" > "$SEEN"; '
                'echo \'{"additionalContext":"[Kontext] Session resumed or started. Re-read MEMORY.md index and load files relevant to the current conversation."}\'; '
                'exit 0; fi; '
                'STIME=$(cat "$SEEN"); '
                'BCAST="$HOME/.claude/projects/_memory_broadcast"; '
                'if [ -f "$BCAST" ]; then '
                'BTIME=$(stat -c %Y "$BCAST" 2>/dev/null || stat -f %m "$BCAST" 2>/dev/null || echo 0); '
                'if [ "$BTIME" -gt "$STIME" ]; then '
                'CONTENT=$(cat "$BCAST" | sort -u | tr \'\\n\' \', \' | sed \'s/,$//\'); '
                'echo "$NOW" > "$SEEN"; '
                'echo "{\\"additionalContext\\":\\"[Kontext Sync] Memory updated: $CONTENT\\"}"; '
                'exit 0; fi; fi; '
                'MEMDIR=$(find "$HOME/.claude/projects" -maxdepth 2 -name \'MEMORY.md\' -path \'*/memory/*\' 2>/dev/null | head -1); '
                'if [ -n "$MEMDIR" ]; then '
                'MDIR=$(dirname "$MEMDIR"); '
                'CHANGED=$(find "$MDIR" -name \'*.md\' -newer "$SEEN" 2>/dev/null | xargs -I{} basename {} | sort -u | tr \'\\n\' \', \' | sed \'s/,$//\'); '
                'if [ -n "$CHANGED" ]; then '
                'echo "$NOW" > "$SEEN"; '
                'echo "{\\"additionalContext\\":\\"[Kontext Sync] Memory files changed since last check: $CHANGED\\"}"; '
                'exit 0; fi; fi; '
                'echo \'{"suppressOutput":true}\''
            ),
            "timeout": 2
        }
    ]
}

KONTEXT_HOOK_MARKER = ".kontext_seen"  # unique string to detect if our hook is already installed


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


def hook_already_installed(settings: dict) -> bool:
    """Check if the Kontext hook is already installed (in any hook type)."""
    hooks = settings.get("hooks", {})
    # Check both old (PreToolUse) and new (UserPromptSubmit) locations
    for hook_type in ["UserPromptSubmit", "PreToolUse"]:
        for group in hooks.get(hook_type, []):
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                if KONTEXT_HOOK_MARKER in cmd:
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

    if hook_already_installed(settings):
        print("  Kontext hooks already installed. Nothing to do.")
        return

    # Ensure hooks structure exists
    if "hooks" not in settings:
        settings["hooks"] = {}

    # Add all three Kontext hooks
    for hook_type, hook_data, label in [
        ("UserPromptSubmit", KONTEXT_HOOK, "Cross-session sync"),
        ("PostCompact", KONTEXT_POSTCOMPACT, "Post-compression memory save"),
        ("SessionEnd", KONTEXT_SESSIONEND, "End-of-session memory sweep"),
    ]:
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
