"""
parsers.py — Multi-format conversation parser for Kontext intake.

Converts raw files (ChatGPT JSON, Gemini text, WhatsApp exports,
plain text/markdown, PDF) into a unified list of message dicts.

Each message dict:
    {
        "source": "chatgpt" | "gemini" | "whatsapp" | "document",
        "timestamp": datetime | None,
        "role": "user" | "assistant" | "system",
        "text": "message content",
        "conversation_title": "optional title" | None,
    }

Python 3.10+, stdlib only.
"""

import json
import re
import zipfile
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# ChatGPT JSON export
# ---------------------------------------------------------------------------

def _walk_chatgpt_mapping(mapping: dict) -> list[dict]:
    """
    ChatGPT exports store messages in a nested mapping keyed by UUID.
    Each node has `parent`, `children`, and optionally `message`.
    We walk the tree depth-first to get messages in order.
    """
    # Build adjacency: find root nodes (no parent or parent not in mapping)
    children_map: dict[str, list[str]] = {}
    roots: list[str] = []

    for node_id, node in mapping.items():
        parent = node.get("parent")
        if parent and parent in mapping:
            children_map.setdefault(parent, []).append(node_id)
        else:
            roots.append(node_id)

    # DFS to collect messages in conversation order
    ordered: list[dict] = []
    stack = list(reversed(roots))  # process first root first

    while stack:
        nid = stack.pop()
        node = mapping.get(nid, {})
        msg = node.get("message")
        if msg and msg.get("content"):
            ordered.append(msg)
        # Push children in reverse so first child is processed first
        for child_id in reversed(children_map.get(nid, [])):
            stack.append(child_id)

    return ordered


def _extract_chatgpt_text(message: dict) -> str:
    """Pull text from a ChatGPT message's content object."""
    content = message.get("content", {})
    content_type = content.get("content_type", "")

    if content_type == "text":
        parts = content.get("parts", [])
        # Parts can be strings or dicts (tool results, images)
        texts = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                # Tool call — extract just the tool name
                tool_name = part.get("name") or part.get("tool_name")
                if tool_name:
                    texts.append(f"[tool: {tool_name}]")
        return "\n".join(texts).strip()

    if content_type == "multimodal_text":
        parts = content.get("parts", [])
        texts = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("content_type") == "image_asset_pointer":
                texts.append("[image]")
            elif isinstance(part, dict) and "text" in part:
                texts.append(part["text"])
        return "\n".join(texts).strip()

    # Fallback: try to get any text-like content
    if isinstance(content, str):
        return content
    return ""


def _map_chatgpt_role(author_role: str) -> str:
    """Map ChatGPT author roles to our standard roles."""
    if author_role == "user":
        return "user"
    if author_role in ("assistant", "tool"):
        return "assistant"
    return "system"


def parse_chatgpt_json(data: list | dict) -> list[dict]:
    """
    Parse a ChatGPT JSON export (list of conversation objects).
    If `data` is a single conversation dict, wrap it in a list.
    """
    if isinstance(data, dict):
        data = [data]

    messages: list[dict] = []

    for convo in data:
        title = convo.get("title", None)
        mapping = convo.get("mapping", {})

        ordered_msgs = _walk_chatgpt_mapping(mapping)

        for msg in ordered_msgs:
            author = msg.get("author", {})
            role_raw = author.get("role", "system")
            role = _map_chatgpt_role(role_raw)

            # Skip system messages — they're prompt scaffolding, not user content
            if role == "system":
                continue

            text = _extract_chatgpt_text(msg)
            if not text:
                continue

            # Timestamp
            ts = None
            create_time = msg.get("create_time")
            if create_time:
                try:
                    ts = datetime.fromtimestamp(create_time, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    pass

            messages.append({
                "source": "chatgpt",
                "timestamp": ts,
                "role": role,
                "text": text,
                "conversation_title": title,
            })

    return messages


def parse_chatgpt_file(path: Path) -> list[dict]:
    """
    Parse a ChatGPT export — either a .json file directly or a .zip
    containing conversations.json.
    """
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            # Look for conversations.json inside the zip
            candidates = [n for n in zf.namelist() if n.endswith("conversations.json")]
            if not candidates:
                # Fallback: any .json file
                candidates = [n for n in zf.namelist() if n.endswith(".json")]
            if not candidates:
                return []
            raw = zf.read(candidates[0])
            data = json.loads(raw)
            return parse_chatgpt_json(data)

    # Standalone .json
    # BUG FIX: handle empty files, binary garbage, and encoding errors
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return []
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    return parse_chatgpt_json(data)


# ---------------------------------------------------------------------------
# Gemini copy-paste (markdown / text)
# ---------------------------------------------------------------------------

# Patterns for turn markers. Gemini exports have no standard format,
# so we match common patterns: "You:", "Gemini:", "User:", "Model:", etc.
_GEMINI_TURN_RE = re.compile(
    r"^(?P<role>You|User|Human|Gemini|Model|Assistant|AI|Claude|Claude Code|Opus|Sonnet|Haiku)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)

# Optional timestamp at start of a line: [2026-04-04 12:00] or similar
_TIMESTAMP_RE = re.compile(
    r"^\[?(\d{4}[-/]\d{2}[-/]\d{2}[,\s]+\d{1,2}:\d{2}(?::\d{2})?)\]?\s*"
)


def _role_from_gemini_label(label: str) -> str:
    """Map Gemini turn labels to standard roles."""
    if label.lower() in ("you", "user", "human"):
        return "user"
    return "assistant"


def parse_gemini_text(text: str, title: Optional[str] = None) -> list[dict]:
    """
    Parse a Gemini-style conversation from plain text.
    Splits on turn markers like "You:" / "Gemini:".
    """
    messages: list[dict] = []

    # Find all turn markers and their positions
    matches = list(_GEMINI_TURN_RE.finditer(text))

    if not matches:
        # No turn markers found — treat as single document
        return parse_plain_text(text, title)

    for i, match in enumerate(matches):
        role_label = match.group("role")
        role = _role_from_gemini_label(role_label)

        # Content runs from end of this marker to start of next marker (or EOF)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        if not content:
            continue

        # Check for timestamp in the content
        ts = None
        ts_match = _TIMESTAMP_RE.match(content)
        if ts_match:
            try:
                ts_str = ts_match.group(1).replace("/", "-")
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d, %H:%M:%S"):
                    try:
                        ts = datetime.strptime(ts_str, fmt)
                        break
                    except ValueError:
                        continue
            except (ValueError, IndexError):
                pass
            if ts:
                content = content[ts_match.end():].strip()

        messages.append({
            "source": "gemini",
            "timestamp": ts,
            "role": role,
            "text": content,
            "conversation_title": title,
        })

    return messages


# ---------------------------------------------------------------------------
# WhatsApp TXT export
# ---------------------------------------------------------------------------

# WhatsApp format: [DD/MM/YYYY, HH:MM:SS] Name: message
# Some exports omit seconds: [DD/MM/YYYY, HH:MM]
_WA_LINE_RE = re.compile(
    r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]?\s*[-–]?\s*(.+?):\s(.+)",
    re.DOTALL,
)

# System messages (group changes, encryption notices, etc.)
_WA_SYSTEM_MARKERS = [
    "messages and calls are end-to-end encrypted",
    "created group",
    "added you",
    "changed the subject",
    "changed this group",
    "left",
    "removed",
    "changed the group description",
    "pinned a message",
]


def parse_whatsapp_file(path: Path) -> list[dict]:
    """
    Parse a WhatsApp .txt export.
    Multi-line messages: lines not matching the timestamp pattern
    are appended to the previous message.
    """
    # BUG FIX: handle encoding errors for WhatsApp exports
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return []

    messages: list[dict] = []
    current: Optional[dict] = None

    for line in lines:
        match = _WA_LINE_RE.match(line)
        if match:
            # Save previous message
            if current and current["text"].strip():
                messages.append(current)

            date_str = match.group(1)
            time_str = match.group(2)
            name = match.group(3).strip()
            text = match.group(4).strip()

            # Skip system messages
            if any(marker in text.lower() for marker in _WA_SYSTEM_MARKERS):
                current = None
                continue

            # Skip media placeholders
            if text in ("<Media omitted>", "<media omitted>"):
                text = "[media omitted]"

            # Parse timestamp
            ts = None
            try:
                dt_str = f"{date_str} {time_str}"
                for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%y %H:%M:%S",
                            "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M",
                            "%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
                    try:
                        ts = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        continue
            except (ValueError, IndexError):
                pass

            current = {
                "source": "whatsapp",
                "timestamp": ts,
                "role": "user",  # All WhatsApp messages are "user" — sender stored in text
                "text": f"[{name}] {text}",
                "conversation_title": None,
            }
        else:
            # Continuation of previous message (multi-line)
            if current:
                current["text"] += "\n" + line.rstrip()

    # Don't forget the last message
    if current and current["text"].strip():
        messages.append(current)

    return messages


# ---------------------------------------------------------------------------
# Plain text / Markdown
# ---------------------------------------------------------------------------

def parse_plain_text(text: str, title: Optional[str] = None) -> list[dict]:
    """
    Parse a plain text or markdown file as a document.
    Split by headings (## / ###) or double-newline paragraph breaks.
    Each chunk becomes a single "document" entry.
    """
    messages: list[dict] = []

    # Split on markdown headings or double newlines
    # Prefer heading splits for structured documents
    heading_pattern = re.compile(r"\n(?=#{1,3}\s)")
    sections = heading_pattern.split(text)

    if len(sections) <= 1:
        # No headings — split on double newlines (paragraphs)
        sections = re.split(r"\n\s*\n", text)

    for section in sections:
        section = section.strip()
        if not section:
            continue
        messages.append({
            "source": "document",
            "timestamp": None,
            "role": "user",
            "text": section,
            "conversation_title": title,
        })

    return messages


def parse_plain_file(path: Path) -> list[dict]:
    """Read a text/markdown file and parse it."""
    # BUG FIX: handle empty files and encoding errors
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        return []
    if not text.strip():
        return []
    return parse_plain_text(text, title=path.stem)


# ---------------------------------------------------------------------------
# PDF (stub — stdlib can't read PDFs)
# ---------------------------------------------------------------------------

def parse_pdf_file(path: Path) -> list[dict]:
    """
    Attempt to parse a PDF file.
    Python stdlib has no PDF reader, so we try optional libraries
    and fall back to a clear error message.
    """
    # Try PyPDF2
    try:
        import PyPDF2  # type: ignore
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text_parts = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(extracted)
        if text_parts:
            return parse_plain_text("\n\n".join(text_parts), title=path.stem)
    except ImportError:
        pass

    # Try pdfplumber
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            text_parts = []
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(extracted)
        if text_parts:
            return parse_plain_text("\n\n".join(text_parts), title=path.stem)
    except ImportError:
        pass

    # No library available — return a flag message
    return [{
        "source": "document",
        "timestamp": None,
        "role": "system",
        "text": (
            f"[PDF: {path.name}] Could not extract text. "
            "Install PyPDF2 or pdfplumber, or convert to .txt manually."
        ),
        "conversation_title": path.stem,
    }]


# ---------------------------------------------------------------------------
# Router — detect format and dispatch to the right parser
# ---------------------------------------------------------------------------

def detect_and_parse(path: Path) -> list[dict]:
    """
    Auto-detect file format and parse it into unified messages.
    Returns an empty list if the file can't be parsed.
    """
    suffix = path.suffix.lower()

    # ZIP — likely ChatGPT export
    if suffix == ".zip":
        return parse_chatgpt_file(path)

    # JSON — likely ChatGPT export
    if suffix == ".json":
        try:
            return parse_chatgpt_file(path)
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            # BUG FIX: also catch UnicodeDecodeError for binary files with .json extension
            # Not a ChatGPT export — treat as plain text
            try:
                return parse_plain_file(path)
            except UnicodeDecodeError:
                return []

    # PDF
    if suffix == ".pdf":
        return parse_pdf_file(path)

    # Text and Markdown — check for conversation patterns first
    if suffix in (".txt", ".text"):
        # BUG FIX: handle binary files and empty files with .txt extension
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            return []

        if not text.strip():
            return []

        # Check if it's a WhatsApp export
        if _WA_LINE_RE.search(text[:500]):
            return parse_whatsapp_file(path)

        # Check if it's a Gemini-style conversation
        if _GEMINI_TURN_RE.search(text[:1000]):
            return parse_gemini_text(text, title=path.stem)

        # Plain text
        return parse_plain_text(text, title=path.stem)

    if suffix in (".md", ".markdown"):
        # BUG FIX: handle binary files and empty files with .md extension
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            return []

        if not text.strip():
            return []

        # Check for conversation patterns in markdown too
        if _GEMINI_TURN_RE.search(text[:1000]):
            return parse_gemini_text(text, title=path.stem)

        return parse_plain_text(text, title=path.stem)

    # Unknown format — try as plain text
    try:
        return parse_plain_file(path)
    except (UnicodeDecodeError, PermissionError):
        return [{
            "source": "document",
            "timestamp": None,
            "role": "system",
            "text": f"[Unsupported file: {path.name}] Could not parse this format.",
            "conversation_title": None,
        }]
