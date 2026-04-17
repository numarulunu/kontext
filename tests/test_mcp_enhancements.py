# tests/test_mcp_enhancements.py
"""Tests for mcp_server.py enhancements: kontext_search mode=index,
kontext_prompts tool, and access_count bump in kontext_query."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch
from db import KontextDB


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = KontextDB(str(tmp_path / "test.db"))
    d.add_entry(file="user_identity.md", fact="Name: Alice",         grade=9.0)
    d.add_entry(file="user_identity.md", fact="Location: Bucharest", grade=7.0)
    d.add_entry(file="project_goals.md", fact="Launch app by Q3",    grade=8.0)
    d.add_user_prompt("s1", "Fix the authentication bug")
    d.add_user_prompt("s1", "What is the status of the project?")
    yield d
    d.close()


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "- [User Identity](user_identity.md) — name, location, background\n"
        "- [Project Goals](project_goals.md) — goals, launch plans\n",
        encoding="utf-8",
    )
    (mem / "user_identity.md").write_text("# User Identity\nAlice", encoding="utf-8")
    (mem / "project_goals.md").write_text("# Project Goals\nLaunch", encoding="utf-8")
    return mem


@pytest.fixture
def entries(memory_dir):
    from mcp_server import index_memories
    return index_memories(memory_dir)


def _req(tool_name, args, req_id=1):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }


# ---------------------------------------------------------------------------
# kontext_search mode=index
# ---------------------------------------------------------------------------

class TestKontextSearchIndexMode:
    def test_index_mode_includes_fact_count(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_search", {"query": "user identity", "mode": "index"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "facts" in text
        assert "tokens" in text

    def test_index_mode_includes_top_fact_preview(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_search", {"query": "user identity", "mode": "index"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "Preview" in text

    def test_index_mode_includes_top_grade(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_search", {"query": "user identity", "mode": "index"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "9.0" in text

    def test_full_mode_shows_description_not_fact_count(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_search", {"query": "goals", "mode": "full"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "%" in text
        assert "facts" not in text

    def test_default_mode_is_full(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_search", {"query": "goals"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "%" in text
        assert "facts" not in text
        assert "tokens" not in text

    def test_index_mode_stats_error_degrades_gracefully(self, db, memory_dir, entries):
        """If get_file_stats() raises, index mode still renders."""
        from mcp_server import handle_request
        with patch.object(db, "get_file_stats", side_effect=RuntimeError("boom")):
            with patch("mcp_server._get_db", return_value=db):
                resp = handle_request(
                    _req("kontext_search", {"query": "user identity", "mode": "index"}),
                    memory_dir, entries,
                )
        assert "result" in resp


# ---------------------------------------------------------------------------
# kontext_prompts
# ---------------------------------------------------------------------------

class TestKontextPrompts:
    def test_search_returns_matching_prompts(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_prompts", {"search": "authentication"}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "authentication" in text

    def test_no_args_returns_recent_24h(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_prompts", {}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "Past prompts:" in text
        assert "2 found" in text

    def test_hours_filter(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_prompts", {"hours": 24}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert "Past prompts:" in text

    def test_empty_db_returns_no_prompts_message(self, memory_dir, entries, tmp_path):
        from mcp_server import handle_request
        empty_db = KontextDB(str(tmp_path / "empty.db"))
        try:
            with patch("mcp_server._get_db", return_value=empty_db):
                resp = handle_request(
                    _req("kontext_prompts", {}),
                    memory_dir, entries,
                )
            text = resp["result"]["content"][0]["text"]
            assert "No prompts" in text
        finally:
            empty_db.close()

    def test_limit_respected(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_prompts", {"limit": 1}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert text.count("- `") == 1

    def test_negative_limit_clamped_to_one(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_prompts", {"limit": -1}),
                memory_dir, entries,
            )
        text = resp["result"]["content"][0]["text"]
        assert text.count("- `") == 1


# ---------------------------------------------------------------------------
# Access count bump in kontext_query
# ---------------------------------------------------------------------------

class TestAccessCountBump:
    def test_query_bumps_access_count_for_matched_entries(self, db, memory_dir, entries):
        from mcp_server import handle_request
        before = {e["id"]: e["access_count"] for e in db.get_entries(file="user_identity.md")}

        with patch("mcp_server._get_db", return_value=db):
            handle_request(
                _req("kontext_query", {"search": "Alice"}),
                memory_dir, entries,
            )

        after = {e["id"]: e["access_count"] for e in db.get_entries(file="user_identity.md")}
        bumped = [eid for eid, cnt in after.items() if cnt > before.get(eid, 0)]
        assert len(bumped) >= 1

    def test_query_no_results_does_not_raise(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            resp = handle_request(
                _req("kontext_query", {"search": "zzznomatchxxx"}),
                memory_dir, entries,
            )
        assert "result" in resp

    def test_semantic_model_runtime_error_falls_back_to_keyword(self, db, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            with patch("mcp_server.get_model", side_effect=RuntimeError("offline")):
                resp = handle_request(
                    _req("kontext_query", {"search": "Alice", "semantic": True}),
                    memory_dir, entries,
                )
        text = resp["result"]["content"][0]["text"]
        assert "Alice" in text
        assert "semantic unavailable" in text
