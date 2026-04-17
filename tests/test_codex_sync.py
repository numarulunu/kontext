"""Tests for codex_sync.py."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codex_sync


def _patch_paths(monkeypatch, tmp_path):
    codex_dir = tmp_path / ".codex"
    kontext_dir = tmp_path / "Kontext"
    kontext_dir.mkdir()
    monkeypatch.setattr(codex_sync, "CODEX_DIR", codex_dir)
    monkeypatch.setattr(codex_sync, "CODEX_CONFIG", codex_dir / "config.toml")
    monkeypatch.setattr(codex_sync, "CODEX_HOOKS", codex_dir / "hooks.json")
    monkeypatch.setattr(codex_sync, "CODEX_HOOK_SCRIPT", codex_dir / "kontext_hooks.py")
    monkeypatch.setattr(codex_sync, "CODEX_AGENTS", codex_dir / "AGENTS.md")
    monkeypatch.setattr(codex_sync, "CODEX_SKILLS", codex_dir / "skills")
    monkeypatch.setattr(codex_sync, "CODEX_META", codex_dir / ".kontext-sync-meta.json")
    monkeypatch.setattr(codex_sync, "KONTEXT_DIR", kontext_dir)
    monkeypatch.setattr(codex_sync, "MCP_SERVER", kontext_dir / "mcp_server.py")
    monkeypatch.setattr(codex_sync, "CLAUDE_SETTINGS", tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr(codex_sync, "CLAUDE_PLUGINS", tmp_path / ".claude" / "plugins")
    return codex_dir, kontext_dir


def test_mcp_config_migrates_legacy_table_and_enables_hooks(tmp_path, monkeypatch):
    codex_dir, kontext_dir = _patch_paths(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_sync.CODEX_CONFIG.write_text(
        "\n".join([
            'model = "gpt-5.4"',
            "",
            "[mcp.servers.kontext]",
            'command = "python"',
            'args = ["old.py"]',
            "",
            "[notice.model_migrations]",
            'gpt-5-codex = "gpt-5.3-codex"',
        ]),
        encoding="utf-8",
    )

    result = codex_sync.sync_mcp_config()
    written = codex_sync.CODEX_CONFIG.read_text(encoding="utf-8")

    assert "wrote 1 managed MCP" in result
    assert "[mcp.servers.kontext]" not in written
    assert "[mcp_servers.kontext]" in written
    assert "[features]" in written
    assert "codex_hooks = true" in written
    assert f'args = ["{str(kontext_dir / "mcp_server.py").replace("\\", "/")}"]' in written
    assert f'cwd = "{str(kontext_dir).replace("\\", "/")}"' in written
    assert "[notice.model_migrations]" in written


def test_mcp_config_mirrors_enabled_plugin_mcp_servers(tmp_path, monkeypatch):
    codex_dir, _ = _patch_paths(monkeypatch, tmp_path)
    codex_sync.CLAUDE_SETTINGS.parent.mkdir(parents=True)
    codex_sync.CLAUDE_SETTINGS.write_text(
        json.dumps({"enabledPlugins": {"context7@claude-plugins-official": True}}),
        encoding="utf-8",
    )
    mcp_dir = (
        codex_sync.CLAUDE_PLUGINS
        / "cache"
        / "claude-plugins-official"
        / "context7"
        / "unknown"
    )
    mcp_dir.mkdir(parents=True)
    (mcp_dir / ".mcp.json").write_text(
        json.dumps({"context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]}}),
        encoding="utf-8",
    )

    result = codex_sync.sync_mcp_config()
    written = codex_sync.CODEX_CONFIG.read_text(encoding="utf-8")

    assert "wrote 2 managed MCP" in result
    assert "[mcp_servers.context7]" in written
    assert 'command = "npx"' in written
    assert 'args = ["-y", "@upstash/context7-mcp"]' in written
    assert "[mcp_servers.kontext]" in written


def test_mcp_config_adds_requested_marketplace_mcp_servers(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(codex_sync, "CODEX_REQUESTED_PLUGIN_MCPS", {"serena"})
    serena_dir = (
        codex_sync.CLAUDE_PLUGINS
        / "marketplaces"
        / "claude-plugins-official"
        / "external_plugins"
        / "serena"
    )
    serena_dir.mkdir(parents=True)
    (serena_dir / ".mcp.json").write_text(
        json.dumps({
            "serena": {
                "command": "uvx",
                "args": ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server"],
            }
        }),
        encoding="utf-8",
    )

    result = codex_sync.sync_mcp_config()
    written = codex_sync.CODEX_CONFIG.read_text(encoding="utf-8")

    assert "serena" in result
    assert "[mcp_servers.serena]" in written
    assert 'command = "uvx"' in written
    assert 'args = ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server"]' in written


def test_mcp_config_updates_existing_current_table(tmp_path, monkeypatch):
    codex_dir, kontext_dir = _patch_paths(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_sync.CODEX_CONFIG.write_text(
        "\n".join([
            "[features]",
            "codex_hooks = false",
            "",
            "[mcp_servers.kontext]",
            'command = "python"',
            'args = ["stale.py"]',
        ]),
        encoding="utf-8",
    )

    codex_sync.sync_mcp_config()
    written = codex_sync.CODEX_CONFIG.read_text(encoding="utf-8")

    assert written.count("[mcp_servers.kontext]") == 1
    assert "stale.py" not in written
    assert "codex_hooks = true" in written
    assert f'cwd = "{str(kontext_dir).replace("\\", "/")}"' in written


def test_hooks_are_codex_native_and_preserve_unrelated_hooks(tmp_path, monkeypatch):
    codex_dir, _ = _patch_paths(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_sync.CODEX_HOOKS.write_text(
        json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup", "hooks": [{"type": "command", "command": "echo keep"}]},
                    {"hooks": [{"type": "command", "command": "python C:/Desktop/Claude/Kontext/old.py"}]},
                    {"hooks": [{"type": "command", "command": "if [ -n \"${CODEX_SKIP_HOOKS:-}\" ]; then echo '{\"suppressOutput\":true}'; fi"}]},
                ]
            }
        }),
        encoding="utf-8",
    )

    result = codex_sync.sync_hooks()
    hooks = json.loads(codex_sync.CODEX_HOOKS.read_text(encoding="utf-8"))

    assert "Codex-native Kontext hooks" in result
    assert codex_sync.CODEX_HOOK_SCRIPT.exists()
    assert "hookSpecificOutput" in codex_sync.CODEX_HOOK_SCRIPT.read_text(encoding="utf-8")
    session_groups = hooks["hooks"]["SessionStart"]
    commands = [h["command"] for g in session_groups for h in g["hooks"]]
    assert "echo keep" in commands
    assert not any("old.py" in c for c in commands)
    assert not any("CODEX_SKIP_HOOKS" in c for c in commands)
    assert any("kontext_hooks.py" in c for c in commands)
    assert hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["type"] == "command"


def test_agents_md_adds_codex_continuity_and_windows_hook_gap(tmp_path, monkeypatch):
    codex_dir, _ = _patch_paths(monkeypatch, tmp_path)
    claude_md = tmp_path / "CLAUDE.md"
    monkeypatch.setattr(codex_sync, "CLAUDE_MD", claude_md)
    claude_md.write_text(
        "\n".join([
            "# Claude",
            "## STARTUP (silent)",
            "Claude-only startup text",
            "## TOKEN DISCIPLINE",
            "- keep this",
            "- **Model routing:** default Sonnet.",
        ]),
        encoding="utf-8",
    )

    result = codex_sync.sync_agents_md()
    written = codex_sync.CODEX_AGENTS.read_text(encoding="utf-8")

    assert "AGENTS.md: wrote" in result
    assert "## KONTEXT CODEX CONTINUITY" in written
    assert "mcp__kontext__kontext_session" in written
    assert 'workspace="<current repo root or cwd>"' in written
    assert "currently disabled by Codex" in written
    assert "Claude-only startup text" not in written
    assert "Model routing" in written


def test_sync_skills_migrates_claude_user_skills_for_codex(tmp_path, monkeypatch):
    codex_dir, kontext_dir = _patch_paths(monkeypatch, tmp_path)
    claude_skills = tmp_path / ".claude" / "skills"
    monkeypatch.setattr(codex_sync, "CLAUDE_SKILLS", claude_skills)

    smac = claude_skills / "smac"
    smac_git = smac / ".git"
    smac.mkdir(parents=True)
    smac_git.mkdir()
    (smac / "README.md").write_text("skip me", encoding="utf-8")
    (smac / "LICENSE").write_text("skip me", encoding="utf-8")
    (smac_git / "HEAD").write_text("skip me", encoding="utf-8")
    (smac / "SKILL.md").write_text(
        "\n".join([
            "---",
            "name: smac",
            "description: Dispatches Sonnet researchers synthesized by Opus.",
            "allowed-tools: Read Write Edit Bash Glob Grep Agent",
            "---",
            "",
            "# SMAC",
            "",
            "Use TodoWrite, Grep, Glob, and Agent calls.",
            "If CLAUDE.md exists, read it.",
        ]),
        encoding="utf-8",
    )

    result = codex_sync.sync_skills()
    target = codex_sync.CODEX_SKILLS / "smac"
    skill = (target / "SKILL.md").read_text(encoding="utf-8")
    metadata = (target / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert result == "skills: migrated 1 skill(s): smac"
    assert "allowed-tools:" not in skill
    assert "## Codex Port Notes" in skill
    assert "Codex subagent" in skill
    assert "the main Codex agent" in skill
    assert "`update_plan`" in skill
    assert "AGENTS.md or CLAUDE.md" in skill
    assert not (target / ".git").exists()
    assert not (target / "README.md").exists()
    assert not (target / "LICENSE").exists()
    assert "display_name: \"SMAC\"" in metadata
    assert "default_prompt: \"Use $smac" in metadata


def test_sync_skills_migrates_enabled_plugin_skills_for_codex(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    claude_skills = tmp_path / ".claude" / "skills"
    monkeypatch.setattr(codex_sync, "CLAUDE_SKILLS", claude_skills)

    codex_sync.CLAUDE_SETTINGS.parent.mkdir(parents=True)
    codex_sync.CLAUDE_SETTINGS.write_text(
        json.dumps({
            "enabledPlugins": {
                "superpowers@claude-plugins-official": True,
                "frontend-design@claude-plugins-official": False,
            }
        }),
        encoding="utf-8",
    )

    enabled_skill = (
        codex_sync.CLAUDE_PLUGINS
        / "cache"
        / "claude-plugins-official"
        / "superpowers"
        / "5.0.7"
        / "skills"
        / "dispatching-parallel-agents"
    )
    enabled_skill.mkdir(parents=True)
    (enabled_skill / "SKILL.md").write_text(
        "\n".join([
            "---",
            "name: dispatching-parallel-agents",
            "description: Use Claude Code Task calls.",
            "allowed-tools: Task",
            "---",
            "",
            "# Dispatching Parallel Agents",
            "",
            "Use Task(\"Fix one thing\") and TodoWrite.",
        ]),
        encoding="utf-8",
    )

    disabled_skill = (
        codex_sync.CLAUDE_PLUGINS
        / "cache"
        / "claude-plugins-official"
        / "frontend-design"
        / "unknown"
        / "skills"
        / "frontend-design"
    )
    disabled_skill.mkdir(parents=True)
    (disabled_skill / "SKILL.md").write_text("# Frontend Design", encoding="utf-8")

    result = codex_sync.sync_skills()
    target = codex_sync.CODEX_SKILLS / "dispatching-parallel-agents"
    skill = (target / "SKILL.md").read_text(encoding="utf-8")

    assert result == "skills: migrated 1 skill(s): dispatching-parallel-agents"
    assert "allowed-tools:" not in skill
    assert "`~/.claude/plugins/cache` enabled plugin skill" in skill
    assert "Claude Code" not in skill
    assert "spawn_agent(" in skill
    assert "`update_plan`" in skill
    assert not (codex_sync.CODEX_SKILLS / "frontend-design").exists()


def test_sync_skills_promotes_enabled_plugin_command_without_skills(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(codex_sync, "CLAUDE_SKILLS", tmp_path / ".claude" / "skills")
    codex_sync.CLAUDE_SETTINGS.parent.mkdir(parents=True)
    codex_sync.CLAUDE_SETTINGS.write_text(
        json.dumps({"enabledPlugins": {"code-review@claude-plugins-official": True}}),
        encoding="utf-8",
    )

    command_dir = (
        codex_sync.CLAUDE_PLUGINS
        / "cache"
        / "claude-plugins-official"
        / "code-review"
        / "unknown"
        / "commands"
    )
    command_dir.mkdir(parents=True)
    (command_dir / "code-review.md").write_text(
        "\n".join([
            "---",
            "description: Code review a pull request",
            "allowed-tools: Bash(gh pr view:*)",
            "disable-model-invocation: false",
            "---",
            "",
            "Use a Haiku agent, then launch 5 parallel Sonnet agents.",
            "Check CLAUDE.md and add Generated with [Claude Code](https://claude.ai/code).",
        ]),
        encoding="utf-8",
    )

    result = codex_sync.sync_skills()
    target = codex_sync.CODEX_SKILLS / "code-review"
    skill = (target / "SKILL.md").read_text(encoding="utf-8")
    metadata = (target / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert result == "skills: migrated 1 skill(s): code-review"
    assert "name: code-review" in skill
    assert "allowed-tools:" not in skill
    assert "disable-model-invocation:" not in skill
    assert "lightweight subagent" in skill
    assert "Codex subagents" in skill
    assert "AGENTS.md or CLAUDE.md" in skill
    assert "Generated with Codex" in skill
    assert "display_name: \"Code Review\"" in metadata
