"""Payload packing helpers for cloud sync transport."""
from typing import Any

import msgpack
import zstandard


def pack_payload(data: dict[str, Any]) -> bytes:
    """Serialize and compress a payload for transport or storage."""
    packed = msgpack.packb(data, use_bin_type=True)
    return zstandard.ZstdCompressor(level=3).compress(packed)


def unpack_payload(blob: bytes) -> dict[str, Any]:
    """Decompress and deserialize a payload blob."""
    raw = zstandard.ZstdDecompressor().decompress(blob)
    return msgpack.unpackb(raw, raw=False)
