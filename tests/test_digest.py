# tests/test_digest.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from db import KontextDB
from digest import (
    parse_digest, is_noise, score_message, extract_candidates,
    deduplicate_candidates, route_to_file,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = KontextDB(str(db_path))
    yield db
    db.close()


SAMPLE_DIGEST = """# Test Project — Full Conversation Digest
**Conversations:** 2
**Date range:** 2026-04-07 to 2026-04-07
**Tokens:** 1.0k total

---
## Session 1 — 2026-04-07 10:00 UTC
*4 messages, tokens: 500*

### [10:00] **USER**

I'm switching from Stripe to Revolut for all future invoicing

### [10:00] **CLAUDE**

That's a significant change. Let me help you set that up.

### [10:01] **USER**

ok

### [10:02] **USER**

I have 42 active students right now, up from 38 last month

---
## Session 2 — 2026-04-07 11:00 UTC
*2 messages*

### [11:00] **USER**

The Skool community is live, launched it yesterday

### [11:01] **CLAUDE**

Congratulations on the launch!
"""


@pytest.fixture
def digest_file(tmp_path):
    path = tmp_path / "test-project.md"
    path.write_text(SAMPLE_DIGEST, encoding="utf-8")
    return path


class TestParseDigest:
    def test_extracts_user_messages(self, digest_file):
        msgs = parse_digest(digest_file)
        assert len(msgs) == 3  # "ok" is too short (<10 chars), gets filtered
        assert all(m["role"] == "USER" for m in msgs)

    def test_session_numbers(self, digest_file):
        msgs = parse_digest(digest_file)
        assert msgs[0]["session"] == 1
        assert msgs[-1]["session"] == 2

    def test_timestamps(self, digest_file):
        msgs = parse_digest(digest_file)
        assert msgs[0]["timestamp"] == "10:00"


class TestNoise:
    def test_greeting_is_noise(self):
        assert is_noise("hello")

    def test_acknowledgment_is_noise(self):
        assert is_noise("ok")
        assert is_noise("sure!")
        assert is_noise("thanks")

    def test_tool_marker_is_noise(self):
        assert is_noise("[Tool: Read]")

    def test_real_message_not_noise(self):
        assert not is_noise("I'm switching to Revolut for invoicing")

    def test_system_reminder_is_noise(self):
        assert is_noise("<system-reminder> something")

    def test_skill_base_dir_is_noise(self):
        assert is_noise("Base directory for this skill: C:\\Users\\foo")


class TestScoring:
    def test_decision_detected(self):
        hits = score_message("I'm going with Revolut for all invoicing")
        types = [h["type"] for h in hits]
        assert "decision" in types

    def test_metric_detected(self):
        hits = score_message("I have 42 active students right now")
        types = [h["type"] for h in hits]
        # "I have" matches self_fact, "42 students" matches metric
        assert "metric" in types or "self_fact" in types

    def test_status_change_detected(self):
        hits = score_message("The Skool community is live")
        types = [h["type"] for h in hits]
        assert "status_change" in types

    def test_no_signal_returns_empty(self):
        hits = score_message("Let me think about this for a moment")
        assert len(hits) == 0


class TestExtractCandidates:
    def test_extracts_from_digest(self, digest_file):
        candidates = extract_candidates(digest_file)
        assert len(candidates) >= 1  # At least the status_change (grade 8)
        types = [c["type"] for c in candidates]
        assert "status_change" in types

    def test_candidates_have_required_fields(self, digest_file):
        candidates = extract_candidates(digest_file)
        for c in candidates:
            assert "text" in c
            assert "grade" in c
            assert "type" in c
            assert "source" in c


class TestDedup:
    def test_removes_db_duplicates(self, db, digest_file):
        # Add the status_change candidate to DB first (it's grade 8, passes filter)
        db.add_entry(
            file="project_goals.md",
            fact="The Skool community is live, launched it yesterday",
            grade=8,
        )
        candidates = extract_candidates(digest_file)
        assert len(candidates) >= 1
        fresh = deduplicate_candidates(candidates, db)
        # Should have fewer candidates after dedup
        assert len(fresh) < len(candidates)


class TestRouting:
    def test_finance_project(self):
        assert route_to_file("Claude Finance", "financial") == "user_financial_architecture.md"

    def test_skool_project(self):
        assert route_to_file("Claude Skool", "preference") == "project_vocality_content.md"

    def test_type_fallback(self):
        assert route_to_file("Unknown Project", "financial") == "user_financial_architecture.md"

    def test_default_fallback(self):
        assert route_to_file("Unknown Project", "unknown_type") == "user_identity.md"
