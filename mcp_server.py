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
import os
import sys
import re
from pathlib import Path
from datetime import datetime

# Lazy imports — only load heavy libs when needed
_model = None
_embeddings_cache = {}
_cache_file = None


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
                "serverInfo": {"name": "kontext-memory", "version": "1.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    elif method == "notifications/initialized":
        return None  # No response needed

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "kontext_search",
                        "description": "Search memory files by meaning. Returns the most relevant files for any query. Use this BEFORE reading memory files to know which ones to load.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "What you're looking for — a topic, question, or the user's message",
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
                ]
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        if tool_name == "kontext_search":
            query = args.get("query", "")
            top_k = args.get("top_k", 5)

            if not query:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": "Error: query is required"}]},
                }

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
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Re-indexed {len(entries)} memory files."}]},
            }

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
