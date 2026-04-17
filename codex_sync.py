"""
Kontext Codex Sync — Wire Kontext into Codex CLI.

Produces three files:
  ~/.codex/config.toml   — MCP entry for Kontext
  ~/.codex/hooks.json    — Codex-native Kontext hooks (best effort; disabled by Codex on Windows)
  ~/.codex/AGENTS.md     — Portable rules from CLAUDE.md + Codex continuity protocol
  ~/.codex/skills/*      — Migrated Claude user/plugin skills with Codex metadata

Usage:
    python codex_sync.py              # Full sync
    python codex_sync.py --dry-run    # Preview changes, write nothing
    python codex_sync.py --check      # Exit 1 if CLAUDE.md has drifted since last sync
"""

import hashlib
import json
import logging
import logging.handlers
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.3"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_FILE = Path(__file__).parent / "_codex_sync.log"
log = logging.getLogger("kontext.codex_sync")
if not log.handlers:
    log.setLevel(logging.INFO)
    _h = logging.handlers.RotatingFileHandler(
        str(_LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_h)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

KONTEXT_DIR = Path(__file__).resolve().parent
MCP_SERVER   = KONTEXT_DIR / "mcp_server.py"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CLAUDE_MD    = Path.home() / ".claude" / "CLAUDE.md"
CLAUDE_SKILLS = Path.home() / ".claude" / "skills"
CLAUDE_PLUGINS = Path.home() / ".claude" / "plugins"
CODEX_DIR    = Path.home() / ".codex"
CODEX_CONFIG = CODEX_DIR / "config.toml"
CODEX_HOOKS  = CODEX_DIR / "hooks.json"
CODEX_HOOK_SCRIPT = CODEX_DIR / "kontext_hooks.py"
CODEX_AGENTS = CODEX_DIR / "AGENTS.md"
CODEX_SKILLS = CODEX_DIR / "skills"
CODEX_META   = CODEX_DIR / ".kontext-sync-meta.json"

# MCP plugins the user wants available in Codex even if they are installed in
# Claude's marketplace cache but not enabled in Claude settings.
CODEX_REQUESTED_PLUGIN_MCPS = {"serena"}

# ---------------------------------------------------------------------------
# AGENTS.md translation config
# ---------------------------------------------------------------------------

# Top-level CLAUDE.md sections to drop entirely (Claude-specific logic)
_DROP_SECTIONS = {"STARTUP (silent)", "KONTEXT (memory)"}

# Bullet/line patterns to strip from partial sections
_STRIP_PATTERNS = [
    r"/model\b",
    r"/compact\b",
    r"/rewind\b",
    r"\bSerena\b",
    r"\bserena\b",
    r"find_symbol|get_symbols_overview|find_referencing_symbols|replace_symbol_body|insert_after_symbol",
    r"kontext_session|kontext_write|kontext_query",
]
_STRIP_RE = re.compile("|".join(_STRIP_PATTERNS))

_FEATURES_RE = re.compile(r"(?ms)^\[features\]\s*\n.*?(?=^\[|\Z)")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_codex_dir() -> None:
    CODEX_DIR.mkdir(parents=True, exist_ok=True)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _strip_mcp_tables(text: str, names: list[str]) -> tuple[str, int]:
    """Remove legacy/current MCP tables so sync can rewrite clean managed entries."""
    if not names:
        return text, 0
    escaped = "|".join(re.escape(name) for name in names)
    pattern = re.compile(
        rf"(?ms)^\[(?:mcp_servers|mcp\.servers)\.(?:{escaped})(?:\.[^\]]+)?\]\s*\n.*?(?=^\[|\Z)"
    )
    return pattern.subn("", text)


def _ensure_feature_flag(text: str) -> str:
    """Enable Codex hooks for non-Windows/future support without clobbering config."""
    match = _FEATURES_RE.search(text)
    if not match:
        return text.rstrip() + "\n\n[features]\ncodex_hooks = true\n"

    block = match.group(0)
    if re.search(r"(?m)^codex_hooks\s*=", block):
        new_block = re.sub(r"(?m)^codex_hooks\s*=.*$", "codex_hooks = true", block)
    else:
        new_block = block.rstrip() + "\ncodex_hooks = true\n"
    return text[:match.start()] + new_block + text[match.end():]


def _read_enabled_plugins() -> set[str]:
    """Return enabled Claude plugin names from ~/.claude/settings.json."""
    if not CLAUDE_SETTINGS.exists():
        return set()
    try:
        data = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(f"CODEX_SYNC: could not read Claude settings: {type(exc).__name__}: {exc}")
        return set()

    enabled = data.get("enabledPlugins", {})
    names = set()
    for plugin_id, is_enabled in enabled.items():
        if is_enabled:
            names.add(plugin_id.split("@", 1)[0])
    return names


def _enabled_plugin_cache_dirs() -> list[Path]:
    enabled = _read_enabled_plugins()
    cache_dir = CLAUDE_PLUGINS / "cache"
    if not enabled or not cache_dir.exists():
        return []

    dirs = []
    for marketplace_dir in sorted(p for p in cache_dir.iterdir() if p.is_dir()):
        for plugin_dir in sorted(p for p in marketplace_dir.iterdir() if p.is_dir() and p.name in enabled):
            dirs.extend(sorted(p for p in plugin_dir.iterdir() if p.is_dir()))
    return dirs


def _active_plugin_mcp_servers() -> dict[str, dict]:
    """Read .mcp.json files from enabled Claude plugins for Codex MCP mirroring."""
    servers: dict[str, dict] = {}
    for plugin_dir in _enabled_plugin_cache_dirs():
        mcp_file = plugin_dir / ".mcp.json"
        if not mcp_file.exists():
            continue
        try:
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(f"CODEX_SYNC: could not read {mcp_file}: {type(exc).__name__}: {exc}")
            continue
        for name, config in data.items():
            if isinstance(config, dict) and "command" in config:
                servers[name] = config

    marketplace_external = CLAUDE_PLUGINS / "marketplaces" / "claude-plugins-official" / "external_plugins"
    for plugin_name in sorted(CODEX_REQUESTED_PLUGIN_MCPS):
        mcp_file = marketplace_external / plugin_name / ".mcp.json"
        if not mcp_file.exists():
            continue
        try:
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(f"CODEX_SYNC: could not read {mcp_file}: {type(exc).__name__}: {exc}")
            continue
        for name, config in data.items():
            if isinstance(config, dict) and "command" in config:
                servers.setdefault(name, config)
    return servers


# ---------------------------------------------------------------------------
# 1. MCP config
# ---------------------------------------------------------------------------

def sync_mcp_config(dry_run: bool = False) -> str:
    """Write/merge managed MCP entries into ~/.codex/config.toml."""
    server_path = str(MCP_SERVER).replace("\\", "/")
    cwd_path = str(KONTEXT_DIR).replace("\\", "/")
    entries = {
        "kontext": (
        "[mcp_servers.kontext]\n"
        "command = \"python\"\n"
        f"args = [{_toml_string(server_path)}]\n"
        f"cwd = {_toml_string(cwd_path)}\n"
        "enabled = true\n"
        )
    }

    for name, config in _active_plugin_mcp_servers().items():
        args = config.get("args", [])
        if not isinstance(args, list):
            args = []
        entry = (
            f"[mcp_servers.{name}]\n"
            f"command = {_toml_string(str(config['command']))}\n"
            f"args = [{', '.join(_toml_string(str(arg)) for arg in args)}]\n"
            "enabled = true\n"
        )
        if isinstance(config.get("env"), dict):
            for key, value in sorted(config["env"].items()):
                entry += f"env.{key} = {_toml_string(str(value))}\n"
        entries[name] = entry

    existing = ""
    if CODEX_CONFIG.exists():
        existing = CODEX_CONFIG.read_text(encoding="utf-8")

    managed_names = sorted(entries)
    stripped, removed = _strip_mcp_tables(existing, managed_names)
    updated = _ensure_feature_flag(stripped).rstrip() + "\n\n" + "\n".join(entries[name] for name in managed_names)

    if updated == existing:
        return "config.toml: managed MCP entries already current"

    if dry_run:
        action = "repair" if removed else "add"
        return f"config.toml: would {action} {len(managed_names)} managed MCP entrie(s): {', '.join(managed_names)}"

    _ensure_codex_dir()
    CODEX_CONFIG.write_text(updated, encoding="utf-8")
    log.info(f"CODEX_SYNC: wrote config.toml MCP entries — {', '.join(managed_names)}")
    return f"config.toml: wrote {len(managed_names)} managed MCP entrie(s): {', '.join(managed_names)}"


# ---------------------------------------------------------------------------
# 2. Hooks
# ---------------------------------------------------------------------------

_HOOK_SCRIPT = r'''#!/usr/bin/env python
"""
Kontext Codex hooks.

Codex currently disables hooks on Windows, but this script is portable and ready
for WSL/Linux/macOS or future Windows support. It prints Codex-native
hookSpecificOutput JSON when it has context to add.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


HOME = Path.home()
KONTEXT_DIR = Path(__file__).resolve().parents[1] / "Desktop" / "Claude" / "Kontext"
LOG_FILE = KONTEXT_DIR / "_codex_hooks.log"


def log(message):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        pass


def emit(event, message):
    if not message.strip():
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message.strip(),
        }
    }))


def read_input():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log(f"bad hook input: {type(exc).__name__}: {exc}")
        return {}


def throttle(name, seconds):
    marker = HOME / ".codex" / name
    now = int(time.time())
    try:
        last = int(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
    except Exception:
        last = 0
    if now - last < seconds:
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
    except Exception as exc:
        log(f"throttle write failed: {type(exc).__name__}: {exc}")
    return True


def sync_flat_files():
    script = KONTEXT_DIR / "sync.py"
    if not script.exists():
        return
    try:
        subprocess.run([sys.executable, str(script)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20, check=False)
    except Exception as exc:
        log(f"sync.py failed: {type(exc).__name__}: {exc}")


def memory_dir():
    root = HOME / ".claude" / "projects"
    if not root.exists():
        return None
    best = None
    best_count = -1
    for candidate in root.glob("*/memory"):
        try:
            count = len(list(candidate.glob("*.md")))
        except Exception:
            continue
        if count > best_count:
            best = candidate
            best_count = count
    return best


def recent_memory_files():
    base = memory_dir()
    if not base:
        return []
    cutoff = time.time() - 86400
    names = []
    for path in base.glob("*.md"):
        if path.name == "MEMORY.md":
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                names.append(path.name)
        except Exception:
            continue
    return sorted(names)[:8]


def pending_flags():
    checks = [
        (HOME / "Desktop" / "Claude" / "Backup System" / "_digest-pending",
         "Daily digest is pending."),
        (HOME / "Desktop" / "Claude" / "Kontext" / "_processing-ready",
         "Intake chunks are ready."),
        (HOME / "Desktop" / "Claude" / "Tool Auditor" / "_audit-pending",
         "Tool audit is pending."),
    ]
    messages = []
    for path, label in checks:
        if path.exists():
            messages.append(label)
    return messages


def session_start():
    sync_flat_files()
    parts = [
        "[Kontext] Continuity active. First call mcp__kontext__kontext_session with "
        'action=get and workspace="<current repo root or cwd>". '
        "Do not import task state from a global latest-session note.",
    ]
    recent = recent_memory_files()
    if recent:
        parts.append("Memory files changed in the last 24h: " + ", ".join(recent))
    flags = pending_flags()
    if flags:
        parts.append("Pending Kontext work: " + " ".join(flags))
    emit("SessionStart", "\n\n".join(parts))


def user_prompt_submit():
    if not throttle(".kontext_user_prompt_last", 60):
        return
    emit(
        "UserPromptSubmit",
        "[Kontext] Before ending this turn after meaningful work, call "
        "mcp__kontext__kontext_session action=save with project, status, next_step, "
        "key_decisions, summary, and files_touched. For durable user/project facts, "
        "call kontext_query first, then kontext_write only new facts.",
    )


def main():
    if os.environ.get("CODEX_SKIP_HOOKS"):
        return
    payload = read_input()
    event = payload.get("hook_event_name", "")
    if event == "SessionStart":
        session_start()
    elif event == "UserPromptSubmit":
        user_prompt_submit()


if __name__ == "__main__":
    main()
'''


def _hook_command() -> str:
    script = str(CODEX_HOOK_SCRIPT).replace("\\", "/")
    return f"python {_toml_string(script)}"


def _kontext_hook_group(group: dict) -> bool:
    markers = (
        "kontext_hooks.py",
        "CODEX_SKIP_HOOKS",
        "Desktop/Claude/Kontext",
        "Desktop/Claude/Backup System",
        "Desktop/Claude/Tool Auditor",
        ".claude/.kontext",
        ".claude/_last_session.md",
        "[Kontext]",
        "kontext_session",
        "kontext_query",
        "kontext_write",
    )
    for hook in group.get("hooks", []):
        command = hook.get("command", "")
        if any(marker in command for marker in markers):
            return True
    return False


def _codex_hooks() -> dict:
    command = _hook_command()
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume",
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 10,
                        "statusMessage": "Loading Kontext",
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 5,
                    }
                ],
            }
        ],
    }


def sync_hooks(dry_run: bool = False) -> str:
    """Write Codex-native Kontext hooks for platforms where Codex enables hooks."""
    codex_hooks = _codex_hooks()

    # Merge into existing hooks.json without clobbering other events
    output: dict = {"hooks": {}}
    if CODEX_HOOKS.exists():
        try:
            output = json.loads(CODEX_HOOKS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    for event, groups in codex_hooks.items():
        existing_groups = output.setdefault("hooks", {}).get(event, [])
        kept = [group for group in existing_groups if not _kontext_hook_group(group)]
        output["hooks"][event] = kept + groups

    hook_count = sum(len(v) for v in codex_hooks.values())
    if dry_run:
        return f"hooks.json: would write {hook_count} Codex-native Kontext hooks and helper script"

    _ensure_codex_dir()
    CODEX_HOOK_SCRIPT.write_text(_HOOK_SCRIPT, encoding="utf-8")
    CODEX_HOOKS.write_text(json.dumps(output, indent=2), encoding="utf-8")
    log.info(f"CODEX_SYNC: wrote hooks.json — {hook_count} hooks")
    return f"hooks.json: wrote {hook_count} Codex-native Kontext hooks and helper script"


# ---------------------------------------------------------------------------
# 3. AGENTS.md
# ---------------------------------------------------------------------------

def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Split text into [(heading, full_block)] pairs on ## headings."""
    sections: list[tuple[str, str]] = []
    current_heading = "__preamble__"
    current_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if current_lines:
                sections.append((current_heading, "".join(current_lines)))
            current_heading = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, "".join(current_lines)))
    return sections


def _filter_lines(body: str) -> tuple[str, list[str]]:
    """Strip lines matching _STRIP_RE. Returns (kept_body, stripped_lines)."""
    kept, stripped = [], []
    for line in body.splitlines(keepends=True):
        if _STRIP_RE.search(line):
            stripped.append(line.rstrip())
        else:
            kept.append(line)
    return "".join(kept), stripped


def sync_agents_md(dry_run: bool = False) -> str:
    """Translate ~/.claude/CLAUDE.md → ~/.codex/AGENTS.md."""
    if not CLAUDE_MD.exists():
        return "AGENTS.md: ~/.claude/CLAUDE.md not found — skipped"

    sections = _parse_sections(CLAUDE_MD.read_text(encoding="utf-8"))

    portable: list[str] = []
    gaps: list[str] = []

    for heading, body in sections:
        if heading == "__preamble__":
            continue

        if any(d in heading for d in _DROP_SECTIONS):
            gaps.append(
                f"### {heading}\n"
                f"Reason: Claude Code–specific (routing table / MCP hook triggers).\n"
                f"Mitigation: Codex uses the explicit continuity protocol above. "
                f"`hooks.json` is best effort only because Codex hooks are currently "
                f"disabled on Windows."
            )
            continue

        filtered, stripped = _filter_lines(body)
        if stripped:
            gaps.append(
                f"### {heading} (partial — {len(stripped)} line(s) removed)\n"
                + "\n".join(f"  - `{l}`" for l in stripped[:5])
                + ("\n  - *(more omitted)*" if len(stripped) > 5 else "")
            )
            portable.append(filtered)
        else:
            portable.append(body)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = (
        "# Agent Instructions\n"
        f"> Auto-generated from CLAUDE.md — edit source, re-run `python codex_sync.py`\n"
        f"> Last synced: {date_str}\n\n"
    )
    continuity = (
        "## KONTEXT CODEX CONTINUITY\n\n"
        "- On session start or resume, call `mcp__kontext__kontext_session` with `action=\"get\"` and `workspace=\"<current repo root or cwd>\"` before continuing work. Do not restore task state from a global latest-session note.\n"
        "- Before ending a turn after meaningful work, call `mcp__kontext__kontext_session` with `action=\"save\"`, `workspace=\"<current repo root or cwd>\"`, and fill `project`, `status`, `next_step`, `key_decisions`, `summary`, and `files_touched`.\n"
        "- For durable user or project facts, call `mcp__kontext__kontext_query` first, then `mcp__kontext__kontext_write` only for genuinely new facts. Keep dated source tags.\n"
        "- Codex hooks are synced as best-effort support for non-Windows/future runtimes. On Windows they are currently disabled by Codex, so follow this section manually every session.\n\n"
        "---\n\n"
    )
    gap_block = ""
    if gaps:
        gap_block = (
            "\n---\n## GAP REPORT\n"
            "Sections from CLAUDE.md that could not be fully translated to AGENTS.md:\n\n"
            + "\n\n".join(gaps)
        )

    content = header + continuity + "".join(portable) + gap_block

    if dry_run:
        return f"AGENTS.md: would write {len(portable)} sections, {len(gaps)} gap entries"

    _ensure_codex_dir()
    CODEX_AGENTS.write_text(content, encoding="utf-8")
    log.info(f"CODEX_SYNC: wrote AGENTS.md — {len(portable)} sections, {len(gaps)} gaps")
    return f"AGENTS.md: wrote {len(portable)} sections, {len(gaps)} gap entries"


# ---------------------------------------------------------------------------
# 4. Skills
# ---------------------------------------------------------------------------

_SKILL_UI = {
    "kontext": {
        "display_name": "Kontext",
        "short_description": "Memory, digest, intake, and cleanup",
        "default_prompt": "Use $kontext to check memory status and process pending Kontext work.",
    },
    "mastermind": {
        "display_name": "Mastermind",
        "short_description": "Multi-agent project brainstorming",
        "default_prompt": "Use $mastermind to brainstorm and spec a project idea.",
    },
    "smac": {
        "display_name": "SMAC",
        "short_description": "Multi-agent codebase research",
        "default_prompt": "Use $smac to audit this codebase for the highest-impact fixes.",
    },
    "code-review": {
        "display_name": "Code Review",
        "short_description": "Pull request review workflow",
        "default_prompt": "Use $code-review to review a pull request.",
    },
}

_SKILL_SKIP_FILES = {"README.md", "LICENSE", "LICENSE.txt", ".gitignore"}
_SKILL_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}


def _plugin_skill_dirs() -> dict[str, Path]:
    """Find skills provided by enabled Claude plugins cached locally."""
    skills: dict[str, Path] = {}
    for plugin_dir in _enabled_plugin_cache_dirs():
        skills_dir = plugin_dir / "skills"
        if not skills_dir.exists():
            continue
        for source_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists()):
            name = source_dir.name
            target_name = name if name not in skills else f"{plugin_dir.parent.name}-{name}"
            skills[target_name] = source_dir
    return skills


def _plugin_command_files(existing_skill_names: set[str]) -> dict[str, Path]:
    """Promote enabled Claude plugin commands to skills when the plugin has no skills."""
    commands: dict[str, Path] = {}
    for plugin_dir in _enabled_plugin_cache_dirs():
        skills_dir = plugin_dir / "skills"
        has_skills = skills_dir.exists() and any(
            p.is_dir() and (p / "SKILL.md").exists() for p in skills_dir.iterdir()
        )
        if has_skills:
            continue
        commands_dir = plugin_dir / "commands"
        if not commands_dir.exists():
            continue
        for command_file in sorted(commands_dir.glob("*.md")):
            name = command_file.stem
            if name not in existing_skill_names and name not in commands:
                commands[name] = command_file
    return commands


def _skill_sources() -> dict[str, tuple[Path, str, str]]:
    """Return all Claude skills Codex should mirror: user skills plus enabled plugin skills."""
    sources: dict[str, tuple[Path, str, str]] = {}
    if CLAUDE_SKILLS.exists():
        for source_dir in sorted(p for p in CLAUDE_SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").exists()):
            sources[source_dir.name] = (source_dir, "`~/.claude/skills`", "skill")

    for name, source_dir in _plugin_skill_dirs().items():
        sources.setdefault(name, (source_dir, "`~/.claude/plugins/cache` enabled plugin skill", "skill"))

    for name, command_file in _plugin_command_files(set(sources)).items():
        sources[name] = (command_file, "`~/.claude/plugins/cache` enabled plugin command", "command")

    return sources


def _read_skill_source(name: str, source_path: Path, source_kind: str) -> str:
    """Use the repo's current Kontext skill, otherwise the installed Claude skill."""
    if name == "kontext" and (KONTEXT_DIR / "SKILL.md").exists():
        return (KONTEXT_DIR / "SKILL.md").read_text(encoding="utf-8")
    if source_kind == "command":
        return source_path.read_text(encoding="utf-8")
    return (source_path / "SKILL.md").read_text(encoding="utf-8")


def _strip_claude_frontmatter_keys(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx] == "---":
            end = idx
            break
    if end is None:
        return text
    blocked = ("allowed-tools:", "disable-model-invocation:")
    frontmatter = [line for line in lines[:end + 1] if not line.startswith(blocked)]
    return "\n".join(frontmatter + lines[end + 1:]) + "\n"


def _ensure_skill_name_frontmatter(name: str, text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0] == "---":
        end = None
        for idx in range(1, len(lines)):
            if lines[idx] == "---":
                end = idx
                break
        if end is not None:
            frontmatter = lines[:end + 1]
            if not any(line.startswith("name:") for line in frontmatter):
                frontmatter.insert(1, f"name: {name}")
            return "\n".join(frontmatter + lines[end + 1:]) + "\n"

    return "\n".join([
        "---",
        f"name: {name}",
        f"description: Migrated Claude command {name}",
        "---",
        "",
        text,
    ])


def _adapt_skill_for_codex(name: str, text: str, source_label: str = "`~/.claude/skills`") -> str:
    text = _strip_claude_frontmatter_keys(text)
    text = _ensure_skill_name_frontmatter(name, text)
    text = text.replace("Claude Code", "Claude/Codex")
    text = text.replace("compily", "comply")
    text = text.replace("CLAUDE.md", "AGENTS.md or CLAUDE.md")
    text = text.replace("Claude's context", "the agent's context")
    text = text.replace("Claude decides", "the agent decides")
    text = text.replace("guidance for Claude", "guidance for the agent")
    text = text.replace("loaded by Claude", "loaded by the agent")
    text = text.replace("Claude can answer", "the agent can answer")
    text = text.replace("Bash:", "Shell:")
    text = text.replace("Haiku grading", "low-cost AI grading")
    text = text.replace("Haiku is cheap", "The grading pass is cheap")
    text = text.replace("(Haiku)", "(low-cost AI pass)")
    text = text.replace("Haiku scores", "The grading pass scores")
    text = text.replace("Haiku agent", "lightweight subagent")
    text = text.replace("Haiku agents", "lightweight subagents")
    text = text.replace("Sonnet extraction", "AI extraction")
    text = text.replace("Sonnet handles", "The extraction pass handles")
    text = text.replace("Sonnet subagents", "Codex subagents")
    text = text.replace("Sonnet agents", "Codex subagents")
    text = text.replace("dispatch Sonnet", "dispatch Codex")
    text = text.replace("Use `Agent` tool with `model: \"sonnet\"` explicitly", "Use Codex subagents when the user explicitly invoked this skill")
    text = text.replace("`Agent` tool", "Codex subagents")
    text = text.replace("Generated with [Claude/Codex](https://claude.ai/code)", "Generated with Codex")
    text = text.replace("Task(", "spawn_agent(")
    text = text.replace("TodoWrite", "`update_plan`")
    text = text.replace("Glob(\"", "file search for `")
    text = text.replace("\")", "`")
    text = re.sub(r"[\U0001F300-\U0001FAFF]\s*", "", text)

    if name in {"kontext", "mastermind", "smac"}:
        text = text.replace("Sonnet", "Codex subagent")
        text = text.replace("Opus", "the main Codex agent")
        text = text.replace("Agent calls", "Codex subagent dispatches")
        text = text.replace("Agent call", "Codex subagent dispatch")
        text = text.replace("Agent with `model: \"sonnet\"`", "Codex subagent")
        text = text.replace("Grep/Read", "`Select-String`/file reads")
        text = text.replace("Grep", "`Select-String`")
        text = text.replace("Glob", "file search")
        text = text.replace(
            "All 7 council Agent calls MUST be made in a SINGLE message so they run in parallel.",
            "Dispatch council subagents in parallel when the user explicitly invokes this skill and subagents are available.",
        )
        text = text.replace(
            "All N researcher Agent calls MUST be made in a SINGLE message so they run in parallel.",
            "Dispatch researcher subagents in parallel when the user explicitly invokes this skill and subagents are available.",
        )

    preface = (
        "\n## Codex Port Notes\n\n"
        f"- This skill was migrated from {source_label} for Codex.\n"
        "- Use Codex MCP tool names exactly as exposed in this session, such as `mcp__kontext__kontext_query`.\n"
        "- Use `spawn_agent` only when the user explicitly invokes this multi-agent skill or otherwise asks for subagents.\n"
        "- Prefer portable paths with `$HOME`/`~`; the `~/Desktop/Claude/...` paths refer to this machine's existing shared workspace.\n\n"
    )
    marker = "\n# "
    if "## Codex Port Notes" not in text and marker in text:
        first_heading = text.find(marker)
        text = text[:first_heading] + preface + text[first_heading:]
    return text


def _write_openai_yaml(skill_dir: Path, name: str) -> None:
    ui = _SKILL_UI.get(name, {
        "display_name": name.replace("-", " ").title(),
        "short_description": "Migrated Claude skill",
        "default_prompt": f"Use ${name} for this task.",
    })
    agents_dir = skill_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "interface:\n"
        f"  display_name: {json.dumps(ui['display_name'])}\n"
        f"  short_description: {json.dumps(ui['short_description'])}\n"
        f"  default_prompt: {json.dumps(ui['default_prompt'])}\n"
        "policy:\n"
        "  allow_implicit_invocation: true\n"
    )
    (agents_dir / "openai.yaml").write_text(content, encoding="utf-8")


def _copy_skill_resources(source_dir: Path, target_dir: Path) -> None:
    for item in source_dir.iterdir():
        if item.name in _SKILL_SKIP_FILES or item.name in _SKILL_SKIP_DIRS or item.name == "SKILL.md":
            continue
        dest = target_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*_SKILL_SKIP_DIRS))
        elif item.is_file():
            shutil.copy2(item, dest)


def sync_skills(dry_run: bool = False) -> str:
    """Migrate Claude user skills and enabled plugin skills into Codex user skills."""
    sources = _skill_sources()
    names = sorted(sources)
    if not names:
        return "skills: no Claude skills found — skipped"

    if dry_run:
        return f"skills: would migrate {len(names)} skill(s): {', '.join(names)}"

    CODEX_SKILLS.mkdir(parents=True, exist_ok=True)
    for name in names:
        source_path, source_label, source_kind = sources[name]
        target_dir = CODEX_SKILLS / name
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_text = _adapt_skill_for_codex(
            name,
            _read_skill_source(name, source_path, source_kind),
            source_label,
        )
        (target_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        if source_path.is_dir():
            _copy_skill_resources(source_path, target_dir)
        _write_openai_yaml(target_dir, name)

    log.info(f"CODEX_SYNC: migrated skills — {', '.join(names)}")
    return f"skills: migrated {len(names)} skill(s): {', '.join(names)}"


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def _claude_md_hash() -> str:
    if not CLAUDE_MD.exists():
        return ""
    return hashlib.sha256(CLAUDE_MD.read_bytes()).hexdigest()


def check_drift() -> bool:
    """Return True if CLAUDE.md has changed since last sync."""
    if not CODEX_META.exists():
        return True
    try:
        meta = json.loads(CODEX_META.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return meta.get("claude_md_hash") != _claude_md_hash()


def _write_meta() -> None:
    meta = {
        "claude_md_hash": _claude_md_hash(),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    CODEX_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def sync(dry_run: bool = False) -> list[str]:
    results = [
        sync_mcp_config(dry_run),
        sync_hooks(dry_run),
        sync_agents_md(dry_run),
        sync_skills(dry_run),
    ]
    if not dry_run:
        _write_meta()
        log.info("CODEX_SYNC: complete")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    check = "--check" in sys.argv

    if check:
        if check_drift():
            print("DRIFT: CLAUDE.md changed since last sync. Run `python codex_sync.py` to update.")
            sys.exit(1)
        else:
            print("OK: No drift detected.")
            sys.exit(0)

    results = sync(dry_run=dry_run)
    for r in results:
        print(r)
    if dry_run:
        print("(dry-run — no files written)")
