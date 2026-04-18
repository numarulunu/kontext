#!/usr/bin/env python3
"""Audit kontext routing keyword coverage against memory file descriptions.

For each memory file:
  1. Read frontmatter `description:` field.
  2. Tokenize into candidate keywords (>=4 chars, drop stopwords).
  3. Check which routing rule maps to that file.
  4. Report candidates NOT already covered by that rule's keywords/anti_keywords.

Output: a proposed YAML diff for review. Manual approve only — never auto-edits.

Usage:
    python kontext_audit_keywords.py            # report missing coverage
    python kontext_audit_keywords.py --verbose  # show all candidates
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.claude"))
import kontext_route  # noqa: E402

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "your",
    "what", "when", "where", "which", "their", "they", "them", "have",
    "been", "will", "would", "could", "should", "about", "user", "memory",
    "file", "context", "claude", "kontext", "info", "information", "type",
    "name", "description", "things", "stuff", "general", "various",
    "specific", "related", "covers", "includes", "tracks", "logs", "notes",
}


def parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter as dict. Returns {} if missing/malformed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm = parts[1]
    out: dict = {}
    for line in fm.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def candidate_keywords(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-ZăâîșțéíáúüöÄ]{4,}", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = kontext_route.load_config()
    if not cfg:
        print("ERROR: routing config not loaded")
        return 1

    memory_root = kontext_route._resolve_memory_root(cfg)
    files_to_rules: dict[str, list[int]] = {}
    for i, rule in enumerate(cfg.get("topic_routes", []) or []):
        for f in rule.get("files", []) or []:
            files_to_rules.setdefault(f, []).append(i)

    proposals: list[tuple[str, list[int], set[str]]] = []

    for path in sorted(memory_root.glob("*.md")):
        if path.name in {"MEMORY.md", "_sync_test.md"}:
            continue
        fm = parse_frontmatter(path)
        desc = fm.get("description", "")
        if not desc:
            continue
        candidates = candidate_keywords(desc)
        rule_idxs = files_to_rules.get(path.name, [])
        if not rule_idxs:
            print(f"\n=== {path.name} (NOT routed by any topic rule) ===")
            print(f"  desc: {desc[:100]}")
            print(f"  candidates: {sorted(candidates)[:15]}")
            continue
        existing: set[str] = set()
        for idx in rule_idxs:
            rule = cfg["topic_routes"][idx]
            for kw in rule.get("keywords", []) or []:
                existing.add(kw.lower().strip())
        new = candidates - existing - {kw.split()[0] for kw in existing if " " in kw}
        if new:
            proposals.append((path.name, rule_idxs, new))

    if not proposals:
        print("=== All routed files have keyword coverage ===")
        return 0

    for fname, idxs, missing in proposals:
        print(f"\n=== {fname} (rules {idxs}) ===")
        print(f"  Candidate additions: {sorted(missing)[:12]}")
        if args.verbose:
            print(f"  All candidates: {sorted(missing)}")

    print("\nReview above. Manually add the relevant ones to kontext_routing.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
