"""
Kontext MCP Server — Semantic memory retrieval for Claude Code.

Runs locally. No cloud. No API calls. Embeds memory file descriptions
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
import os
import sys
import re
from pathlib import Path
from datetime import datetime

# Set up file logging — every write, every failure, every tool call
_LOG_FILE = Path(__file__).parent / "_kontext.log"
_logger = logging.getLogger("kontext")
_logger.setLevel(logging.INFO)
_file_handler = logging.FileHandler(str(_LOG_FILE), encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(_file_handler)

# Lazy imports — only load heavy libs when needed
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
        # Match: - [Title](filename.md) — description
        match = re.match(r"^-\s+\[(.+?)\]\((.+?\.md)\)\s*[—–-]\s*(.+?)(?:\*\*Updated.*)?$", line.strip())
        if match:
            title = match.group(1)
            filename = match.group(2)
            description = match.group(3).strip()
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

    model = get_model()
    import numpy as np

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
        "description": "Search memory files by meaning. Returns the most relevant files for any query. Use this BEFORE reading memory files to know which ones to load.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you are looking for -- a topic, question, or the user message",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5,
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
            },
        },
    },
    {
        "name": "kontext_relate",
        "description": "Query the knowledge graph. Find everything connected to an entity (person, tool, platform) up to N hops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name to look up (e.g. Stripe, Luiza, Convertor)"},
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
                "project": {"type": "string", "description": "Project name (for save)"},
                "status": {"type": "string", "description": "Current status summary (for save)"},
                "next_step": {"type": "string", "description": "What to do next (for save)"},
                "key_decisions": {"type": "string", "description": "Important decisions made this session (for save)"},
                "summary": {"type": "string", "description": "2-3 sentence conversation summary — what was discussed, tone, direction (for save)"},
                "files_touched": {"type": "string", "description": "Comma-separated list of files edited or discussed this session (for save)"},
            },
            "required": ["action"],
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
                "serverInfo": {"name": "kontext-memory", "version": "5.0.0"},
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
            top_k = args.get("top_k", 5)

            if not query:
                return _mcp_error(req_id, "query is required")

            results = search(query, entries, top_k)
            output_lines = [f"**Kontext Search:** {len(results)} files matched\n"]
            for i, r in enumerate(results, 1):
                score_pct = int(r["score"] * 100)
                output_lines.append(f"{i}. **{r['title']}** (`{r['filename']}`) — {score_pct}% match")
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

                source = args.get("source", "")
                grade = args.get("grade", 5)
                tier = args.get("tier", "active")

                entry_id = db.add_entry(file=file, fact=fact, source=source, grade=grade, tier=tier)

                # Embed the new entry
                try:
                    model = get_model()
                    vec = model.encode(fact).tolist()
                    db.store_embedding(entry_id, vec)
                except Exception:
                    pass  # Embedding is optional

                # Auto-export the affected file and update MEMORY.md
                md_content = export_file(db, file)
                (memory_dir / file).write_text(md_content, encoding="utf-8")
                export_memory_index(db, memory_dir)

                _logger.info(f"WRITE: {file} | {fact[:80]} | grade={grade} tier={tier}")
                return _mcp_result(req_id, f"Wrote entry #{entry_id} to {file} (grade {grade}, {tier}). Exported to markdown.")
            except Exception as e:
                _logger.error(f"WRITE FAILED: {e}")
                return _mcp_error(req_id, f"kontext_write failed: {e}")

        elif tool_name == "kontext_query":
            try:
                db = _get_db()
                search_text = args.get("search", "")

                if search_text:
                    qresults = db.search_entries(search_text)
                else:
                    qresults = db.get_entries(
                        file=args.get("file"),
                        tier=args.get("tier"),
                        min_grade=args.get("min_grade"),
                    )

                if not qresults:
                    return _mcp_result(req_id, "No entries found matching those filters.")

                lines = [f"**Kontext Query:** {len(qresults)} entries\n"]
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

                depth = args.get("depth", 2)
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
                hours = args.get("hours", 24)
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
                        mem_dir = find_memory_dir()
                        if mem_dir:
                            export_all(db, mem_dir)
                            export_memory_index(db, mem_dir)

                mode = "DRY RUN" if dry_run else "APPLIED"
                lines = [f"**Dream consolidation ({mode}):**"]
                for phase_name, stats in results.items():
                    parts = [f"{k}={v}" for k, v in stats.items()]
                    lines.append(f"  {phase_name}: {', '.join(parts)}")
                _logger.info(f"DREAM: {mode} results={results}")
                return _mcp_result(req_id, "\n".join(lines))
            except Exception as e:
                return _mcp_error(req_id, f"kontext_dream failed: {e}")

        elif tool_name == "kontext_decay":
            try:
                db = _get_db()
                from export import export_all, export_memory_index

                days_threshold = args.get("days_threshold", 60)
                decay_amount = args.get("decay_amount", 0.5)

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
                    db.save_session(
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
                    session = db.get_latest_session()
                    if not session:
                        return _mcp_result(req_id, "No saved sessions found.")

                    lines = [
                        "**Latest session:**",
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
                    return _mcp_result(req_id, f"Conflict #{conflict_id} resolved.")
                else:
                    return _mcp_error(req_id, "action must be detect, list, or resolve")
            except Exception as e:
                return _mcp_error(req_id, f"kontext_conflicts failed: {e}")


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