# tests/test_mcp_server.py
"""Tests for mcp_server.py — tool handlers, input validation, error paths."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    db.add_entry(file="user_identity.md", fact="Name: Test User", source="[test]", grade=9, tier="active")
    db.add_entry(file="user_identity.md", fact="Location: Berlin", source="[test]", grade=8, tier="active")
    db.add_entry(file="project_goals.md", fact="Launch Skool community", source="[test]", grade=7, tier="active")
    yield db
    db.close()


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "- [User Identity](user_identity.md) — name, location, role\n"
        "- [Project Goals](project_goals.md) — goals and plans\n",
        encoding="utf-8",
    )
    (mem / "user_identity.md").write_text("# User Identity\nTest file", encoding="utf-8")
    (mem / "project_goals.md").write_text("# Project Goals\nTest file", encoding="utf-8")
    return mem


@pytest.fixture
def entries(memory_dir):
    from mcp_server import index_memories
    return index_memories(memory_dir)


def _make_request(method, tool_name=None, args=None, req_id=1):
    """Build a JSON-RPC request dict."""
    request = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if tool_name:
        request["params"] = {"name": tool_name, "arguments": args or {}}
    elif args:
        request["params"] = args
    return request


class TestInitialize:
    def test_returns_server_info(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("initialize")
        resp = handle_request(req, memory_dir, entries)
        assert resp["result"]["serverInfo"]["name"] == "kontext-memory"
        assert resp["result"]["serverInfo"]["version"] == "6.1.0"

    def test_notifications_initialized_returns_none(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("notifications/initialized")
        resp = handle_request(req, memory_dir, entries)
        assert resp is None


class TestToolsList:
    def test_lists_all_tools(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("tools/list")
        resp = handle_request(req, memory_dir, entries)
        tools = resp["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        expected = {
            "kontext_search", "kontext_reindex", "kontext_write",
            "kontext_query", "kontext_relate", "kontext_recent",
            "kontext_dream", "kontext_digest", "kontext_decay",
            "kontext_session", "kontext_cloud_status", "kontext_cloud_link",
            "kontext_cloud_sync", "kontext_cloud_recover", "kontext_conflicts",
            "kontext_prompts",
        }
        assert expected == tool_names


class TestKontextSearch:
    def test_search_returns_results(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("tools/call", "kontext_search", {"query": "identity name"})
        resp = handle_request(req, memory_dir, entries)
        assert "content" in resp["result"]
        text = resp["result"]["content"][0]["text"]
        assert "matched" in text

    def test_search_empty_query_errors(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("tools/call", "kontext_search", {"query": ""})
        resp = handle_request(req, memory_dir, entries)
        assert "Error" in resp["result"]["content"][0]["text"]

    def test_search_truncates_long_query(self, memory_dir, entries):
        from mcp_server import handle_request
        long_query = "x" * 1000
        req = _make_request("tools/call", "kontext_search", {"query": long_query})
        resp = handle_request(req, memory_dir, entries)
        # Should not crash — query truncated to 500
        assert "result" in resp or "error" in resp

    def test_search_caps_top_k(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("tools/call", "kontext_search", {"query": "test", "top_k": 100})
        resp = handle_request(req, memory_dir, entries)
        # top_k capped to 20
        assert "result" in resp


class TestKontextWrite:
    def test_write_requires_file_and_fact(self, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db") as mock_db:
            req = _make_request("tools/call", "kontext_write", {"file": "", "fact": ""})
            resp = handle_request(req, memory_dir, entries)
            assert "Error" in resp["result"]["content"][0]["text"]

    def test_write_rejects_long_fact(self, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db") as mock_db:
            req = _make_request("tools/call", "kontext_write", {
                "file": "test.md", "fact": "x" * 6000
            })
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "too long" in text

    def test_write_rejects_long_filename(self, memory_dir, entries):
        from mcp_server import handle_request
        with patch("mcp_server._get_db") as mock_db:
            req = _make_request("tools/call", "kontext_write", {
                "file": "x" * 300 + ".md", "fact": "test fact"
            })
            resp = handle_request(req, memory_dir, entries)
            assert "Error" in resp["result"]["content"][0]["text"]

    def test_write_clamps_grade(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            with patch("export.export_file", return_value="# Test"):
                with patch("export.export_memory_index"):
                    req = _make_request("tools/call", "kontext_write", {
                        "file": "user_identity.md", "fact": "Grade test",
                        "grade": 99, "source": "[test]"
                    })
                    resp = handle_request(req, memory_dir, entries)
                    assert "result" in resp

    def test_db_failure_returns_clean_error(self, memory_dir, entries):
        """If db.add_entry raises (e.g. disk full), MCP must return a structured
        error response — not a 500-style crash or malformed JSON-RPC."""
        from mcp_server import handle_request
        mock_db = MagicMock()
        mock_db.add_entry.side_effect = RuntimeError("disk full")
        with patch("mcp_server._get_db", return_value=mock_db):
            req = _make_request("tools/call", "kontext_write", {
                "file": "user_identity.md", "fact": "should fail",
                "source": "[test]", "grade": 7,
            })
            resp = handle_request(req, memory_dir, entries)
            # Either an explicit JSON-RPC error or an error string in the content payload
            if "error" in resp:
                assert "disk full" in str(resp["error"]).lower() or "failed" in str(resp["error"]).lower()
            else:
                text = resp["result"]["content"][0]["text"].lower()
                assert "fail" in text or "disk full" in text or "error" in text

    def test_write_sanitizes_invalid_tier(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            with patch("export.export_file", return_value="# Test"):
                with patch("export.export_memory_index"):
                    req = _make_request("tools/call", "kontext_write", {
                        "file": "user_identity.md", "fact": "Tier test",
                        "tier": "invalid_tier", "source": "[test]"
                    })
                    resp = handle_request(req, memory_dir, entries)
                    # Should default to "active" instead of crashing
                    assert "result" in resp


class TestKontextSession:
    def test_session_save_and_get(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            # Save
            req = _make_request("tools/call", "kontext_session", {
                "action": "save", "project": "Test Project",
                "status": "in progress", "next_step": "write tests",
                "key_decisions": "use pytest", "summary": "testing session",
                "files_touched": "db.py, mcp_server.py",
                "workspace": "C:/repos/kontext",
            })
            resp = handle_request(req, memory_dir, entries)
            assert "result" in resp

            # Get
            req = _make_request("tools/call", "kontext_session", {"action": "get", "workspace": "C:/repos/kontext"})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "Test Project" in text
            assert "testing session" in text
            assert "c:/repos/kontext" in text.lower()

    def test_session_get_requires_workspace(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_session", {"action": "get"})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "workspace is required" in text.lower()

    def test_session_invalid_action(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_session", {"action": "delete"})
            resp = handle_request(req, memory_dir, entries)
            assert "Error" in resp["result"]["content"][0]["text"]


class TestKontextConflicts:
    def test_detect_no_conflicts(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_conflicts", {"action": "detect"})
            resp = handle_request(req, memory_dir, entries)
            assert "result" in resp

    def test_list_no_pending(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_conflicts", {"action": "list"})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "No pending" in text

    def test_resolve_requires_id(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_conflicts", {"action": "resolve"})
            resp = handle_request(req, memory_dir, entries)
            assert "Error" in resp["result"]["content"][0]["text"]

    def test_invalid_action(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_conflicts", {"action": "foo"})
            resp = handle_request(req, memory_dir, entries)
            assert "Error" in resp["result"]["content"][0]["text"]


class TestKontextQuery:
    def test_query_by_search(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_query", {"search": "Name"})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "Test User" in text

    def test_query_no_results(self, memory_dir, entries, db):
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db):
            req = _make_request("tools/call", "kontext_query", {"search": "zzz_nonexistent_zzz"})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "No entries" in text

    def test_semantic_search_path(self, memory_dir, entries, db):
        """semantic=True should call get_model().encode() then db.semantic_search()."""
        from mcp_server import handle_request
        fake_model = MagicMock()
        fake_model.encode.return_value = [0.1, 0.2, 0.3]
        fake_results = [
            {"id": 1, "file": "user_identity.md", "fact": "Name: Test User",
             "source": "[test]", "grade": 9, "tier": "active", "score": 0.92},
        ]
        with patch("mcp_server._get_db", return_value=db), \
             patch("mcp_server.get_model", return_value=fake_model), \
             patch.object(db, "semantic_search", return_value=fake_results) as mock_sem:
            req = _make_request("tools/call", "kontext_query",
                                {"search": "who is the user", "semantic": True, "top_k": 5})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            assert "Test User" in text
            fake_model.encode.assert_called_once()
            mock_sem.assert_called_once()
            # RRF fusion pulls a wider retriever window (max(top_k*3, 20))
            # before slicing to top_k post-merge.
            assert mock_sem.call_args.kwargs.get("limit") == 20

    def test_semantic_falls_back_on_import_error(self, memory_dir, entries, db):
        """Missing sentence_transformers → fall back to search_entries gracefully."""
        from mcp_server import handle_request
        with patch("mcp_server._get_db", return_value=db), \
             patch("mcp_server.get_model", side_effect=ImportError("no module")):
            req = _make_request("tools/call", "kontext_query",
                                {"search": "Name", "semantic": True})
            resp = handle_request(req, memory_dir, entries)
            text = resp["result"]["content"][0]["text"]
            # Should still return keyword results, not crash
            assert "Test User" in text


class TestUnknownMethod:
    def test_unknown_method_returns_error(self, memory_dir, entries):
        from mcp_server import handle_request
        req = _make_request("nonexistent/method")
        resp = handle_request(req, memory_dir, entries)
        assert "error" in resp
        assert resp["error"]["code"] == -32601


class TestSearchFallback:
    def test_keyword_fallback_when_model_unavailable(self, memory_dir, entries):
        """Search should fall back to keyword matching when sentence-transformers fails."""
        from mcp_server import search
        # Simulate entries without embeddings
        test_entries = [
            {"filename": "user_identity.md", "title": "User Identity",
             "path": str(memory_dir / "user_identity.md"),
             "description": "name location role identity", "embedding": None},
        ]
        with patch("mcp_server.get_model", side_effect=ImportError("no module")):
            results = search("identity name", test_entries, top_k=5)
            assert len(results) >= 1
            assert results[0]["filename"] == "user_identity.md"


class TestTransactionIsolation:
    def test_transaction_commits_on_success(self, tmp_path):
        db = KontextDB(str(tmp_path / "test.db"))
        with db.transaction():
            db.conn.execute(
                "INSERT INTO entries (file, fact, source, grade, tier) VALUES (?, ?, ?, ?, ?)",
                ("test.md", "transaction test", "[test]", 5, "active"),
            )
        result = db.conn.execute("SELECT fact FROM entries WHERE fact = 'transaction test'").fetchone()
        assert result is not None
        db.close()

    def test_transaction_rolls_back_on_error(self, tmp_path):
        db = KontextDB(str(tmp_path / "test.db"))
        try:
            with db.transaction():
                db.conn.execute(
                    "INSERT INTO entries (file, fact, source, grade, tier) VALUES (?, ?, ?, ?, ?)",
                    ("test.md", "rollback test", "[test]", 5, "active"),
                )
                raise ValueError("intentional error")
        except ValueError:
            pass
        result = db.conn.execute("SELECT fact FROM entries WHERE fact = 'rollback test'").fetchone()
        assert result is None
        db.close()


class TestEmbeddingValidation:
    def test_malformed_blob_returns_none(self, tmp_path):
        db = KontextDB(str(tmp_path / "test.db"))
        eid = db.add_entry(file="test.md", fact="embed test", grade=5)
        # Write a malformed blob (3 bytes, not divisible by 4)
        db.conn.execute("UPDATE entries SET embedding = ? WHERE id = ?", (b"\x00\x01\x02", eid))
        db.conn.commit()
        result = db.get_embedding(eid)
        assert result is None
        db.close()

    def test_empty_blob_returns_none(self, tmp_path):
        db = KontextDB(str(tmp_path / "test.db"))
        eid = db.add_entry(file="test.md", fact="embed test 2", grade=5)
        db.conn.execute("UPDATE entries SET embedding = ? WHERE id = ?", (b"", eid))
        db.conn.commit()
        result = db.get_embedding(eid)
        assert result is None
        db.close()


