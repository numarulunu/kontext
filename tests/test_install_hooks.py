# tests/test_install_hooks.py
"""Tests for install_hooks.py — hook installation logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from pathlib import Path
from install_hooks import (
    load_settings, save_settings, _has_kontext_hook, install,
    KONTEXT_SESSION_DETECT, KONTEXT_SESSION_SAVE, KONTEXT_MEMORY_SAVE,
    KONTEXT_POSTCOMPACT, HAIKU_MODEL, SETTINGS_PATH,
)


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """Override SETTINGS_PATH to a temp directory."""
    import install_hooks
    fake_settings = tmp_path / "settings.json"
    fake_claude_dir = tmp_path
    monkeypatch.setattr(install_hooks, "SETTINGS_PATH", fake_settings)
    monkeypatch.setattr(install_hooks, "CLAUDE_DIR", fake_claude_dir)
    return tmp_path


class TestLoadSettings:
    def test_returns_empty_when_no_file(self, settings_dir):
        result = load_settings()
        assert result == {}

    def test_loads_existing_settings(self, settings_dir):
        import install_hooks
        install_hooks.SETTINGS_PATH.write_text('{"hooks": {}}', encoding="utf-8")
        result = load_settings()
        assert result == {"hooks": {}}

    def test_handles_corrupt_json(self, settings_dir):
        import install_hooks
        install_hooks.SETTINGS_PATH.write_text("not json!!!", encoding="utf-8")
        result = load_settings()
        assert result == {}
        # Should create backup
        assert install_hooks.SETTINGS_PATH.with_suffix(".json.bak").exists()


class TestSaveSettings:
    def test_saves_and_creates_backup(self, settings_dir):
        import install_hooks
        # Create initial file
        install_hooks.SETTINGS_PATH.write_text('{"old": true}', encoding="utf-8")
        # Save new
        save_settings({"new": True})
        # Check new content
        loaded = json.loads(install_hooks.SETTINGS_PATH.read_text(encoding="utf-8"))
        assert loaded == {"new": True}
        # Check backup
        backup = install_hooks.SETTINGS_PATH.with_suffix(".json.bak")
        assert backup.exists()


class TestHasKontextHook:
    def test_detects_existing_hook(self):
        settings = {
            "hooks": {
                "UserPromptSubmit": [KONTEXT_SESSION_DETECT]
            }
        }
        assert _has_kontext_hook(settings, "UserPromptSubmit") is True

    def test_returns_false_when_empty(self):
        settings = {"hooks": {"UserPromptSubmit": []}}
        assert _has_kontext_hook(settings, "UserPromptSubmit") is False

    def test_returns_false_when_no_hooks_section(self):
        settings = {}
        assert _has_kontext_hook(settings, "UserPromptSubmit") is False


class TestInstall:
    def test_installs_all_hooks(self, settings_dir, capsys):
        install()
        import install_hooks
        settings = json.loads(install_hooks.SETTINGS_PATH.read_text(encoding="utf-8"))
        assert "UserPromptSubmit" in settings["hooks"]
        assert "PostCompact" in settings["hooks"]
        assert len(settings["hooks"]["UserPromptSubmit"]) == 3
        assert len(settings["hooks"]["PostCompact"]) == 1

    def test_idempotent_installation(self, settings_dir, capsys):
        install()
        install()  # Second install should not duplicate
        import install_hooks
        settings = json.loads(install_hooks.SETTINGS_PATH.read_text(encoding="utf-8"))
        assert len(settings["hooks"]["UserPromptSubmit"]) == 3

    def test_cleans_dead_session_end(self, settings_dir):
        import install_hooks
        # Pre-populate with dead SessionEnd hooks
        install_hooks.SETTINGS_PATH.write_text(json.dumps({
            "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo old"}]}]}
        }), encoding="utf-8")
        install()
        settings = json.loads(install_hooks.SETTINGS_PATH.read_text(encoding="utf-8"))
        assert settings["hooks"]["SessionEnd"] == []


class TestHookConstants:
    def test_haiku_model_is_valid(self):
        assert "claude" in HAIKU_MODEL
        assert "haiku" in HAIKU_MODEL

    def test_postcompact_uses_haiku_model(self):
        hook = KONTEXT_POSTCOMPACT["hooks"][0]
        assert hook["model"] == HAIKU_MODEL

    def test_postcompact_has_6_fields(self):
        prompt = KONTEXT_POSTCOMPACT["hooks"][0]["prompt"]
        assert "summary" in prompt
        assert "files_touched" in prompt

    def test_memory_save_throttle_is_60s(self):
        cmd = KONTEXT_MEMORY_SAVE["hooks"][0]["command"]
        assert "60" in cmd

    def test_session_detect_5_min_gap(self):
        cmd = KONTEXT_SESSION_DETECT["hooks"][0]["command"]
        assert "300" in cmd
