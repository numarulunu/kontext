"""Deterministic query expansion for Kontext retrieval (HyDE-style, no LLM).

Bridges vocabulary gaps so the semantic leg can reach the right file even
when the user's phrasing doesn't share tokens with the target entries.
Example: "I'm stuck" has no direct match against blind_spots facts, but
expanding the semantic query with "procrastination avoidance blind spots"
pulls the right cluster into the top-k.

The FTS5 leg receives the literal query (OR-tokenization already handles
term recall). The semantic leg receives the expanded intent text.
"""
from __future__ import annotations


# keyword -> {domain, add_tokens}
# Matching is case-insensitive substring. Keep keywords distinctive so
# short words like "plan" don't trigger on "planned" across domains.
EXPANSION_MAP: dict[str, dict] = {
    # Behavioral -- the "stuck" cluster
    "stuck": {"domain": "behavioral", "add_tokens": ["procrastination", "avoidance", "blind spots"]},
    "procrastinat": {"domain": "behavioral", "add_tokens": ["avoidance", "blind spots", "patterns"]},
    "avoiding": {"domain": "behavioral", "add_tokens": ["procrastination", "blind spots", "patterns"]},
    "tweaking": {"domain": "behavioral", "add_tokens": ["polishing", "avoidance", "blind spots"]},
    "keep opening": {"domain": "behavioral", "add_tokens": ["abandonment", "patterns", "blind spots"]},
    "can't ship": {"domain": "behavioral", "add_tokens": ["procrastination", "blind spots"]},
    "can't just": {"domain": "behavioral", "add_tokens": ["blind spots", "patterns"]},
    "why can't": {"domain": "behavioral", "add_tokens": ["blind spots", "patterns"]},

    # Emotional -- identity and psychology
    "drowning": {"domain": "emotional", "add_tokens": ["overwhelm", "psychology", "identity"]},
    "hate myself": {"domain": "emotional", "add_tokens": ["psychology", "identity", "childhood"]},
    "meaningless": {"domain": "emotional", "add_tokens": ["psychology", "identity", "purpose"]},
    "don't feel like": {"domain": "emotional", "add_tokens": ["psychology", "identity", "blind spots"]},
    "wasted": {"domain": "emotional", "add_tokens": ["psychology", "identity", "regret"]},
    "everything feels": {"domain": "emotional", "add_tokens": ["psychology", "identity"]},

    # Relationship / family
    "luiza": {"domain": "relationship", "add_tokens": ["Luiza", "relationship", "wedding"]},
    "fight": {"domain": "relationship", "add_tokens": ["Luiza", "conflict"]},
    "mother": {"domain": "family", "add_tokens": ["no-contact", "family"]},
    "suicide threat": {"domain": "family", "add_tokens": ["mother", "no-contact", "boundaries"]},

    # Financial
    "pfa": {"domain": "financial", "add_tokens": ["ANAF", "taxes", "invoice"]},
    "anaf": {"domain": "financial", "add_tokens": ["PFA", "taxes", "invoice"]},
    "srl": {"domain": "financial", "add_tokens": ["PFA", "ANAF", "legal entity"]},
    "tax": {"domain": "financial", "add_tokens": ["PFA", "ANAF", "invoice"]},
    "deduct": {"domain": "financial", "add_tokens": ["PFA", "ANAF", "expense"]},
    "d212": {"domain": "financial", "add_tokens": ["PFA", "ANAF", "deadline"]},
    "stripe": {"domain": "financial", "add_tokens": ["payment", "invoice"]},
    "invoice": {"domain": "financial", "add_tokens": ["PFA", "ANAF", "payment"]},

    # Vocality / content / strategy
    "skool": {"domain": "strategic", "add_tokens": ["Vocality", "community", "curriculum", "pricing"]},
    "youtube": {"domain": "strategic", "add_tokens": ["content", "Vocality", "POV Guy", "video"]},
    "vsl": {"domain": "strategic", "add_tokens": ["script", "video", "marketing"]},
    "launch": {"domain": "strategic", "add_tokens": ["Vocality", "project goals", "blind spots"]},
    "pricing": {"domain": "strategic", "add_tokens": ["Vocality", "Skool", "course"]},
    "course": {"domain": "strategic", "add_tokens": ["Vocality", "curriculum"]},
    "pilot": {"domain": "strategic", "add_tokens": ["Vocality", "content", "video"]},
    "dashboard": {"domain": "strategic", "add_tokens": ["project", "blind spots", "avoidance"]},
    "plan my": {"domain": "strategic", "add_tokens": ["project goals", "priorities"]},
    "quarter": {"domain": "strategic", "add_tokens": ["project goals", "planning"]},

    # Vocal / teaching
    "singing": {"domain": "vocal", "add_tokens": ["voice", "teaching", "Melocchi"]},
    "vocal": {"domain": "vocal", "add_tokens": ["voice", "teaching", "Melocchi"]},
    "teaching": {"domain": "vocal", "add_tokens": ["students", "voice", "lesson"]},
    "lesson": {"domain": "vocal", "add_tokens": ["teaching", "students", "voice"]},
    "student": {"domain": "vocal", "add_tokens": ["teaching", "voice", "Preply"]},
    "melocchi": {"domain": "vocal", "add_tokens": ["voice", "teaching", "method"]},
    "vazquez": {"domain": "vocal", "add_tokens": ["Rafael", "voice", "teaching"]},

    # Brand / copy
    "medical noir": {"domain": "brand", "add_tokens": ["Vocality", "brand", "aesthetic"]},
    "vocality": {"domain": "brand", "add_tokens": ["brand", "Medical Noir"]},
    "copy": {"domain": "brand", "add_tokens": ["Void Strategy", "brand", "influences"]},
    "marketing": {"domain": "brand", "add_tokens": ["brand", "copy"]},

    # People
    "palazu": {"domain": "social", "add_tokens": ["friend", "peer"]},

    # Health
    "skincare": {"domain": "health", "add_tokens": ["eczema", "routine"]},
    "eczema": {"domain": "health", "add_tokens": ["skin", "routine"]},
    "sleep": {"domain": "health", "add_tokens": ["recovery", "routine"]},
}


# Project-name routing. Moved here from digest.py so expansion keywords
# and project routing live in one place.
PROJECT_TO_FILE: dict[str, str] = {
    "finance": "user_financial_architecture.md",
    "pfa": "user_financial_architecture.md",
    "contabilitate": "user_financial_architecture.md",
    "skool": "project_content.md",
    "youtube": "project_content.md",
    "personal context": "user_identity.md",
    "kontext": "design_principles.md",
    "tool auditor": "tool_registry.md",
}


# Cap prevents a query hitting multiple keywords from ballooning into a
# vector drifted away from the user's actual intent. 3 tokens keeps the
# expansion tight — empirically, 6 over-pulled cross-domain queries toward
# single-domain file clusters.
_MAX_TOKENS = 3


def expand(query: str) -> dict:
    """Expand a user query with domain tokens.

    Returns dict with:
      literal: the original query, for the FTS5 leg (preserves precision).
      intent:  query + " " + joined add_tokens, for the semantic leg.
      domain:  first-matched keyword's domain, or "" if no match.
    """
    q = query.strip()
    q_lower = q.lower()
    matched_tokens: list[str] = []
    seen: set[str] = set()
    domain = ""

    for keyword, spec in EXPANSION_MAP.items():
        if keyword in q_lower:
            if not domain:
                domain = spec["domain"]
            for tok in spec["add_tokens"]:
                if tok.lower() not in seen:
                    seen.add(tok.lower())
                    matched_tokens.append(tok)

    if not matched_tokens:
        return {"literal": q, "intent": q, "domain": ""}

    matched_tokens = matched_tokens[:_MAX_TOKENS]
    return {"literal": q, "intent": q + " " + " ".join(matched_tokens), "domain": domain}
