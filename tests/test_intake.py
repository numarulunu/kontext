"""
test_intake.py — End-to-end tests for the Kontext intake pipeline.

Tests parsers, grading, chunking, and the full extract pipeline.
Stdlib only — runs with: python -m pytest tests/test_intake.py
Or directly:              python tests/test_intake.py

Python 3.10+, stdlib only.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add pipeline dir to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))

from parsers import (
    detect_and_parse,
    parse_chatgpt_json,
    parse_chatgpt_file,
    parse_gemini_text,
    parse_whatsapp_file,
    parse_plain_text,
    parse_plain_file,
)
from grading import grade_entry, grade_messages
from chunker import chunk_messages, estimate_tokens

FIXTURES = Path(__file__).parent / "fixtures"


# ===========================================================================
# Parser tests
# ===========================================================================

class TestChatGPTParsing(unittest.TestCase):
    """Test ChatGPT JSON export parsing."""

    def test_basic_conversation(self):
        msgs = parse_chatgpt_file(FIXTURES / "chatgpt_sample.json")
        self.assertGreater(len(msgs), 0)
        # Should have user and assistant messages (system filtered out)
        roles = {m["role"] for m in msgs}
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        # System messages should be filtered
        self.assertNotIn("system", roles)

    def test_source_is_chatgpt(self):
        msgs = parse_chatgpt_file(FIXTURES / "chatgpt_sample.json")
        for m in msgs:
            self.assertEqual(m["source"], "chatgpt")

    def test_timestamps_parsed(self):
        msgs = parse_chatgpt_file(FIXTURES / "chatgpt_sample.json")
        user_msgs = [m for m in msgs if m["role"] == "user"]
        # At least one user message should have a timestamp
        has_ts = any(m["timestamp"] is not None for m in user_msgs)
        self.assertTrue(has_ts, "No timestamps parsed from ChatGPT export")

    def test_conversation_titles(self):
        msgs = parse_chatgpt_file(FIXTURES / "chatgpt_sample.json")
        titles = {m["conversation_title"] for m in msgs}
        self.assertIn("Test Conversation", titles)

    def test_single_conversation_dict(self):
        """parse_chatgpt_json should accept a single dict (not just list)."""
        single = {
            "title": "Solo",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["msg1"],
                    "message": None,
                },
                "msg1": {
                    "id": "msg1",
                    "parent": "root",
                    "children": [],
                    "message": {
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": ["Hello"]},
                        "create_time": 1712188800,
                    },
                },
            },
        }
        msgs = parse_chatgpt_json(single)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "Hello")

    def test_empty_json_file(self):
        """Empty JSON file should return empty list, not crash."""
        msgs = detect_and_parse(FIXTURES / "empty_file.json")
        self.assertEqual(msgs, [])

    def test_malformed_json(self):
        """Malformed JSON should return empty list, not crash."""
        msgs = detect_and_parse(FIXTURES / "malformed.json")
        # Should not raise — either returns empty or falls back to plain text
        self.assertIsInstance(msgs, list)

    def test_binary_garbage_json(self):
        """Binary file with .json extension should return empty list."""
        msgs = detect_and_parse(FIXTURES / "binary_garbage.json")
        self.assertIsInstance(msgs, list)


class TestWhatsAppParsing(unittest.TestCase):
    """Test WhatsApp TXT export parsing."""

    def test_basic_parsing(self):
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        self.assertGreater(len(msgs), 0)

    def test_source_is_whatsapp(self):
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        for m in msgs:
            self.assertEqual(m["source"], "whatsapp")

    def test_system_messages_filtered(self):
        """WhatsApp system messages (encryption notices etc.) should be skipped."""
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        texts = [m["text"] for m in msgs]
        for t in texts:
            self.assertNotIn("end-to-end encrypted", t)

    def test_multiline_messages(self):
        """Multi-line WhatsApp messages should be joined."""
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        # The third message has a continuation line
        multiline = [m for m in msgs if "multi-line" in m["text"]]
        self.assertEqual(len(multiline), 1)

    def test_timestamps_parsed(self):
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        has_ts = any(m["timestamp"] is not None for m in msgs)
        self.assertTrue(has_ts)

    def test_sender_in_text(self):
        """Sender name should be included in the text as [Name]."""
        msgs = parse_whatsapp_file(FIXTURES / "whatsapp_sample.txt")
        has_name = any("[Ionut]" in m["text"] for m in msgs)
        self.assertTrue(has_name)


class TestGeminiParsing(unittest.TestCase):
    """Test Gemini-style text conversation parsing."""

    def test_basic_parsing(self):
        with open(FIXTURES / "gemini_sample.txt", "r", encoding="utf-8") as f:
            text = f.read()
        msgs = parse_gemini_text(text, title="test")
        self.assertGreater(len(msgs), 0)

    def test_roles_detected(self):
        with open(FIXTURES / "gemini_sample.txt", "r", encoding="utf-8") as f:
            text = f.read()
        msgs = parse_gemini_text(text, title="test")
        roles = {m["role"] for m in msgs}
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_source_is_gemini(self):
        with open(FIXTURES / "gemini_sample.txt", "r", encoding="utf-8") as f:
            text = f.read()
        msgs = parse_gemini_text(text, title="test")
        for m in msgs:
            self.assertEqual(m["source"], "gemini")

    def test_unicode_content(self):
        """Romanian diacritics and mixed-language content should parse fine."""
        msgs = detect_and_parse(FIXTURES / "unicode_sample.txt")
        self.assertGreater(len(msgs), 0)
        # Should contain Romanian text
        all_text = " ".join(m["text"] for m in msgs)
        self.assertIn("predau canto", all_text)


class TestPlainTextParsing(unittest.TestCase):
    """Test plain text/markdown parsing."""

    def test_heading_splits(self):
        msgs = detect_and_parse(FIXTURES / "plain_doc.txt")
        self.assertGreater(len(msgs), 1, "Should split on headings")

    def test_source_is_document(self):
        msgs = detect_and_parse(FIXTURES / "plain_doc.txt")
        for m in msgs:
            self.assertEqual(m["source"], "document")

    def test_empty_string(self):
        """Empty text should produce empty message list."""
        msgs = parse_plain_text("")
        self.assertEqual(msgs, [])

    def test_whitespace_only(self):
        msgs = parse_plain_text("   \n\n\t  ")
        self.assertEqual(msgs, [])


class TestFormatDetection(unittest.TestCase):
    """Test the auto-detection router in detect_and_parse."""

    def test_detects_chatgpt_json(self):
        msgs = detect_and_parse(FIXTURES / "chatgpt_sample.json")
        sources = {m["source"] for m in msgs}
        self.assertIn("chatgpt", sources)

    def test_detects_whatsapp_txt(self):
        msgs = detect_and_parse(FIXTURES / "whatsapp_sample.txt")
        sources = {m["source"] for m in msgs}
        self.assertIn("whatsapp", sources)

    def test_detects_gemini_txt(self):
        msgs = detect_and_parse(FIXTURES / "gemini_sample.txt")
        sources = {m["source"] for m in msgs}
        self.assertIn("gemini", sources)

    def test_detects_plain_txt(self):
        msgs = detect_and_parse(FIXTURES / "plain_doc.txt")
        sources = {m["source"] for m in msgs}
        self.assertIn("document", sources)

    def test_empty_file_no_crash(self):
        msgs = detect_and_parse(FIXTURES / "empty_file.json")
        self.assertEqual(msgs, [])

    def test_binary_file_no_crash(self):
        msgs = detect_and_parse(FIXTURES / "binary_garbage.json")
        self.assertIsInstance(msgs, list)


# ===========================================================================
# Grading tests
# ===========================================================================

class TestGrading(unittest.TestCase):
    """Test the heuristic grading system."""

    def test_decision_language_scores_high(self):
        entry = {"text": "I decided to launch Vocality. I'm going with the premium tier.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 8, f"Decision language should score >= 8, got {score}")

    def test_identity_markers_score_high(self):
        entry = {"text": "I am a vocal coach. My name is Ionut and I live in Romania.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 7, f"Identity markers should score >= 7, got {score}")

    def test_noise_scores_low(self):
        entry = {"text": "ok", "role": "user"}
        score = grade_entry(entry)
        self.assertLessEqual(score, 4, f"'ok' should score <= 4, got {score}")

    def test_thanks_scores_low(self):
        entry = {"text": "thanks", "role": "user"}
        score = grade_entry(entry)
        self.assertLessEqual(score, 4, f"'thanks' should score <= 4, got {score}")

    def test_assistant_penalty(self):
        text = "I decided to launch a new product."
        user_score = grade_entry({"text": text, "role": "user"})
        asst_score = grade_entry({"text": text, "role": "assistant"})
        self.assertGreater(user_score, asst_score, "User messages should score higher than assistant")

    def test_system_always_1(self):
        entry = {"text": "You are a helpful assistant. I decided to launch.", "role": "system"}
        score = grade_entry(entry)
        self.assertEqual(score, 1)

    def test_financial_data_scores_high(self):
        entry = {"text": "My rate is 50 EUR per session and my monthly income is 3000 EUR.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 7, f"Financial data should score >= 7, got {score}")

    def test_ai_feedback_scores_high(self):
        entry = {"text": "Don't ever ask me clarifying questions. Remember that I prefer direct answers.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 7, f"AI feedback should score >= 7, got {score}")

    def test_score_clamped_1_to_10(self):
        """Score should never go below 1 or above 10."""
        low = {"text": "ok", "role": "assistant"}
        high = {"text": "I decided I am a vocal coach. My name is Ionut. I prefer Melocchi. From now on I teach only this method. My income is 5000 EUR. Remember that feedback for Claude.", "role": "user"}
        self.assertGreaterEqual(grade_entry(low), 1)
        self.assertLessEqual(grade_entry(high), 10)

    def test_grade_messages_adds_key(self):
        msgs = [
            {"text": "I decided to launch", "role": "user"},
            {"text": "ok", "role": "user"},
        ]
        result = grade_messages(msgs)
        for m in result:
            self.assertIn("grade", m)
        self.assertIs(result, msgs, "grade_messages should modify in-place")

    def test_empty_text(self):
        entry = {"text": "", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 1)
        self.assertLessEqual(score, 10)

    def test_missing_text_key(self):
        """Entry with no 'text' key should not crash."""
        entry = {"role": "user"}
        score = grade_entry(entry)
        self.assertIsInstance(score, int)

    # --- Romanian-language fixtures ---
    # The grading system has Romanian patterns added in commit 76c6f57. These
    # cases lock in the expected scoring so a future regex tweak can't silently
    # break the user's primary content language.

    def test_romanian_decision_scores_high(self):
        # "Am decis să trec de la Stripe la Revolut" — explicit decision
        entry = {"text": "Am decis să trec de la Stripe la Revolut pentru toate facturile.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 7, f"Romanian decision should score >= 7, got {score}")

    def test_romanian_identity_scores_high(self):
        # "Sunt profesor de canto" — identity statement
        entry = {"text": "Sunt profesor de canto și locuiesc în Constanța.", "role": "user"}
        score = grade_entry(entry)
        self.assertGreaterEqual(score, 6, f"Romanian identity should score >= 6, got {score}")

    def test_romanian_noise_scores_low(self):
        # Bare acknowledgment
        entry = {"text": "ok mersi", "role": "user"}
        score = grade_entry(entry)
        self.assertLessEqual(score, 4, f"Romanian noise should score <= 4, got {score}")


# ===========================================================================
# Chunker tests
# ===========================================================================

class TestChunker(unittest.TestCase):
    """Test conversation-aware chunking."""

    def test_empty_input(self):
        chunks = chunk_messages([])
        self.assertEqual(chunks, [])

    def test_single_message(self):
        msgs = [{"text": "Hello world", "role": "user", "conversation_title": "Test", "timestamp": None}]
        chunks = chunk_messages(msgs, source_file="test.json")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_number"], 1)
        self.assertEqual(chunks[0]["total_chunks"], 1)
        self.assertEqual(chunks[0]["source_file"], "test.json")

    def test_chunk_has_required_keys(self):
        msgs = [{"text": "Hello world", "role": "user", "conversation_title": "T", "timestamp": None}]
        chunks = chunk_messages(msgs)
        required = {"chunk_number", "total_chunks", "date_range_start", "date_range_end",
                     "source_file", "token_estimate", "text"}
        for chunk in chunks:
            self.assertTrue(required.issubset(chunk.keys()), f"Missing keys: {required - chunk.keys()}")

    def test_token_estimate(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)
        self.assertEqual(estimate_tokens(""), 0)

    def test_date_range_extraction(self):
        ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 6, 15, tzinfo=timezone.utc)
        msgs = [
            {"text": "First", "role": "user", "conversation_title": "T", "timestamp": ts1},
            {"text": "Last", "role": "user", "conversation_title": "T", "timestamp": ts2},
        ]
        chunks = chunk_messages(msgs)
        self.assertEqual(chunks[0]["date_range_start"], "2026-01-01")
        self.assertEqual(chunks[0]["date_range_end"], "2026-06-15")

    def test_oversized_conversation_splits(self):
        """A massive conversation with many messages should split into multiple chunks."""
        # Create a conversation with many messages that exceeds TARGET_CHARS (120,000)
        # Single messages are never split (by design), so we need multiple messages
        msgs = [
            {"text": "word " * 5000, "role": "user", "conversation_title": "Big", "timestamp": None}
            for _ in range(10)
        ]  # ~250,000 chars total across 10 messages
        chunks = chunk_messages(msgs)
        self.assertGreater(len(chunks), 1, "Oversized conversation should produce multiple chunks")

    def test_single_huge_message_not_split(self):
        """A single oversized message stays whole — splitting mid-message loses context."""
        big_msg = "word " * 35000  # ~175,000 chars
        msgs = [{"text": big_msg, "role": "user", "conversation_title": "Big", "timestamp": None}]
        chunks = chunk_messages(msgs)
        self.assertEqual(len(chunks), 1, "Single message should never be split mid-text")

    def test_conversation_grouping(self):
        """Messages with same title should stay together."""
        msgs = [
            {"text": "A1", "role": "user", "conversation_title": "ConvoA", "timestamp": None},
            {"text": "A2", "role": "assistant", "conversation_title": "ConvoA", "timestamp": None},
            {"text": "B1", "role": "user", "conversation_title": "ConvoB", "timestamp": None},
        ]
        chunks = chunk_messages(msgs)
        # Should have one chunk with both conversations (small enough)
        self.assertEqual(len(chunks), 1)
        self.assertIn("ConvoA", chunks[0]["text"])
        self.assertIn("ConvoB", chunks[0]["text"])

    def test_none_title_messages_separate(self):
        """Messages with None title should each be their own group."""
        msgs = [
            {"text": "Msg1", "role": "user", "conversation_title": None, "timestamp": None},
            {"text": "Msg2", "role": "user", "conversation_title": None, "timestamp": None},
        ]
        chunks = chunk_messages(msgs)
        self.assertEqual(len(chunks), 1)  # Small enough for one chunk


# ===========================================================================
# Full pipeline test
# ===========================================================================

class TestFullPipeline(unittest.TestCase):
    """Test the full parse -> grade -> chunk pipeline."""

    def test_chatgpt_pipeline(self):
        msgs = detect_and_parse(FIXTURES / "chatgpt_sample.json")
        self.assertGreater(len(msgs), 0)

        graded = grade_messages(msgs)
        self.assertTrue(all("grade" in m for m in graded))

        high_value = [m for m in graded if m["grade"] >= 5]
        # Should have at least the decision message
        self.assertGreater(len(high_value), 0)

        chunks = chunk_messages(high_value, source_file="chatgpt_sample.json")
        self.assertGreater(len(chunks), 0)
        self.assertIn("text", chunks[0])

    def test_whatsapp_pipeline(self):
        msgs = detect_and_parse(FIXTURES / "whatsapp_sample.txt")
        self.assertGreater(len(msgs), 0)

        graded = grade_messages(msgs)
        chunks = chunk_messages(
            [m for m in graded if m["grade"] >= 5],
            source_file="whatsapp_sample.txt",
        )
        # At least some messages should survive grading
        self.assertIsInstance(chunks, list)

    def test_gemini_pipeline(self):
        msgs = detect_and_parse(FIXTURES / "gemini_sample.txt")
        self.assertGreater(len(msgs), 0)

        graded = grade_messages(msgs)
        chunks = chunk_messages(
            [m for m in graded if m["grade"] >= 5],
            source_file="gemini_sample.txt",
        )
        self.assertIsInstance(chunks, list)

    def test_empty_file_pipeline(self):
        """Empty file should produce zero chunks, no crashes."""
        msgs = detect_and_parse(FIXTURES / "empty_file.json")
        self.assertEqual(msgs, [])
        graded = grade_messages(msgs)
        self.assertEqual(graded, [])
        chunks = chunk_messages(graded)
        self.assertEqual(chunks, [])

    def test_malformed_json_pipeline(self):
        """Malformed JSON should not crash the pipeline."""
        msgs = detect_and_parse(FIXTURES / "malformed.json")
        self.assertIsInstance(msgs, list)
        graded = grade_messages(msgs)
        chunks = chunk_messages([m for m in graded if m.get("grade", 0) >= 5])
        self.assertIsInstance(chunks, list)

    def test_binary_garbage_pipeline(self):
        """Binary garbage should not crash the pipeline."""
        msgs = detect_and_parse(FIXTURES / "binary_garbage.json")
        self.assertIsInstance(msgs, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
