# tests/test_hook_enhancements.py
"""Regression tests for hook capture and session summary behavior."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import KontextDB


class TestCaptureToolHelpers:
    def test_bash_redirect_is_captured(self):
        from hooks.capture_tool import build_summary

        summary, file_path, grade = build_summary(
            "Bash", {"command": "echo hello > notes.txt"}
        )

        assert summary == "Ran: echo hello > notes.txt"
        assert file_path is None
        assert grade == 5.0

    def test_throttle_key_is_session_scoped(self):
        from hooks.capture_tool import throttle_file_for

        first = throttle_file_for("session-a", "Bash")
        second = throttle_file_for("session-b", "Bash")

        assert first != second


class TestSessionSummaryHelpers:
    def test_summary_uses_newest_three_events(self):
        from hooks.session_summary import build_summary_fields

        events = [
            {"summary": "newest", "file_path": "new.py", "grade": 5.0},
            {"summary": "middle", "file_path": "mid.py", "grade": 5.0},
            {"summary": "oldest", "file_path": "old.py", "grade": 5.0},
            {"summary": "ignored", "file_path": "ignored.py", "grade": 5.0},
        ]

        fields = build_summary_fields(events)

        assert fields["summary"] == "newest -> middle -> oldest"

    def test_learned_summaries_are_grade_ranked(self):
        from hooks.session_summary import build_summary_fields

        events = [
            {"summary": "recent low", "file_path": "a.py", "grade": 6.0},
            {"summary": "older high", "file_path": "b.py", "grade": 9.0},
            {"summary": "older medium", "file_path": "c.py", "grade": 7.0},
        ]

        fields = build_summary_fields(events)

        assert fields["learned"] == "older high | older medium | recent low"

    def test_session_summary_update_uses_hook_session_id_not_latest(self, tmp_path):
        db = KontextDB(str(tmp_path / "test.db"))
        try:
            db.save_session(project="unrelated-a")
            db.save_session(project="unrelated-b")

            session_id = db.upsert_session_summary(
                hook_session_id="hook-1",
                investigated="file.py",
                learned="learned",
                files_touched="file.py",
                summary="summary",
            )

            latest = db.get_latest_session()
            row = db.conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            unrelated = db.conn.execute(
                "SELECT * FROM sessions WHERE project = ?", ("unrelated-b",)
            ).fetchone()

            assert latest["id"] == session_id
            assert row["hook_session_id"] == "hook-1"
            assert row["summary"] == "summary"
            assert unrelated["summary"] == ""
        finally:
            db.close()
