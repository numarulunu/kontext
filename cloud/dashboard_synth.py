"""Haiku-backed synthesis for dashboard entries.

The Kontext dashboard shows a `why` (what this entry is used for) and a
`body` (deduped synthesis of facts) per memory file. Both fields need real
intelligence — naive concatenation of raw facts looks like log output.

This module runs a short Claude Haiku 4.5 call per entry, parallel via a
small thread pool, and persists the result in a SQLite cache keyed on
content hash. A fact set that hasn't changed never costs a second token.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
MAX_PARALLEL = 6
MAX_FACTS_PER_ENTRY = 40
MAX_FACT_BLOCK_CHARS = 5000

SYSTEM_PROMPT = """You summarize entries from a personal AI memory library.

Each entry is a markdown file holding facts that an AI assistant has learned about the library's owner. Your job is to distill one entry into two short strings.

Write in second person ("you"). Do not quote timestamps, counts, or file names. No preamble, no hedging, no quotation marks around the output fields.

- `why`: one sentence, <=120 characters, stating what this entry is used for in the owner's AI sessions. Example: "calibrates tone and response length across every session."
- `body`: one or two sentences, <=280 characters total, synthesizing the most load-bearing, deduped facts. Strip boilerplate. Keep only the details that would change how an AI responds to this person."""


class _Synth(BaseModel):
    why: str = Field(max_length=200)
    body: str = Field(max_length=400)


def _content_hash(facts: list[str]) -> str:
    joined = "\n".join(f.strip().lower() for f in facts if f and f.strip())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_synth_cache (
            file TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            why TEXT NOT NULL,
            body TEXT NOT NULL,
            model TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0
        )
        """
    )


def _read_cached(conn: sqlite3.Connection, file: str) -> tuple[str, str, str] | None:
    row = conn.execute(
        "SELECT content_hash, why, body FROM dashboard_synth_cache WHERE file = ?",
        (file,),
    ).fetchone()
    return row if row is None else (row[0], row[1], row[2])


def _write_cache(
    conn: sqlite3.Connection,
    file: str,
    content_hash: str,
    why: str,
    body: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    conn.execute(
        """
        INSERT INTO dashboard_synth_cache
            (file, content_hash, why, body, model, generated_at, tokens_in, tokens_out)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)
        ON CONFLICT(file) DO UPDATE SET
            content_hash = excluded.content_hash,
            why          = excluded.why,
            body         = excluded.body,
            model        = excluded.model,
            generated_at = excluded.generated_at,
            tokens_in    = excluded.tokens_in,
            tokens_out   = excluded.tokens_out
        """,
        (file, content_hash, why, body, model, tokens_in, tokens_out),
    )


def _build_user_message(file: str, type_: str, facts: list[str]) -> str:
    clean = [f.strip() for f in facts if f and f.strip()][:MAX_FACTS_PER_ENTRY]
    block = "\n".join(f"- {f}" for f in clean)
    if len(block) > MAX_FACT_BLOCK_CHARS:
        block = block[:MAX_FACT_BLOCK_CHARS] + "\n- …"
    return f"File: {file}\nType: {type_}\n\nFacts:\n{block}"


def _synthesize_one(client, file: str, type_: str, facts: list[str]) -> dict[str, Any]:
    user_msg = _build_user_message(file, type_, facts)
    resp = client.messages.parse(
        model=HAIKU_MODEL,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        output_format=_Synth,
    )
    parsed: _Synth = resp.parsed_output
    tokens_in = int(getattr(resp.usage, "input_tokens", 0) or 0) + int(
        getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    )
    tokens_out = int(getattr(resp.usage, "output_tokens", 0) or 0)
    return {
        "why": parsed.why[:120],
        "body": parsed.body[:280],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def _fallback(facts: list[str]) -> dict[str, str]:
    head = " ".join(f.strip() for f in facts[:2] if f and f.strip())
    return {
        "why": f"{len([f for f in facts if f and f.strip()])} facts captured",
        "body": (head[:277] + "…") if len(head) > 280 else head,
    }


def synthesize_entries(
    db_path: str,
    entries: list[tuple[str, str, list[str]]],
) -> dict[str, dict[str, str]]:
    """Return {file: {why, body}} for every entry.

    Cache hits return instantly. Misses go to Haiku in parallel. If the
    ANTHROPIC_API_KEY env var is missing, misses fall back to a trivial
    "N facts captured" / first-two-facts body so the dashboard still
    renders.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    results: dict[str, dict[str, str]] = {}
    need_synth: list[tuple[str, str, list[str], str]] = []

    # Use check_same_thread=False so reads can happen from worker threads;
    # writes are serialized behind a lock.
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    try:
        _ensure_cache_table(conn)
        conn.commit()

        for file, type_, facts in entries:
            h = _content_hash(facts)
            cached = _read_cached(conn, file)
            if cached is not None and cached[0] == h:
                results[file] = {"why": cached[1], "body": cached[2]}
            else:
                need_synth.append((file, type_, facts, h))

        if not need_synth:
            return results

        if not api_key:
            log.info("dashboard_synth: no ANTHROPIC_API_KEY; using fallback for %d entries", len(need_synth))
            for file, _type, facts, _h in need_synth:
                results[file] = _fallback(facts)
            return results

        import anthropic  # imported lazily so snapshot never fails on a missing dep

        client = anthropic.Anthropic(api_key=api_key)
        fresh: list[tuple[str, str, dict[str, Any]]] = []

        def work(file: str, type_: str, facts: list[str], h: str):
            try:
                out = _synthesize_one(client, file, type_, facts)
                return (file, h, out, None)
            except anthropic.APIError as exc:
                log.warning("dashboard_synth API error for %s: %s", file, exc)
                return (file, h, None, exc)
            except Exception as exc:
                log.exception("dashboard_synth unexpected error for %s", file)
                return (file, h, None, exc)

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = [ex.submit(work, f, t, fa, h) for f, t, fa, h in need_synth]
            for fut in as_completed(futures):
                file, h, out, _exc = fut.result()
                if out is None:
                    facts = next((fa for f, _t, fa, _h in need_synth if f == file), [])
                    results[file] = _fallback(facts)
                else:
                    fresh.append((file, h, out))
                    results[file] = {"why": out["why"], "body": out["body"]}

        # Serialize writes on a lock; SQLite is thread-safe under the default
        # serialized threading mode but pysqlite still wants one writer at a time.
        write_lock = threading.Lock()
        with write_lock:
            for file, h, out in fresh:
                _write_cache(
                    conn,
                    file,
                    h,
                    out["why"],
                    out["body"],
                    HAIKU_MODEL,
                    out["tokens_in"],
                    out["tokens_out"],
                )
            conn.commit()

        return results
    finally:
        conn.close()
