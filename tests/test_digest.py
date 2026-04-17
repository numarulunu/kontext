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
        assert route_to_file("Claude Skool", "preference") == "project_content.md"

    def test_type_fallback(self):
        assert route_to_file("Unknown Project", "financial") == "user_financial_architecture.md"

    def test_default_fallback(self):
        assert route_to_file("Unknown Project", "unknown_type") == "user_identity.md"


class TestProcessDigests:
    """Integration coverage for digest.process_digests — the orchestration
    layer that ingests raw user messages straight into the DB. Previously
    untested end-to-end."""

    @pytest.fixture
    def patched(self, monkeypatch, tmp_path, db):
        """Patch module-level state so process_digests uses our test DB and
        a tmp pending flag, not the user's real Backup System."""
        import digest as digest_mod
        # KontextDB() inside process_digests must yield our test db
        monkeypatch.setattr(digest_mod, "KontextDB", lambda *a, **kw: _NoCloseDB(db))
        # Use tmp pending flag so unlink doesn't touch the real one
        pending = tmp_path / "_digest-pending"
        pending.write_text("", encoding="utf-8")
        monkeypatch.setattr(digest_mod, "PENDING_FLAG", pending)
        # Block the export side-effects in auto mode
        monkeypatch.setattr("mcp_server.find_memory_dir", lambda: None)
        # Stub Haiku distillation — tests don't hit the real API
        monkeypatch.setattr(digest_mod, "distill_with_haiku", lambda text: text)
        return {"pending": pending}

    def test_auto_mode_imports_high_grade_candidates(self, patched, db, digest_file):
        from digest import process_digests
        result = process_digests(auto=True, specific_file=digest_file, min_grade=7)
        assert result["files_processed"] == 1
        assert result["imported"] >= 1
        # The status_change "Skool community is live" (grade 8) should be in DB
        hits = db.search_entries("Skool community is live")
        assert len(hits) >= 1

    def test_auto_mode_clears_pending_flag(self, patched, digest_file):
        from digest import process_digests
        # specific_file path skips the unlink — use manifest path instead
        # by faking the manifest + glob via monkeypatch
        import digest as digest_mod
        # Re-patch DIGEST_DIR + MANIFEST so the non-specific_file branch runs
        digest_dir = digest_file.parent
        manifest = digest_dir / "_manifest.md"
        manifest.write_text("", encoding="utf-8")
        import unittest.mock as mock
        with mock.patch.object(digest_mod, "DIGEST_DIR", digest_dir), \
             mock.patch.object(digest_mod, "MANIFEST", manifest):
            assert patched["pending"].exists()
            process_digests(auto=True, min_grade=7)
            assert not patched["pending"].exists()

    def test_auto_mode_idempotent(self, patched, db, digest_file):
        """Running twice should produce zero new imports the second time."""
        from digest import process_digests
        r1 = process_digests(auto=True, specific_file=digest_file, min_grade=7)
        r2 = process_digests(auto=True, specific_file=digest_file, min_grade=7)
        assert r1["imported"] >= 1
        assert r2["imported"] == 0  # all dedup'd against the DB

    def test_auto_mode_skips_low_grade(self, patched, db, digest_file):
        """min_grade=11 means nothing qualifies — no imports."""
        from digest import process_digests
        result = process_digests(auto=True, specific_file=digest_file, min_grade=11)
        assert result["imported"] == 0

    def test_partial_crash_propagates(self, patched, db, digest_file, monkeypatch):
        """If db.add_entry raises mid-import, the exception propagates and the
        pending flag is NOT cleared (caller can retry)."""
        from digest import process_digests
        original_add = db.add_entry
        call_count = {"n": 0}

        def flaky_add(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 1:
                raise RuntimeError("disk full")
            return original_add(*args, **kwargs)

        monkeypatch.setattr(db, "add_entry", flaky_add)
        with pytest.raises(RuntimeError, match="disk full"):
            process_digests(auto=True, specific_file=digest_file, min_grade=7)
        # specific_file path doesn't touch pending flag — assert it's still there
        assert patched["pending"].exists()


class TestDistillation:
    """Haiku distillation in auto_import — drop raw chatter, keep only the distilled fact."""

    def test_distill_skip_drops_candidate(self, db, monkeypatch):
        """If Haiku returns SKIP (None), the candidate must NOT be stored."""
        from digest import auto_import
        import digest as digest_mod
        monkeypatch.setattr(digest_mod, "distill_with_haiku", lambda text: None)
        candidates = [{
            "text": "ok cool", "grade": 8, "type": "decision",
            "source": "[test]", "project": "Test",
        }]
        result = auto_import(candidates, db)
        assert result["imported"] == 0
        assert result["distill_dropped"] == 1
        assert db.get_entries() == []

    def test_distill_replaces_text(self, db, monkeypatch):
        """Distilled text — not the raw — is what lands in the DB."""
        from digest import auto_import
        import digest as digest_mod
        monkeypatch.setattr(digest_mod, "distill_with_haiku",
                            lambda text: "Alice switched invoicing from Stripe to Revolut.")
        candidates = [{
            "text": "ok so I'm definitely going with Revolut now instead of Stripe lol",
            "grade": 9, "type": "decision",
            "source": "[test]", "project": "Test",
        }]
        result = auto_import(candidates, db)
        assert result["imported"] == 1
        rows = db.get_entries()
        assert len(rows) == 1
        assert "Alice switched" in rows[0]["fact"]
        assert "lol" not in rows[0]["fact"]  # raw chatter dropped

    def test_distill_unavailable_drops_all(self, db, monkeypatch):
        """If Haiku is unreachable (no key, no network), no raw fallback."""
        from digest import auto_import
        import digest as digest_mod
        monkeypatch.setattr(digest_mod, "distill_with_haiku", lambda text: None)
        candidates = [
            {"text": "fact one", "grade": 8, "type": "decision", "source": "[t]", "project": "P"},
            {"text": "fact two", "grade": 9, "type": "metric", "source": "[t]", "project": "P"},
        ]
        result = auto_import(candidates, db)
        assert result["imported"] == 0
        assert result["distill_dropped"] == 2

    def test_distill_false_bypasses(self, db):
        """distill=False stores raw text — used by tests and one-off imports."""
        from digest import auto_import
        candidates = [{
            "text": "raw text", "grade": 8, "type": "decision",
            "source": "[t]", "project": "P",
        }]
        result = auto_import(candidates, db, distill=False)
        assert result["imported"] == 1


class TestArchiveProcessed:
    """Processed digest files should move to _processed/ on success."""

    def test_archives_processed_files(self, monkeypatch, tmp_path, db, digest_file):
        """After a successful run, the digest file moves to _processed/."""
        import digest as digest_mod
        # Wire process_digests to read from a tmp digest dir
        digest_dir = digest_file.parent
        manifest = digest_dir / "_manifest.md"
        manifest.write_text("", encoding="utf-8")
        pending = tmp_path / "_digest-pending"
        pending.write_text("", encoding="utf-8")

        monkeypatch.setattr(digest_mod, "KontextDB", lambda *a, **kw: _NoCloseDB(db))
        monkeypatch.setattr(digest_mod, "DIGEST_DIR", digest_dir)
        monkeypatch.setattr(digest_mod, "MANIFEST", manifest)
        monkeypatch.setattr(digest_mod, "PENDING_FLAG", pending)
        monkeypatch.setattr(digest_mod, "distill_with_haiku", lambda t: t)
        monkeypatch.setattr("mcp_server.find_memory_dir", lambda: None)

        from digest import process_digests
        assert digest_file.exists()
        process_digests(auto=True, min_grade=7)
        # Original location is empty, file moved
        assert not digest_file.exists()
        assert (digest_dir / "_processed" / digest_file.name).exists()

    def test_archive_idempotent_on_rerun(self, monkeypatch, tmp_path, db, digest_file):
        """If a file with the same name already exists in _processed/, replace it."""
        import digest as digest_mod
        digest_dir = digest_file.parent
        manifest = digest_dir / "_manifest.md"
        manifest.write_text("", encoding="utf-8")
        pending = tmp_path / "_digest-pending"
        pending.write_text("", encoding="utf-8")
        # Pre-populate _processed/ with a stale copy
        (digest_dir / "_processed").mkdir()
        (digest_dir / "_processed" / digest_file.name).write_text("stale", encoding="utf-8")

        monkeypatch.setattr(digest_mod, "KontextDB", lambda *a, **kw: _NoCloseDB(db))
        monkeypatch.setattr(digest_mod, "DIGEST_DIR", digest_dir)
        monkeypatch.setattr(digest_mod, "MANIFEST", manifest)
        monkeypatch.setattr(digest_mod, "PENDING_FLAG", pending)
        monkeypatch.setattr(digest_mod, "distill_with_haiku", lambda t: t)
        monkeypatch.setattr("mcp_server.find_memory_dir", lambda: None)

        from digest import process_digests
        process_digests(auto=True, min_grade=7)
        archived = (digest_dir / "_processed" / digest_file.name).read_text(encoding="utf-8")
        assert "stale" not in archived  # was overwritten with the fresh content


class _NoCloseDB:
    """Wrap a KontextDB so process_digests' .close() call is a no-op
    (the test fixture owns the lifetime)."""
    def __init__(self, real):
        self._real = real
    def __getattr__(self, name):
        return getattr(self._real, name)
    def close(self):
        pass
