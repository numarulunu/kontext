"""
Kontext MCP Server Ã¢â‚¬” Semantic memory retrieval for Claude Code.

Runs locally for retrieval. Optional cloud sync is exposed through separate tools. Embeds memory file descriptions
and returns the most relevant files for any query.

Replaces keyword matching with meaning matching.

Usage:
    python mcp_server.py                    # Start server (default port 3945)
    python mcp_server.py --port 8080        # Custom port
    python mcp_server.py --reindex          # Force re-embed all descriptions

Protocol: Claude Code MCP (stdio transport)
"""

import json
import logging
import logging.handlers
import os
import sys
import re
from pathlib import Path
from datetime import datetime

# Set up file logging Ã¢â‚¬” every write, every failure, every tool call.
# Rotating handler: 1 MB per file, 2 backups = hard cap ~3 MB.
_LOG_FILE = Path(__file__).parent / "_kontext.log"
_logger = logging.getLogger("kontext")
_logger.setLevel(logging.INFO)
_file_handler = logging.handlers.RotatingFileHandler(
    str(_LOG_FILE), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(_file_handler)

# Lazy imports Ã¢â‚¬” only load heavy libs when needed
_model = None
_embeddings_cache = {}
_cache_file = None

# Lazy DB instance -- only initialized when database tools are called
_db_instance = None


def _get_db():
    """Lazy-load the database. Avoids import errors if db.py has issues."""
    global _db_instance
    if _db_instance is None:
        from db import KontextDB
        _db_instance = KontextDB()
        _logger.info(f"DB INITIALIZED: {_db_instance.db_path}")
    return _db_instance



def find_memory_dir() -> Path:
    """Auto-detect the memory directory."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    candidates = []
    for project_dir in claude_dir.iterdir():
        if project_dir.is_dir():
            mem = project_dir / "memory"
            if mem.exists() and (mem / "MEMORY.md").exists():
                file_count = len(list(mem.glob("*.md")))
                candidates.append((mem, file_count))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def get_model():
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        # all-MiniLM-L6-v2: 80MB, fast, good quality. Runs on CPU.
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def load_cache(cache_path: Path) -> dict:
    """Load cached embeddings from disk."""
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(cache_path: Path, cache: dict):
    """Save embeddings cache to disk."""
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def parse_memory_index(memory_dir: Path) -> list[dict]:
    """Parse MEMORY.md and extract file entries with descriptions."""
    index_path = memory_dir / "MEMORY.md"
    if not index_path.exists():
        return []

    entries = []
    content = index_path.read_text(encoding="utf-8")

    for line in content.split("\n"):
        # Match: - [Title](filename.md) Ã¢â‚¬” description
        match = re.match(r"^-\s+\[(.+?)\]\((.+?\.md)\)\s*(.+)$", line.strip())
        if match:
            title = match.group(1)
            filename = match.group(2)
            description = re.sub(r"^[^A-Za-z0-9]+", "", match.group(3).strip())
            filepath = memory_dir / filename

            if filepath.exists():
                # Read first 500 chars of file content for richer embedding
                try:
                    content_preview = filepath.read_text(encoding="utf-8")[:500]
                    # Strip frontmatter
                    if content_preview.startswith("---"):
                        end = content_preview.find("---", 3)
                        if end > 0:
                            content_preview = content_preview[end+3:].strip()
                except OSError:
                    content_preview = ""

                entries.append({
                    "title": title,
                    "filename": filename,
                    "path": str(filepath),
                    "description": description,
                    # Combine title + description + content preview for rich embedding
                    "embed_text": f"{title}: {description}\n{content_preview}",
                })

    return entries


def index_memories(memory_dir: Path, force: bool = False) -> list[dict]:
    """Embed all memory file descriptions. Uses cache to avoid re-embedding unchanged entries."""
    global _embeddings_cache, _cache_file

    _cache_file = memory_dir / "_embeddings_cache.json"
    _embeddings_cache = load_cache(_cache_file)

    entries = parse_memory_index(memory_dir)
    if not entries:
        return []

    model = get_model()
    updated = False

    for entry in entries:
        cache_key = entry["filename"]
        cached = _embeddings_cache.get(cache_key)

        # Re-embed if: forced, not cached, or description changed
        if force or not cached or cached.get("text") != entry["embed_text"]:
            embedding = model.encode(entry["embed_text"]).tolist()
            _embeddings_cache[cache_key] = {
                "text": entry["embed_text"],
                "embedding": embedding,
                "indexed_at": datetime.now().isoformat(),
            }
            updated = True

        entry["embedding"] = _embeddings_cache[cache_key]["embedding"]

    if updated:
        save_cache(_cache_file, _embeddings_cache)

    return entries


def search(query: str, entries: list[dict], top_k: int = 6) -> list[dict]:
    """Find the most relevant memory files for a query."""
    if not entries:
        return []

    try:
        model = get_model()
        import numpy as np
    except Exception:
        # Graceful fallback: keyword match when sentence-transformers unavailable
        query_lower = query.lower()
        query_words = set(query_lower.split())
        results = []
        for entry in entries:
            text = f"{entry.get('title', '')} {entry.get('description', '')} {entry.get('filename', '')}".lower()
            overlap = sum(1 for w in query_words if w in text)
            if overlap > 0:
                results.append({
                    "filename": entry["filename"],
                    "title": entry["title"],
                    "path": entry["path"],
                    "score": overlap / max(len(query_words), 1),
                    "description": entry["description"],
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    query_embedding = model.encode(query)

    results = []
    for entry in entries:
        entry_embedding = np.array(entry["embedding"])
        # Cosine similarity
        similarity = np.dot(query_embedding, entry_embedding) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(entry_embedding)
        )
        results.append({
            "filename": entry["filename"],
            "title": entry["title"],
            "path": entry["path"],
            "score": float(similarity),
            "description": entry["description"],
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]



def _mcp_result(req_id, text: str) -> dict:
    """Helper: wrap text in an MCP tool result."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}]},
    }


def _mcp_error(req_id, text: str) -> dict:
    """Helper: wrap text in an MCP error result."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": f"Error: {text}"}]},
    }


# ---------------------------------------------------------------------------
# Tool definitions for tools/list
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS = [
    {
        "name": "kontext_search",
        "description": (
            "Search memory files by meaning. Returns the most relevant files for any query."
            " Use mode='index' for a compact cost-aware summary before deciding which files to load."
            " Use mode='full' (default) for descriptions + file paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you are looking for Ã¢â‚¬” a topic, question, or user message",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5,
                },
                "mode": {
                    "type": "string",
                    "enum": ["full", "index"],
                    "description": (
                        "full (default) = file paths + descriptions. "
                        "index = compact: fact count, top grade, top fact preview, "
                        "estimated token cost Ã¢â‚¬” lets you choose which files are worth loading."
                    ),
                    "default": "full",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "kontext_reindex",
        "description": "Re-index all memory files. Run after adding new files or changing descriptions in MEMORY.md.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "kontext_write",
        "description": "Write a memory entry to the database and auto-export to flat markdown files. Use this to store new facts, decisions, or corrections.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Target memory file (e.g. user_identity.md)"},
                "fact": {"type": "string", "description": "The fact, decision, or observation to store"},
                "source": {"type": "string", "description": "Where this came from (e.g. user stated, inferred)"},
                "grade": {"type": "number", "description": "Importance score 1-10 (default 5)", "default": 5},
                "tier": {"type": "string", "description": "Entry tier: active, historical, or cold (default active)", "default": "active"},
            },
            "required": ["file", "fact"],
        },
    },
    {
        "name": "kontext_query",
        "description": "Query memory entries from the database. Filter by file, tier, minimum grade, or search text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Filter by memory file name"},
                "tier": {"type": "string", "description": "Filter by tier: active, historical, or cold"},
                "min_grade": {"type": "number", "description": "Minimum grade to include"},
                "search": {"type": "string", "description": "Full-text search on fact content"},
                "semantic": {"type": "boolean", "description": "Use embedding-based semantic search fused with FTS5 via reciprocal-rank fusion (default: true; set false to force keyword-only). Falls back to keyword search if sentence-transformers is unavailable.", "default": True},
                "top_k": {"type": "integer", "description": "Max results when semantic=true (default 10)", "default": 10},
            },
        },
    },
    {
        "name": "kontext_relate",
        "description": "Query the knowledge graph. Find everything connected to an entity (person, tool, platform) up to N hops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name to look up (e.g. Stripe, a person, a tool)"},
                "depth": {"type": "integer", "description": "How many hops to traverse (default 2)", "default": 2},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "kontext_recent",
        "description": "Get memory entries changed in the last N hours. Useful for seeing what was recently updated.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Look back this many hours (default 24)", "default": 24},
            },
        },
    },
    {
        "name": "kontext_dream",
        "description": "Run memory consolidation (dream cycle). Deduplicates, normalizes dates, auto-resolves stale conflicts, compresses cold entries, purges dead ones.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "description": "Report what would change without modifying (default false)", "default": False},
                "phase": {"type": "string", "description": "Run a single phase instead of all", "enum": ["dedup", "normalize", "resolve", "compress", "purge"]},
            },
        },
    },
    {
        "name": "kontext_digest",
        "description": "Process conversation digests from the Backup System into memory candidates. Extracts facts, decisions, corrections, and status changes from past conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "auto": {"type": "boolean", "description": "Auto-import high-confidence facts (grade >= min_grade). Default false Ã¢â‚¬” writes candidates file for review.", "default": False},
                "dry_run": {"type": "boolean", "description": "Show what would be extracted without modifying (default false)", "default": False},
                "min_grade": {"type": "integer", "description": "Minimum grade for auto-import (default 8)", "default": 8},
            },
        },
    },
    {
        "name": "kontext_decay",
        "description": "Run score decay on stale memory entries. Reduces grade of entries not accessed recently. Auto-exports affected files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_threshold": {"type": "integer", "description": "Entries not accessed in this many days get decayed (default 60)", "default": 60},
                "decay_amount": {"type": "number", "description": "How much to reduce the grade by (default 0.5)", "default": 0.5},
            },
        },
    },
    {
        "name": "kontext_session",
        "description": "Save or retrieve session state. Use save to bookmark where you are, get to resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "save to store session state, get to retrieve the latest", "enum": ["save", "get"]},
                "workspace": {"type": "string", "description": "Current repo root or cwd. Required for safe save/get."},
                "project": {"type": "string", "description": "Project name (for save)"},
                "status": {"type": "string", "description": "Current status summary (for save)"},
                "next_step": {"type": "string", "description": "What to do next (for save)"},
                "key_decisions": {"type": "string", "description": "Important decisions made this session (for save)"},
                "summary": {"type": "string", "description": "2-3 sentence conversation summary Ã¢â‚¬” what was discussed, tone, direction (for save)"},
                "files_touched": {"type": "string", "description": "Comma-separated list of files edited or discussed this session (for save)"},
            },
            "required": ["action"],
        },
    },
       {
        "name": "kontext_cloud_status",
        "description": "Show cloud link status, workspace, device, and the current sync cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "kontext_cloud_link",
        "description": "Link this Kontext database to a cloud control plane workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server_url": {"type": "string", "description": "Cloud control plane base URL"},
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "workspace_name": {"type": "string", "description": "Optional workspace display name"},
                "recovery_key_id": {"type": "string", "description": "Optional recovery key id"},
                "device_id": {"type": "string", "description": "Optional device identifier"},
                "label": {"type": "string", "description": "Device label for this machine"},
                "device_class": {
                    "type": "string",
                    "enum": ["interactive", "server"],
                    "description": "Device type for quota enforcement",
                    "default": "interactive"
                }
            },
            "required": ["server_url", "workspace_id"],
        },
    },
    {
        "name": "kontext_cloud_sync",
        "description": "Push local history ops, pull remote history ops, and update the sync cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Per-request pull page size (default 500)", "default": 500}
            },
        },
    },
    {
        "name": "kontext_cloud_recover",
        "description": "Replay the full remote history lane into this local database.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Per-request pull page size (default 500)", "default": 500}
            },
        },
    },
    {
        "name": "kontext_conflicts",
        "description": "Detect and manage memory conflicts. Use detect to find contradictions, list to see pending, resolve to mark resolved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "detect, list, or resolve", "enum": ["detect", "list", "resolve"]},
                "file": {"type": "string", "description": "Filter by file (for detect)"},
                "conflict_id": {"type": "integer", "description": "Conflict ID (for resolve)"},
                "resolution": {"type": "string", "description": "Resolution text (for resolve)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "kontext_prompts",
        "description": (
            "Search or list past user prompts. Useful for resuming context from a past session"
            " or finding when a topic was discussed. Results are ordered newest-first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Keyword to search (FTS5 trigram; leave blank for recent)",
                },
                "hours": {
                    "type": "number",
                    "description": "Restrict to prompts from the last N hours",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 20, max 100)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# MCP Protocol (stdio transport)
# ---------------------------------------------------------------------------

def handle_request(request: dict, memory_dir: Path, entries: list[dict]) -> dict:
    """Handle a single MCP JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "kontext-memory", "version": "6.1.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    elif method == "notifications/initialized":
        return None  # No response needed

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": _TOOL_DEFINITIONS},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        if tool_name == "kontext_search":
            query = args.get("query", "")
            top_k = min(args.get("top_k", 5), 20)
            mode = args.get("mode", "full")

            if not query:
                return _mcp_error(req_id, "query is required")
            truncated = False
            if len(query) > 500:
                query = query[:500]
                truncated = True

            results = search(query, entries, top_k)
            header = f"**Kontext Search:** {len(results)} files matched"
            if truncated:
                header += "  _(query truncated to 500 chars)_"
            output_lines = [header + "\n"]

            if mode == "index":
                # Progressive disclosure: compact index with DB stats per file.
                try:
                    file_stats = _get_db().get_file_stats()
                except Exception:
                    file_stats = {}
                for i, r in enumerate(results, 1):
                    score_pct = int(r["score"] * 100)
                    fname = r["filename"]
                    st = file_stats.get(fname, {})
                    fact_count = st.get("fact_count", "?")
                    top_grade = st.get("top_grade", "?")
                    est_tokens = fact_count * 15 if isinstance(fact_count, int) else "?"
                    top_fact = st.get("top_fact", "") or ""
                    preview = (top_fact[:80] + "...") if len(top_fact) > 80 else top_fact
                    output_lines.append(
                        f"{i}. **{r['title']}** (`{fname}`) - {score_pct}% match"
                        f" | {fact_count} facts | top grade {top_grade} | ~{est_tokens} tokens"
                    )
                    if preview:
                        output_lines.append(f"   Preview: {preview}")
                    output_lines.append(f"   Path: `{r['path']}`\n")
            else:
                # Existing full mode
                for i, r in enumerate(results, 1):
                    score_pct = int(r["score"] * 100)
                    output_lines.append(
                        f"{i}. **{r['title']}** (`{r['filename']}`) - {score_pct}% match"
                    )
                    output_lines.append(f"   {r['description']}")
                    output_lines.append(f"   Path: `{r['path']}`\n")

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": "\n".join(output_lines)}]},
            }

        elif tool_name == "kontext_reindex":
            entries.clear()
            entries.extend(index_memories(memory_dir, force=True))
            # Also embed individual entries into DB
            try:
                db = _get_db()
                model = get_model()
                all_db_entries = db.get_entries()
                embedded = 0
                for entry in all_db_entries:
                    if not entry.get("embedding"):
                        vec = model.encode(entry["fact"]).tolist()
                        db.store_embedding(entry["id"], vec)
                        embedded += 1
                _logger.info(f"REINDEX: {len(entries)} files, {embedded} entries embedded")
                return _mcp_result(req_id, f"Re-indexed {len(entries)} files. Embedded {embedded} entries in DB.")
            except Exception as e:
                _logger.error(f"REINDEX DB embedding failed: {e}", exc_info=True)
                return _mcp_result(
                    req_id,
                    f"Re-indexed {len(entries)} memory files. WARNING: DB embedding failed ({type(e).__name__}: {e}). Check _kontext.log."
                )
        # --- Database-backed tools ---

        elif tool_name == "kontext_write":
            try:
                db = _get_db()
                from export import export_file, export_memory_index

                file = args.get("file", "")
                fact = args.get("fact", "")
                if not file or not fact:
                    return _mcp_error(req_id, "file and fact are required")

                # Input validation Ã¢â‚¬” prevent OOM on embedding and DB bloat
                if len(fact) > 5000:
                    return _mcp_error(req_id, f"fact too long ({len(fact)} chars, max 5000)")
                if len(file) > 200:
                    return _mcp_error(req_id, f"file name too long ({len(file)} chars, max 200)")

                source = args.get("source", "")
                grade = min(10, max(1, args.get("grade", 5)))
                tier = args.get("tier", "active")
                if tier not in ("active", "historical", "cold"):
                    tier = "active"

                entry_id = db.add_entry(file=file, fact=fact, source=source, grade=grade, tier=tier)

                # Embed the new entry
                embed_failed = False
                try:
                    model = get_model()
                    vec = model.encode(fact).tolist()
                    db.store_embedding(entry_id, vec)
                except Exception as embed_err:
                    embed_failed = True
                    _logger.warning(f"EMBED FAILED for entry #{entry_id}: {type(embed_err).__name__}: {embed_err}")

                # Auto-export the affected file and update MEMORY.md.
                # FS errors are logged but do NOT roll back the DB Ã¢â‚¬” DB is source of truth;
                # a partial export is recoverable via kontext_reindex.
                export_failed = False
                try:
                    md_content = export_file(db, file)
                    (memory_dir / file).write_text(md_content, encoding="utf-8")
                    export_memory_index(db, memory_dir)
                except Exception as export_err:
                    export_failed = True
                    _logger.error(f"WRITE EXPORT FAILED for entry #{entry_id}: {type(export_err).__name__}: {export_err}")

                _logger.info(f"WRITE: {file} | {fact[:80]} | grade={grade} tier={tier}")
                msg = f"Wrote entry #{entry_id} to {file} (grade {grade}, {tier})."
                if embed_failed:
                    msg += " NOTE: embedding failed Ã¢â‚¬” entry won't appear in semantic search. Run kontext_reindex to retry."
                if export_failed:
                    msg += " WARNING: markdown export failed Ã¢â‚¬” DB is correct, re-run kontext_reindex to refresh files."
                if not (embed_failed or export_failed):
                    msg += " Exported to markdown."
                return _mcp_result(req_id, msg)
            except Exception as e:
                _logger.error(f"WRITE FAILED: {e}")
                return _mcp_error(req_id, f"kontext_write failed: {e}")

        elif tool_name == "kontext_query":
            try:
                db = _get_db()
                search_text = args.get("search", "")

                semantic = bool(args.get("semantic", True))
                top_k = max(1, min(int(args.get("top_k", 10) or 10), 100))
                fallback_note = ""

                if search_text and semantic:
                    # Run FTS5 and semantic in parallel, then reciprocal-rank fuse.
                    # FTS5 wins on exact terms, semantic wins on paraphrase.
                    # HyDE-style expansion bridges vocabulary gaps on the
                    # semantic leg; the FTS5 leg keeps the literal query so
                    # expansion tokens don't dilute term-match precision.
                    from retrieval import rrf_merge, expand
                    expanded = expand(search_text)
                    retriever_limit = max(top_k * 3, 20)
                    fts_results = db.search_entries(
                        expanded["literal"],
                        limit=retriever_limit,
                        file=args.get("file"),
                        tier=args.get("tier"),
                        min_grade=args.get("min_grade"),
                    )
                    sem_results: list = []
                    try:
                        model = get_model()
                        vec = model.encode(expanded["intent"])
                        if hasattr(vec, "tolist"):
                            vec = vec.tolist()
                        sem_results = db.semantic_search(
                            list(vec),
                            limit=retriever_limit,
                            min_grade=float(args.get("min_grade") or 0),
                            file=args.get("file"),
                        )
                        if args.get("tier"):
                            sem_results = [r for r in sem_results if r.get("tier") == args["tier"]]
                        qresults = rrf_merge(fts_results, sem_results)[:top_k]
                    except Exception as e:
                        _logger.warning(f"SEMANTIC FALLBACK: {type(e).__name__}: {e}")
                        fallback_note = " (semantic unavailable - using keyword search)"
                        qresults = fts_results[:top_k]
                elif search_text:
                    # Forward file/tier/min_grade filters to search_entries so they
                    # aren't silently dropped when 'search' is supplied.
                    qresults = db.search_entries(
                        search_text,
                        file=args.get("file"),
                        tier=args.get("tier"),
                        min_grade=args.get("min_grade"),
                    )
                else:
                    qresults = db.get_entries(
                        file=args.get("file"),
                        tier=args.get("tier"),
                        min_grade=args.get("min_grade"),
                    )

                # Bump access count for every returned entry (non-critical; swallow errors).
                try:
                    _db = _get_db()
                    for _e in qresults:
                        if _e.get("id"):
                            _db.bump_access_count(_e["id"])
                except Exception:
                    pass

                if not qresults:
                    return _mcp_result(req_id, "No entries found matching those filters.")

                lines = [f"**Kontext Query:** {len(qresults)} entries{fallback_note}\n"]
                for e in qresults:
                    lines.append(f"- [{e['file']}] (grade {e['grade']}, {e['tier']}) {e['fact']}")

                _logger.info(f"QUERY: {len(qresults)} results | file={args.get('file')} search={args.get('search','')[:40]}")
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_query failed: {e}")

        elif tool_name == "kontext_relate":
            try:
                db = _get_db()
                from graph import query_connections, describe_entity

                entity = args.get("entity", "")
                if not entity:
                    return _mcp_error(req_id, "entity is required")

                depth = max(1, min(int(args.get("depth", 2)), 5))
                connections = query_connections(db, entity, depth)
                description = describe_entity(db, entity)

                lines = [description, ""]
                if connections:
                    lines.append(f"Graph traversal ({len(connections)} relations, depth {depth}):")
                    seen = set()
                    for r in connections:
                        key = f"{r['entity_a']}-{r['relation']}-{r['entity_b']}"
                        if key not in seen:
                            seen.add(key)
                            lines.append(f"  {r['entity_a']} --{r['relation']}--> {r['entity_b']}")

                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_relate failed: {e}")

        elif tool_name == "kontext_recent":
            try:
                db = _get_db()
                hours = max(1, min(int(args.get("hours", 24)), 8760))  # cap at 1 year
                rresults = db.get_recent_changes(hours)

                if not rresults:
                    return _mcp_result(req_id, f"No entries changed in the last {hours} hours.")

                lines = [f"**Recent changes** (last {hours}h): {len(rresults)} entries\n"]
                for e in rresults:
                    lines.append(f"- [{e['file']}] {e['fact']} (updated {e['updated_at']})")

                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_recent failed: {e}")

        elif tool_name == "kontext_dream":
            try:
                db = _get_db()
                from dream import dream as run_dream

                dry_run = args.get("dry_run", False)
                phase = args.get("phase")
                results = run_dream(db, dry_run=dry_run, phase=phase)

                # Re-export if changes were made
                if not dry_run:
                    total = sum(
                        v for stats in results.values() for k, v in stats.items()
                        if k in ("merged", "anchored", "auto_resolved", "compressed", "purged") and v > 0
                    )
                    if total > 0:
                        from export import export_all, export_memory_index
                        # Use the memory_dir already resolved at handler entry Ã¢â‚¬”
                        # avoids a redundant find_memory_dir() disk scan.
                        export_all(db, memory_dir)
                        export_memory_index(db, memory_dir)

                mode = "DRY RUN" if dry_run else "APPLIED"
                lines = [f"**Dream consolidation ({mode}):**"]
                for phase_name, stats in results.items():
                    parts = [f"{k}={v}" for k, v in stats.items()]
                    lines.append(f"  {phase_name}: {', '.join(parts)}")
                _logger.info(f"DREAM: {mode} results={results}")
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_dream failed: {e}")

        elif tool_name == "kontext_digest":
            try:
                from digest import process_digests
                results = process_digests(
                    auto=args.get("auto", False),
                    dry_run=args.get("dry_run", False),
                    min_grade=args.get("min_grade", 8),
                )
                lines = [
                    "**Digest processing:**",
                    f"  Files processed: {results['files_processed']}",
                    f"  Candidates found: {results['candidates_found']}",
                    f"  Fresh (new): {results['candidates_fresh']}",
                    f"  Imported: {results['imported']}",
                ]
                if not args.get("auto") and not args.get("dry_run"):
                    lines.append(f"  Candidates written to _digest_candidates.md for review.")
                _logger.info(f"DIGEST: {results}")
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_digest failed: {e}")

        elif tool_name == "kontext_decay":
            try:
                db = _get_db()
                from export import export_all, export_memory_index

                days_threshold = max(1, min(int(args.get("days_threshold", 60)), 3650))
                decay_amount = max(0.1, min(float(args.get("decay_amount", 0.5)), 5.0))

                db.decay_scores(days_threshold=days_threshold, decay_amount=decay_amount)

                # Auto-export all files after decay (scores may have changed across files)
                export_all(db, memory_dir)
                export_memory_index(db, memory_dir)

                _logger.info(f"DECAY: threshold={days_threshold}d amount={decay_amount}")
                return _mcp_result(req_id, f"Decay applied (threshold: {days_threshold} days, amount: {decay_amount}). All files re-exported.")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_decay failed: {e}")

        elif tool_name == "kontext_session":
            try:
                db = _get_db()
                action = args.get("action", "")

                if action == "save":
                    workspace = args.get("workspace", "")
                    if not str(workspace).strip():
                        return _mcp_result(
                            req_id,
                            "Workspace is required for safe session save. Pass the current repo root or cwd.",
                        )
                    db.save_session(
                        workspace=workspace,
                        project=args.get("project", ""),
                        status=args.get("status", ""),
                        next_step=args.get("next_step", ""),
                        key_decisions=args.get("key_decisions", ""),
                        summary=args.get("summary", ""),
                        files_touched=args.get("files_touched", ""),
                    )
                    _logger.info(f"SESSION SAVE: project={args.get('project','')} status={args.get('status','')[:60]}")
                    return _mcp_result(req_id, "Session state saved.")

                elif action == "get":
                    workspace = args.get("workspace", "")
                    if not str(workspace).strip():
                        return _mcp_result(
                            req_id,
                            "Workspace is required for safe session restore. Pass the current repo root or cwd.",
                        )
                    session = db.get_latest_session(workspace=workspace)
                    if not session:
                        return _mcp_result(req_id, f"No saved sessions found for workspace: {workspace}")

                    lines = [
                        "**Latest session:**",
                        f"  Workspace: {session.get('workspace', '')}",
                        f"  Project: {session.get('project', '')}",
                        f"  Status: {session.get('status', '')}",
                        f"  Next step: {session.get('next_step', '')}",
                        f"  Key decisions: {session.get('key_decisions', '')}",
                    ]
                    if session.get('summary'):
                        lines.append(f"  Summary: {session['summary']}")
                    if session.get('files_touched'):
                        lines.append(f"  Files touched: {session['files_touched']}")
                    lines.append(f"  Saved at: {session.get('created_at', '')}")
                    return _mcp_result(req_id, "\n".join(lines))

                else:
                    return _mcp_error(req_id, "action must be save or get")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_session failed: {e}")


        elif tool_name == "kontext_cloud_status":
            try:
                from cloud.daemon import get_status

                status = get_status(_get_db())
                if not status.get("linked"):
                    return _mcp_result(req_id, "Cloud sync not linked.")

                lines = [
                    "**Cloud status:**",
                    f"  Server: {status.get('server_url', '')}",
                    f"  Workspace: {status.get('workspace_id', '')}",
                    f"  Device: {status.get('device_id', '')} ({status.get('device_class', '')})",
                    f"  Cursor: {status.get('cursor', '') or '(empty)'}",
                    f"  History ops: {status.get('history_count', 0)}",
                ]
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_cloud_status failed: {e}")

        elif tool_name == "kontext_cloud_link":
            try:
                from cloud.daemon import link_workspace

                label = args.get("label") or os.environ.get("COMPUTERNAME") or "Device"
                status = link_workspace(
                    _get_db(),
                    server_url=args.get("server_url", ""),
                    workspace_id=args.get("workspace_id", ""),
                    label=label,
                    device_class=args.get("device_class", "interactive"),
                    device_id=args.get("device_id"),
                    workspace_name=args.get("workspace_name"),
                    recovery_key_id=args.get("recovery_key_id"),
                )
                lines = [
                    "Cloud sync linked.",
                    f"Server: {status.get('server_url', '')}",
                    f"Workspace: {status.get('workspace_id', '')}",
                    f"Device: {status.get('device_id', '')}",
                ]
                return _mcp_result(req_id, "\n".join(lines))
            except ValueError as e:
                return _mcp_result(req_id, f"Cloud sync not linked: {e}")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_cloud_link failed: {e}")

        elif tool_name == "kontext_cloud_sync":
            try:
                from cloud.daemon import sync_once

                result = sync_once(_get_db(), limit=max(1, min(int(args.get("limit", 500) or 500), 5000)))
                lines = [
                    "Cloud sync complete.",
                    f"Pushed {result.get('pushed', 0)} history ops.",
                    f"Pulled {result.get('pulled', 0)} history ops.",
                    f"Cursor: {result.get('cursor', '') or '(empty)'}",
                ]
                return _mcp_result(req_id, "\n".join(lines))
            except ValueError:
                return _mcp_result(req_id, "Cloud sync not linked.")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_cloud_sync failed: {e}")

        elif tool_name == "kontext_cloud_recover":
            try:
                from cloud.daemon import recover_workspace

                result = recover_workspace(_get_db(), limit=max(1, min(int(args.get("limit", 500) or 500), 5000)))
                lines = [
                    "Cloud recovery complete.",
                    f"Recovered {result.get('recovered', 0)} history ops.",
                    f"Cursor: {result.get('cursor', '') or '(empty)'}",
                ]
                return _mcp_result(req_id, "\n".join(lines))
            except ValueError:
                return _mcp_result(req_id, "Cloud sync not linked.")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_cloud_recover failed: {e}")
        elif tool_name == "kontext_conflicts":
            try:
                db = _get_db()
                action = args.get("action", "")
                if action == "detect":
                    conflicts = db.detect_conflicts(file=args.get("file"))
                    if not conflicts:
                        return _mcp_result(req_id, "No conflicts detected.")
                    _logger.info(f"CONFLICTS: detected {len(conflicts)} in file={args.get('file','all')}")
                    clines = [f"**Detected {len(conflicts)} potential conflict(s):**\n"]
                    for c in conflicts:
                        clines.append(f"- [{c['file']}] \"{c['entry_a']}\" vs \"{c['entry_b']}\" (shared: {', '.join(c['shared_words'])})")
                    return _mcp_result(req_id, "\n".join(clines))
                elif action == "list":
                    pending = db.get_pending_conflicts()
                    if not pending:
                        return _mcp_result(req_id, "No pending conflicts.")
                    clines = [f"**{len(pending)} pending conflict(s):**\n"]
                    for c in pending:
                        clines.append(f"- #{c['id']} [{c['file']}] \"{c['entry_a']}\" vs \"{c['entry_b']}\" ({c['created_at']})")
                    return _mcp_result(req_id, "\n".join(clines))
                elif action == "resolve":
                    conflict_id = args.get("conflict_id")
                    resolution = args.get("resolution", "")
                    if not conflict_id:
                        return _mcp_error(req_id, "conflict_id is required for resolve")
                    db.resolve_conflict(conflict_id, resolution)
                    _logger.info(f"CONFLICT RESOLVED: id={conflict_id} resolution={resolution[:80]}")
                    return _mcp_result(req_id, f"Conflict #{conflict_id} resolved.")
                else:
                    return _mcp_error(req_id, "action must be detect, list, or resolve")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_conflicts failed: {e}")

        elif tool_name == "kontext_prompts":
            try:
                db = _get_db()
                query = args.get("search", "")
                hours = args.get("hours")
                limit = max(1, min(int(args.get("limit", 20) or 20), 100))

                if query:
                    results = db.search_prompts(query=query, limit=limit, hours=hours)
                elif hours is not None:
                    results = db.get_recent_prompts(hours=hours, limit=limit)
                else:
                    results = db.get_recent_prompts(hours=24, limit=limit)

                if not results:
                    return _mcp_result(req_id, "No prompts found.")

                lines = [f"**Past prompts:** {len(results)} found\n"]
                for p in results:
                    ts = str(p.get("created_at", ""))[:16]
                    content = p.get("content", "")
                    preview = (content[:100] + "...") if len(content) > 100 else content
                    lines.append(f"- `{ts}` {preview}")

                _logger.info(f"PROMPTS: {len(results)} results | search={query[:40]} hours={hours}")
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                _logger.error(f"kontext_prompts error: {e}", exc_info=True)
                return _mcp_error(req_id, f"kontext_prompts failed: {e}")


    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    """Run the MCP server on stdio."""
    memory_dir = find_memory_dir()
    if not memory_dir:
        print("ERROR: No memory directory found.", file=sys.stderr)
        sys.exit(1)

    print(f"Kontext MCP Server starting...", file=sys.stderr)
    print(f"Memory dir: {memory_dir}", file=sys.stderr)

    # Index on startup
    entries = index_memories(memory_dir)
    print(f"Indexed {len(entries)} memory files.", file=sys.stderr)
    print(f"Ready.", file=sys.stderr)
    _logger.info(f"MCP SERVER STARTED: {memory_dir}, {len(entries)} files indexed")

    # MCP stdio loop
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request, memory_dir, entries)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    if "--reindex" in sys.argv:
        memory_dir = find_memory_dir()
        if memory_dir:
            entries = index_memories(memory_dir, force=True)
            print(f"Re-indexed {len(entries)} files.")
        else:
            print("No memory directory found.")
    else:
        main()


