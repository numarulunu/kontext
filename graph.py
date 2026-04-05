# graph.py
"""
Knowledge Graph — extract entities and relations from memory entries.

Builds a graph of connected entities: people, tools, platforms, projects.
Enables queries like "what connects to Preply?" or "what depends on Stripe?"

Uses simple regex-based NER (no heavy ML) for speed. Can be upgraded
to spaCy or transformer NER later if needed.
"""

import re
from db import KontextDB

# Simple NER patterns for common entity types
_PROPER_NOUN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
_TOOL_PATTERN = re.compile(r"\b(Convertor|Transcriptor|AutoPipeline|UsageBOT|pocketDEV|Kontext|Claude HUD|Claude Codex)\b")
_PLATFORM_PATTERN = re.compile(r"\b(Preply|Stripe|GitHub|Skool|YouTube|Hetzner|Cloudflare|Revolut|Raiffeisen|ANAF|ElevenLabs|Freepik)\b")
_PERSON_PATTERN = re.compile(r"\b(Ionut|Luiza|Vazquez|Lordu|Waru)\b")

# Minimum entity length — single/two-char matches are always noise
_MIN_ENTITY_LENGTH = 3

# Comprehensive stopword set — words that appear capitalized at sentence start
# but are not named entities. Covers English sentence starters, common nouns,
# domain false positives, and Romanian noise words.
_STOPWORDS = {
    # Articles, pronouns, determiners
    "the", "this", "that", "these", "those", "its", "his", "her", "our", "your", "their",
    "some", "any", "all", "each", "every", "both", "few", "many", "much", "most",
    # Question words
    "who", "what", "when", "where", "which", "how", "why", "whom", "whose",
    # Conjunctions and transitions
    "and", "but", "or", "nor", "yet", "so", "for", "also", "too", "then",
    "however", "therefore", "meanwhile", "furthermore", "moreover", "nevertheless",
    "otherwise", "instead", "likewise", "accordingly", "consequently", "hence",
    "thus", "still", "besides", "although", "though", "unless", "since", "because",
    "while", "whereas", "whether", "after", "before", "during", "until", "once",
    # Common verbs (sentence starters)
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "may", "might",
    "can", "could", "must", "need", "dare", "let", "make", "made", "keep",
    "kept", "see", "saw", "seen", "get", "got", "set", "run", "ran", "add",
    "added", "put", "take", "took", "taken", "come", "came", "give", "gave",
    "try", "tried", "use", "used", "uses", "using", "know", "knew", "think",
    "say", "said", "tell", "told", "ask", "asked", "want", "wanted", "move",
    "moved", "start", "started", "stop", "stopped", "check", "checked",
    "built", "created", "moved", "applied", "removed", "fixed", "updated",
    # Common nouns that appear capitalized at sentence start
    "air", "anti", "fix", "phase", "module", "modules", "authority", "system",
    "systems", "pattern", "patterns", "section", "sections", "entry", "entries",
    "tool", "tools", "project", "projects", "step", "steps", "rule", "rules",
    "note", "notes", "update", "updates", "change", "changes", "new", "old",
    "file", "files", "line", "lines", "code", "data", "list", "item", "items",
    "value", "values", "key", "keys", "part", "parts", "way", "ways", "end",
    "time", "times", "day", "days", "week", "month", "year", "date", "number",
    "test", "tests", "error", "errors", "issue", "issues", "bug", "bugs",
    "feature", "features", "option", "options", "mode", "field", "fields",
    "page", "pages", "link", "links", "path", "paths", "port", "log", "logs",
    "fact", "facts", "source", "sources", "tier", "tiers", "grade", "grades",
    "active", "historical", "status", "version", "type", "name", "names",
    "location", "bank", "result", "results", "output", "input", "config",
    "default", "defaults", "setting", "settings", "format", "table", "tables",
    "query", "queries", "model", "models", "class", "method", "function",
    "script", "scripts", "command", "commands", "server", "client", "app",
    "build", "release", "deploy", "install", "package", "import", "export",
    "window", "view", "state", "event", "events", "action", "actions",
    "user", "users", "account", "session", "token", "request", "response",
    "message", "messages", "text", "string", "content", "context", "scope",
    "index", "count", "size", "total", "max", "min", "current", "next",
    "last", "first", "second", "third", "main", "base", "core", "top",
    "bottom", "left", "right", "back", "front", "full", "empty", "open",
    "close", "true", "false", "null", "none", "other", "same", "different",
    "only", "just", "even", "already", "always", "never", "often", "sometimes",
    "here", "there", "now", "about", "above", "below", "between", "through",
    "with", "without", "into", "from", "over", "under", "down", "not", "very",
    "more", "less", "well", "just", "really", "quite", "rather", "almost",
    "enough", "too", "also", "either", "neither", "per", "via", "like",
    # Domain false positives from actual Kontext data
    "apex", "prompt", "architect", "arezzo", "automation", "net", "memory",
    "digest", "intake", "snapshot", "backlog", "changelog", "pipeline",
    "hook", "hooks", "sync", "batch", "queue", "cache", "thread", "threads",
    "agent", "agents", "task", "tasks", "skill", "skills", "plan", "plans",
    "spec", "specs", "design", "audit", "review", "debug", "refactor",
    # Romanian noise words
    "din", "care", "sunt", "este", "pentru", "sau", "dar", "mai", "lui",
    "cum", "cel", "cea", "ale", "ori", "fie", "nici", "deja", "doar",
    "prin", "spre", "sub", "ala", "asta", "aia", "aici", "acum", "apoi",
    "daca", "cand", "unde", "cine", "cat", "tot", "alta", "alte",
}

# Relation extraction patterns
_RELATION_PATTERNS = [
    (re.compile(r"(\w+)\s+(?:uses?|using)\s+(\w+)", re.I), "uses"),
    (re.compile(r"(\w+)\s+(?:migrat\w+)\s+(?:from\s+)?(\w+)\s+to\s+(\w+)", re.I), "migrating"),
    (re.compile(r"(\w+)\s+(?:built|created|published)\s+(?:on\s+)?(\w+)", re.I), "built_on"),
    (re.compile(r"(\w+)\s+(?:teaches?|teaching)\s+(?:at\s+|on\s+)?(\w+)", re.I), "teaches_at"),
    (re.compile(r"(\w+)\s+(?:pays?|payment)\s+(?:via|through)\s+(\w+)", re.I), "pays_via"),
]

def extract_entities(text: str) -> list[str]:
    """Extract named entities from text using pattern matching."""
    entities = set()

    # Known entities (tools, platforms, people) always pass — no filtering
    for pattern in [_TOOL_PATTERN, _PLATFORM_PATTERN, _PERSON_PATTERN]:
        for match in pattern.finditer(text):
            entities.add(match.group(1))

    # Also grab capitalized proper nouns not already matched
    for match in _PROPER_NOUN.finditer(text):
        word = match.group(1)
        # Skip if too short, in stopwords, or ALL-CAPS abbreviation
        if len(word) < _MIN_ENTITY_LENGTH:
            continue
        if word.lower() in _STOPWORDS:
            continue
        if word.isupper():
            continue
        entities.add(word)

    return sorted(entities)


def build_graph(db: KontextDB) -> int:
    """Scan all entries, extract entities, build relations. Returns count of relations added."""
    entries = db.get_entries()
    count = 0

    for entry in entries:
        entities = extract_entities(entry["fact"])

        # Create relations between co-occurring entities in the same fact
        for i, e1 in enumerate(entities):
            for e2 in entities[i + 1:]:
                if e1 != e2:
                    rel_id = db.add_relation(
                        entity_a=e1,
                        relation="co_occurs_with",
                        entity_b=e2,
                        confidence=0.5,
                        source=entry["source"],
                    )
                    if rel_id:
                        count += 1

        # Try explicit relation extraction
        for pattern, rel_type in _RELATION_PATTERNS:
            for match in pattern.finditer(entry["fact"]):
                groups = match.groups()
                if len(groups) >= 2:
                    db.add_relation(
                        entity_a=groups[0],
                        relation=rel_type,
                        entity_b=groups[-1],
                        confidence=0.8,
                        source=entry["source"],
                    )
                    count += 1

    return count

def prune_graph(db: KontextDB) -> int:
    """Remove relations where either entity is a stopword or too short. Returns count removed."""
    relations = db.get_all_relations()
    removed = 0

    for rel in relations:
        a = rel["entity_a"]
        b = rel["entity_b"]
        if (len(a) < _MIN_ENTITY_LENGTH or a.lower() in _STOPWORDS or
                len(b) < _MIN_ENTITY_LENGTH or b.lower() in _STOPWORDS):
            db.delete_relation(rel["id"])
            removed += 1

    return removed


def rebuild_graph(db: KontextDB) -> int:
    """Clear all relations and rebuild the graph with current quality filters. Returns new count."""
    db.execute("DELETE FROM relations")
    db.conn.commit()
    return build_graph(db)


def query_connections(db: KontextDB, entity: str, depth: int = 2) -> list[dict]:
    """Find everything connected to an entity up to N hops."""
    return db.query_graph(entity, depth)


def describe_entity(db: KontextDB, entity: str) -> str:
    """Human-readable description of an entity and its connections."""
    relations = db.get_relations(entity)
    if not relations:
        return f"No known connections for '{entity}'."

    lines = [f"**{entity}** — {len(relations)} connection(s):\n"]
    for r in relations:
        other = r["entity_b"] if r["entity_a"] == entity else r["entity_a"]
        conf = int(r["confidence"] * 100)
        lines.append(f"  {r['relation']} -> **{other}** ({conf}%)")

    return "\n".join(lines)

