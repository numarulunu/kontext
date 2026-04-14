# tests/test_db_enhancements.py
"""Tests for Kontext DB enhancements: tool_events, user_prompts, access_count, file_stats."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import time
from db import KontextDB


@pytest.fixture
def db(tmp_path):
    d = KontextDB(str(tmp_path / "test.db"))
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Tool events
# ---------------------------------------------------------------------------

class TestToolEvents:
    def test_add_returns_positive_id(self, db):
        eid = db.add_tool_event("s1", "Edit", "Edited foo.py", "foo.py", 6.0)
        assert eid > 0

    def test_retrieve_by_session(self, db):
        db.add_tool_event("s1", "Edit", "Edited foo.py", "foo.py", 6.0)
        db.add_tool_event("s2", "Bash", "Ran: git commit", None, 5.0)
        events = db.get_tool_events(session_id="s1")
        assert len(events) == 1
        assert events[0]["tool_name"] == "Edit"
        assert events[0]["file_path"] == "foo.py"
        assert events[0]["promoted"] == 0

    def test_since_hours_includes_recent(self, db):
        db.add_tool_event("s1", "Write", "Created new.py")
        events = db.get_tool_events(since_hours=1)
        assert len(events) == 1

    def test_since_hours_excludes_old(self, db):
        db.add_tool_event("s1", "Write", "Created old.py")
        # sleep 1.1s then query with a 0-hour window (cutoff = now, row is already in the past)
        time.sleep(1.1)
        events = db.get_tool_events(since_hours=0)
        assert len(events) == 0

    def test_promote_marks_promoted_and_creates_entry(self, db):
        eid = db.add_tool_event("s1", "Edit", "Edited bar.py", "bar.py", 7.0)
        db.promote_tool_event(eid, file="project_edits.md", fact="Edited bar.py")
        promoted = db.get_tool_events(session_id="s1", promoted=True)
        assert len(promoted) == 1
        assert promoted[0]["promoted"] == 1
        entries = db.get_entries(file="project_edits.md")
        assert any(e["fact"] == "Edited bar.py" for e in entries)

    def test_promote_nonexistent_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.promote_tool_event(9999, "x.md", "fact")

    def test_summary_capped_at_500_chars(self, db):
        long = "x" * 600
        db.add_tool_event("s1", "Bash", long)
        events = db.get_tool_events(since_hours=1)
        assert len(events[0]["summary"]) <= 500


# ---------------------------------------------------------------------------
# User prompts
# ---------------------------------------------------------------------------

class TestUserPrompts:
    def test_add_returns_positive_id(self, db):
        pid = db.add_user_prompt("s1", "Fix the bug")
        assert pid > 0

    def test_content_capped_at_2000(self, db):
        long = "y" * 3000
        db.add_user_prompt("s1", long)
        results = db.get_recent_prompts(hours=1)
        assert len(results[0]["content"]) == 2000

    def test_search_by_keyword(self, db):
        db.add_user_prompt("s1", "Fix the authentication bug in login flow")
        db.add_user_prompt("s1", "Add a dark mode toggle")
        results = db.search_prompts(query="authentication")
        assert len(results) == 1
        assert "authentication" in results[0]["content"]

    def test_search_empty_query_returns_all_recent(self, db):
        db.add_user_prompt("s1", "First")
        db.add_user_prompt("s1", "Second")
        results = db.search_prompts(query="", limit=10)
        assert len(results) == 2

    def test_get_recent_prompts_ordered_newest_first(self, db):
        db.add_user_prompt("s1", "Older prompt")
        db.add_user_prompt("s1", "Newer prompt")
        results = db.get_recent_prompts(hours=1)
        assert results[0]["content"] == "Newer prompt"

    def test_hours_filter_on_search_prompts(self, db):
        db.add_user_prompt("s1", "Something happened")
        # sleep 1.1s then query with a 0-hour window (cutoff = now, row is already in the past)
        time.sleep(1.1)
        results = db.search_prompts(query="happened", hours=0)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Access count
# ---------------------------------------------------------------------------

class TestAccessCount:
    def test_bump_increments_count(self, db):
        eid = db.add_entry(file="test.md", fact="Some fact", grade=7.0)
        db.bump_access_count(eid)
        db.bump_access_count(eid)
        entries = db.get_entries(file="test.md")
        assert entries[0]["access_count"] == 2

    def test_bump_updates_last_accessed(self, db):
        eid = db.add_entry(file="test.md", fact="Another fact", grade=5.0)
        before = db.get_entries(file="test.md")[0]["last_accessed"]
        time.sleep(1.1)
        db.bump_access_count(eid)
        after = db.get_entries(file="test.md")[0]["last_accessed"]
        assert after > before

    def test_bump_nonexistent_does_not_raise(self, db):
        db.bump_access_count(99999)  # should be silent no-op


# ---------------------------------------------------------------------------
# File stats
# ---------------------------------------------------------------------------

class TestFileStats:
    def test_returns_fact_count(self, db):
        db.add_entry(file="user.md", fact="Name: Alice", grade=9.0)
        db.add_entry(file="user.md", fact="Role: Engineer", grade=6.0)
        stats = db.get_file_stats()
        assert "user.md" in stats
        assert stats["user.md"]["fact_count"] == 2

    def test_top_grade_is_max(self, db):
        db.add_entry(file="goals.md", fact="Low priority", grade=3.0)
        db.add_entry(file="goals.md", fact="High priority", grade=9.0)
        stats = db.get_file_stats()
        assert stats["goals.md"]["top_grade"] == 9.0

    def test_top_fact_is_highest_grade_entry(self, db):
        db.add_entry(file="goals.md", fact="Boring fact", grade=2.0)
        db.add_entry(file="goals.md", fact="Important goal", grade=9.0)
        stats = db.get_file_stats()
        assert stats["goals.md"]["top_fact"] == "Important goal"

    def test_access_sum_aggregates_counts(self, db):
        eid1 = db.add_entry(file="proj.md", fact="Fact A", grade=7.0)
        eid2 = db.add_entry(file="proj.md", fact="Fact B", grade=5.0)
        db.bump_access_count(eid1)
        db.bump_access_count(eid1)
        db.bump_access_count(eid2)
        stats = db.get_file_stats()
        assert stats["proj.md"]["access_sum"] == 3

    def test_empty_db_returns_empty_dict(self, db):
        assert db.get_file_stats() == {}


# ---------------------------------------------------------------------------
# get_latest_session_id
# ---------------------------------------------------------------------------

class TestGetLatestSessionId:
    def test_returns_none_when_no_sessions(self, db):
        assert db.get_latest_session_id() is None

    def test_returns_most_recent_id(self, db):
        db._execute("INSERT INTO sessions (project) VALUES (?)", ("proj-a",))
        db._execute("INSERT INTO sessions (project) VALUES (?)", ("proj-b",))
        sid = db.get_latest_session_id()
        assert sid == 2
