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

    for pattern in [_TOOL_PATTERN, _PLATFORM_PATTERN, _PERSON_PATTERN]:
        for match in pattern.finditer(text):
            entities.add(match.group(1))

    # Also grab capitalized proper nouns not already matched
    for match in _PROPER_NOUN.finditer(text):
        word = match.group(1)
        if word.lower() not in {"the", "this", "that", "when", "what", "where", "how", "grade",
                                 "active", "historical", "status", "version", "type", "name",
                                 "location", "bank", "uses", "built", "created"}:
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
