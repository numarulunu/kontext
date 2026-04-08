"""
chunker.py — Conversation-aware chunking for Kontext intake.

Takes a unified message list (from parsers.py) and splits it into
chunks suitable for subagent processing.

Rules:
- Target chunk size: 30,000 tokens (~120,000 chars)
- Never split a conversation in half — keep conversation boundaries
- 2,000 token overlap (repeat last conversation of previous chunk)
- Oversized single conversations: split at paragraph breaks
- Each chunk gets metadata: chunk_number, total_chunks, date_range, source_file

Python 3.10+, stdlib only.
"""

import logging
from datetime import datetime
from typing import Optional

_log = logging.getLogger("kontext.chunker")


# Rough token estimate: 1 token ~ 4 chars (conservative for English text)
CHARS_PER_TOKEN = 4
TARGET_TOKENS = 30_000
TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN  # 120,000
OVERLAP_TOKENS = 2_000
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 8,000


def estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return len(text) // CHARS_PER_TOKEN


def _group_by_conversation(messages: list[dict]) -> list[list[dict]]:
    """
    Group messages into conversations.
    A conversation = all consecutive messages with the same conversation_title.
    If title is None, each message is its own "conversation".
    """
    if not messages:
        return []

    groups: list[list[dict]] = []
    current_group: list[dict] = [messages[0]]
    current_title = messages[0].get("conversation_title")

    for msg in messages[1:]:
        msg_title = msg.get("conversation_title")

        # Same conversation if titles match and are not None
        if msg_title is not None and msg_title == current_title:
            current_group.append(msg)
        else:
            groups.append(current_group)
            current_group = [msg]
            current_title = msg_title

    if current_group:
        groups.append(current_group)

    return groups


def _conversation_text(convo: list[dict]) -> str:
    """Render a conversation group as text for chunking."""
    lines = []
    title = convo[0].get("conversation_title")
    if title:
        lines.append(f"## {title}")
        lines.append("")

    for msg in convo:
        role = msg.get("role", "user").upper()
        ts = msg.get("timestamp")
        ts_str = f" [{ts.strftime('%Y-%m-%d %H:%M')}]" if isinstance(ts, datetime) else ""
        lines.append(f"**{role}**{ts_str}:")
        lines.append(msg.get("text", ""))
        lines.append("")

    return "\n".join(lines)


def _split_oversized_conversation(convo: list[dict], max_chars: int) -> list[list[dict]]:
    """
    If a single conversation exceeds max_chars, split it at natural
    paragraph breaks within messages. Returns a list of sub-groups.
    """
    # Pre-render each message exactly once and reuse the size — previously this
    # function called _conversation_text on every message in addition to the
    # full-convo render, an O(n²) string-build pass on huge conversations.
    msg_sizes = [len(_conversation_text([m])) for m in convo]
    if sum(msg_sizes) <= max_chars:
        return [convo]

    # Split at message boundaries first
    sub_groups: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0

    for msg, msg_size in zip(convo, msg_sizes):
        if current_size + msg_size > max_chars and current:
            sub_groups.append(current)
            current = []
            current_size = 0

        # If a single message exceeds max_chars, we still include it whole
        # (splitting mid-message loses context)
        current.append(msg)
        current_size += msg_size

    if current:
        sub_groups.append(current)

    return sub_groups


def _date_range(conversations: list[list[dict]]) -> tuple[Optional[str], Optional[str]]:
    """Extract earliest and latest timestamps from a list of conversation groups."""
    timestamps = []
    for convo in conversations:
        for msg in convo:
            ts = msg.get("timestamp")
            if isinstance(ts, datetime):
                timestamps.append(ts)

    if not timestamps:
        return (None, None)

    earliest = min(timestamps).strftime("%Y-%m-%d")
    latest = max(timestamps).strftime("%Y-%m-%d")
    return (earliest, latest)


def chunk_messages(
    messages: list[dict],
    source_file: str = "unknown",
) -> list[dict]:
    """
    Split messages into chunks for subagent processing.

    Returns a list of chunk dicts:
    {
        "chunk_number": int,
        "total_chunks": int,  # filled in after all chunks are built
        "date_range_start": str | None,
        "date_range_end": str | None,
        "source_file": str,
        "token_estimate": int,
        "text": str,  # the rendered chunk content
    }
    """
    # Step 1: Group by conversation
    conversations = _group_by_conversation(messages)

    # Step 2: Split oversized conversations
    split_convos: list[list[dict]] = []
    for convo in conversations:
        split_convos.extend(_split_oversized_conversation(convo, TARGET_CHARS))

    # Step 3: Pack conversations into chunks, respecting size limits
    chunks: list[dict] = []
    current_convos: list[list[dict]] = []
    current_size = 0

    for convo in split_convos:
        convo_text = _conversation_text(convo)
        convo_size = len(convo_text)

        if current_size + convo_size > TARGET_CHARS and current_convos:
            # Finalize current chunk
            chunk_text = "\n---\n\n".join(
                _conversation_text(c) for c in current_convos
            )
            dr_start, dr_end = _date_range(current_convos)
            chunks.append({
                "chunk_number": len(chunks) + 1,
                "total_chunks": 0,  # filled in later
                "date_range_start": dr_start,
                "date_range_end": dr_end,
                "source_file": source_file,
                "token_estimate": estimate_tokens(chunk_text),
                "text": chunk_text,
            })

            # Overlap: carry the last conversation into the next chunk
            last_convo = current_convos[-1]
            last_convo_size = len(_conversation_text(last_convo))
            if last_convo_size <= OVERLAP_CHARS:
                current_convos = [last_convo]
                current_size = last_convo_size
            else:
                # Last conversation is too big to use as overlap — note it so
                # downstream chunks won't lose continuity silently. Caller can
                # see this in the chunker log if they care about coverage.
                _log.warning(
                    "chunk overlap dropped: last conversation %d chars > OVERLAP_CHARS %d",
                    last_convo_size, OVERLAP_CHARS,
                )
                current_convos = []
                current_size = 0

        current_convos.append(convo)
        current_size += convo_size

    # Final chunk
    if current_convos:
        chunk_text = "\n---\n\n".join(
            _conversation_text(c) for c in current_convos
        )
        dr_start, dr_end = _date_range(current_convos)
        chunks.append({
            "chunk_number": len(chunks) + 1,
            "total_chunks": 0,
            "date_range_start": dr_start,
            "date_range_end": dr_end,
            "source_file": source_file,
            "token_estimate": estimate_tokens(chunk_text),
            "text": chunk_text,
        })

    # Fill in total_chunks
    total = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total

    return chunks
