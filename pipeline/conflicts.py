"""
Conflict Detection & Resolution Pattern Learning

Detects contradictions between new extractions and existing memory.
Logs conflicts to _conflicts.md for manual resolution.
Learns resolution patterns over time to auto-resolve low-confidence conflicts.

Usage (called by Claude during intake processing, not directly):
    from conflicts import ConflictDetector
    detector = ConflictDetector(memory_dir, kontext_dir)
    detector.check(new_entry, target_file)
    detector.resolve(conflict_id, decision, reasoning)
"""

import json
import re
from datetime import datetime
from pathlib import Path


class ConflictDetector:
    """Detects and manages memory conflicts with pattern learning."""

    def __init__(self, memory_dir: str | Path, kontext_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.kontext_dir = Path(kontext_dir)
        self.conflicts_file = self.memory_dir / "_conflicts.md"
        self.patterns_file = self.memory_dir / "feedback_conflict_patterns.md"

    def load_patterns(self) -> list[dict]:
        """Load learned resolution patterns."""
        if not self.patterns_file.exists():
            return []

        patterns = []
        content = self.patterns_file.read_text(encoding="utf-8")

        # Parse pattern blocks
        blocks = re.split(r"\n### Pattern \d+", content)
        for block in blocks[1:]:  # skip header
            pattern = {}
            for line in block.strip().split("\n"):
                line = line.strip()
                if line.startswith("**Category:**"):
                    pattern["category"] = line.split(":**")[1].strip()
                elif line.startswith("**Rule:**"):
                    pattern["rule"] = line.split(":**")[1].strip()
                elif line.startswith("**Confidence:**"):
                    try:
                        pattern["confidence"] = int(line.split(":**")[1].strip().rstrip("%"))
                    except ValueError:
                        pattern["confidence"] = 50
                elif line.startswith("**Examples:**"):
                    # BUG FIX: re.search can return None if no digits found
                    match = re.search(r"\d+", line.split(":**")[1])
                    pattern["examples"] = int(match.group()) if match else 0
            if pattern.get("category") and pattern.get("rule"):
                patterns.append(pattern)

        return patterns

    def can_auto_resolve(self, conflict_category: str) -> dict | None:
        """Check if a conflict can be auto-resolved based on learned patterns.
        Returns the pattern if confidence >= 80% and examples >= 3, else None."""
        patterns = self.load_patterns()
        for p in patterns:
            if p.get("category", "").lower() == conflict_category.lower():
                if p.get("confidence", 0) >= 80 and p.get("examples", 0) >= 3:
                    return p
        return None

    def log_conflict(self, conflict: dict):
        """Append a conflict to _conflicts.md for manual resolution."""
        self.conflicts_file.parent.mkdir(parents=True, exist_ok=True)

        entry = f"""
### Conflict — {datetime.now().strftime('%Y-%m-%d %H:%M')}
- **Source:** {conflict.get('source', 'unknown')}
- **Target file:** {conflict.get('target_file', 'unknown')}
- **Category:** {conflict.get('category', 'uncategorized')}
- **Existing:** {conflict.get('existing', 'N/A')}
- **New:** {conflict.get('new', 'N/A')}
- **Status:** PENDING
"""

        if self.conflicts_file.exists():
            content = self.conflicts_file.read_text(encoding="utf-8")
            content += entry
        else:
            content = f"""---
name: Memory Conflicts
description: Contradictions detected between sources. Resolve manually or let patterns auto-resolve.
type: reference
---

# Pending Conflicts
{entry}
# Resolved
"""
        self.conflicts_file.write_text(content, encoding="utf-8")

    def log_resolution(self, category: str, decision: str, reasoning: str):
        """Log a conflict resolution to build the pattern library.
        Called by Claude after user makes a decision."""

        if not self.patterns_file.exists():
            content = """---
name: Conflict Resolution Patterns
description: Learned patterns from manual conflict resolution. Used to auto-resolve future conflicts when confidence is high enough.
type: feedback
---

# Decision-Making Patterns

These patterns were learned from how you resolved memory conflicts.
After 3+ examples with consistent decisions, Claude can auto-resolve similar conflicts.

"""
        else:
            content = self.patterns_file.read_text(encoding="utf-8")

        # Count existing patterns to number the new one
        pattern_count = len(re.findall(r"### Pattern \d+", content))

        entry = f"""
### Pattern {pattern_count + 1}
- **Category:** {category}
- **Rule:** {decision}
- **Reasoning:** {reasoning}
- **Confidence:** 50%
- **Examples:** 1
- **Last updated:** {datetime.now().strftime('%Y-%m-%d')}
"""

        # Check if this category already has a pattern — if so, update it
        existing_pattern = re.search(
            rf"### Pattern \d+\n.*?\*\*Category:\*\* {re.escape(category)}.*?(?=### Pattern|\Z)",
            content,
            re.DOTALL,
        )

        if existing_pattern:
            old_block = existing_pattern.group()
            # Increment examples count
            examples_match = re.search(r"\*\*Examples:\*\* (\d+)", old_block)
            old_count = int(examples_match.group(1)) if examples_match else 0
            new_count = old_count + 1

            # Increase confidence (caps at 95%)
            conf_match = re.search(r"\*\*Confidence:\*\* (\d+)", old_block)
            old_conf = int(conf_match.group(1)) if conf_match else 50
            new_conf = min(95, old_conf + 10)

            # Update the block
            new_block = re.sub(r"\*\*Examples:\*\* \d+", f"**Examples:** {new_count}", old_block)
            new_block = re.sub(r"\*\*Confidence:\*\* \d+%", f"**Confidence:** {new_conf}%", new_block)
            new_block = re.sub(
                r"\*\*Last updated:\*\* .+",
                f"**Last updated:** {datetime.now().strftime('%Y-%m-%d')}",
                new_block,
            )
            # Update rule if different (most recent decision wins)
            new_block = re.sub(r"\*\*Rule:\*\* .+", f"**Rule:** {decision}", new_block)

            content = content.replace(old_block, new_block)
        else:
            # Append new pattern before any trailing content
            content = content.rstrip() + "\n" + entry

        self.patterns_file.write_text(content, encoding="utf-8")
