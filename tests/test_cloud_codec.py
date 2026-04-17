"""Tests for cloud codec, crypto, and manifest helpers."""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.codec import pack_payload, unpack_payload
from cloud.crypto import open_payload, seal_payload
from cloud.manifest import validate_manifest
from cloud.models import EnrollmentRequest, HistoryEnvelope


def test_history_envelope_stores_required_fields():
    envelope = HistoryEnvelope(
        op_id="op-1",
        workspace_id="ws-1",
        device_id="dev-1",
        op_kind="prompt.logged",
        entity_type="prompt",
        entity_id="prompt-1",
        created_at="2026-04-16T10:00:00Z",
        payload={"content": "hello"},
    )

    assert envelope.op_id == "op-1"
    assert envelope.payload["content"] == "hello"


def test_enrollment_request_accepts_valid_device_class():
    req = EnrollmentRequest(
        workspace_id="ws-1",
        enrollment_code="abc123",
        label="Laptop",
        device_class="interactive",
        device_public_key="cHVia2V5",
    )

    assert req.device_class == "interactive"
    assert req.label == "Laptop"


def test_enrollment_request_rejects_invalid_device_class():
    with pytest.raises(ValidationError):
        EnrollmentRequest(
            workspace_id="ws-1",
            enrollment_code="abc123",
            label="Laptop",
            device_class="tablet",
            device_public_key="cHVia2V5",
        )


def test_codec_round_trip():
    original = {
        "fact": "Name: Alice",
        "grade": 9,
        "tags": ["identity", "active"],
    }

    blob = pack_payload(original)
    restored = unpack_payload(blob)

    assert isinstance(blob, bytes)
    assert restored == original


def test_secretbox_round_trip():
    ciphertext = seal_payload(b"a" * 32, b"hello", nonce=b"b" * 24)
    plaintext = open_payload(b"a" * 32, ciphertext, nonce=b"b" * 24)

    assert plaintext == b"hello"


def test_secretbox_rejects_wrong_key():
    ciphertext = seal_payload(b"a" * 32, b"hello", nonce=b"b" * 24)

    with pytest.raises(Exception):
        open_payload(b"c" * 32, ciphertext, nonce=b"b" * 24)


def test_validate_manifest_accepts_identical_versions():
    local = {
        "schema_version": 12,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "ranking_version": "v1",
        "prompt_routing_version": "v1",
    }

    validate_manifest(local, dict(local))


def test_validate_manifest_rejects_mismatch():
    local = {
        "schema_version": 12,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "ranking_version": "v1",
        "prompt_routing_version": "v1",
    }
    remote = dict(local)
    remote["embedding_model"] = "sentence-transformers/all-mpnet-base-v2"

    with pytest.raises(ValueError, match="embedding_model"):
        validate_manifest(local, remote)
