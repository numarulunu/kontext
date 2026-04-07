"""
grading.py — Heuristic entry grading for Kontext intake.

Scores extracted nuggets 1-10 based on keyword patterns.
This is a PRE-FILTER that runs before Claude sees the data.
No AI involved — pure keyword and pattern matching.

Score ranges:
    8-10: Explicitly stated, actionable, changes AI behavior
    5-7:  Useful context, not critical
    1-4:  Noise — debugging, pleasantries, abandoned topics

Python 3.10+, stdlib only.
"""

import re


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Decision language — strong signals of actionable choices
_DECISION_PATTERNS = [
    r"\bi decided\b",
    r"\bi'm going with\b",
    r"\bgoing with\b",
    r"\bi chose\b",
    r"\bwill do\b",
    r"\bfinal decision\b",
    r"\bsettled on\b",
    r"\bcommitting to\b",
    r"\bfrom now on\b",
    r"\bthe plan is\b",
    r"\bthe move is\b",
    r"\bshipping\b",
    r"\blaunching\b",
]

# Identity markers — who the user is
_IDENTITY_PATTERNS = [
    r"\bi am a?\b",
    r"\bmy name is\b",
    r"\bi work as\b",
    r"\bi work at\b",
    r"\bi live in\b",
    r"\bmy profession\b",
    r"\bmy background\b",
    r"\bi'm a\b",
    r"\bi teach\b",
    r"\bmy brand\b",
    r"\bmy business\b",
    r"\bmy company\b",
]

# Preference markers — what the user wants/doesn't want
_PREFERENCE_PATTERNS = [
    r"\bi prefer\b",
    r"\bi hate\b",
    r"\bi love\b",
    r"\balways do\b",
    r"\bnever do\b",
    r"\bi want\b",
    r"\bi don't want\b",
    r"\bi need\b",
    r"\bi don't like\b",
    r"\bstop doing\b",
    r"\bdon't ever\b",
    r"\bmake sure\b.*\balways\b",
]

# Financial markers — money, costs, pricing
_FINANCIAL_PATTERNS = [
    r"\b\d+\s*(?:EUR|RON|USD|\$|€|lei)\b",
    r"\b(?:EUR|RON|USD|\$|€)\s*\d+\b",
    r"\bcost\b",
    r"\bprice\b",
    r"\bincome\b",
    r"\brevenue\b",
    r"\bsalary\b",
    r"\brate\b.*\b(?:per|/)\s*(?:hour|session|month)\b",
    r"\binvoice\b",
    r"\btax\b",
    r"\bstripe\b",
    r"\bPFA\b",
    r"\bANAF\b",
]

# Project status markers — clear outcomes
_PROJECT_PATTERNS = [
    r"\blaunched\b",
    r"\bshipped\b",
    r"\bcompleted\b",
    r"\bfinished\b",
    r"\bstalled\b",
    r"\bkilled\b",
    r"\bpivoted\b",
    r"\bv\d+\.\d+\b",
    r"\bversion\b",
    r"\bmilestone\b",
    r"\bdeadline\b",
]

# AI interaction feedback — how Claude should behave
_AI_FEEDBACK_PATTERNS = [
    r"\bdon't\b.*\bask\b",
    r"\bstop\b.*\bdoing\b",
    r"\bwhen i say\b",
    r"\bi mean\b",
    r"\bthat's not what\b",
    r"\bbetter if you\b",
    r"\bnext time\b",
    r"\bremember that\b",
    r"\bkeep in mind\b",
    r"\bfeedback\b.*\bclaude\b",
    r"\bclaude\b.*\bfeedback\b",
]

# Emotional depth — long personal messages (scored by length + pronouns)
_PERSONAL_PRONOUNS = re.compile(
    r"\b(?:I|my|me|myself|I'm|I've|I'd|I'll)\b", re.IGNORECASE
)

# Noise markers — low-value content
_NOISE_PATTERNS = [
    r"^(?:thanks|thank you|ok|okay|got it|cool|nice|perfect|great|yep|yes|no|sure)[\.\!\?]?\s*$",
    r"^(?:hi|hello|hey|good morning|good evening)\b",
    r"\bdebug\b",
    r"\berror\b.*\btraceback\b",
    r"\btraceback\b.*\berror\b",
    r"\bstack\s*trace\b",
    r"^```[\s\S]{500,}```$",  # Large code blocks with no context
    r"\btest\b.*\btest\b",
    r"\bfixing\b.*\bbug\b",
    r"\bbug\b.*\bfixing\b",
]

# Compile all patterns for efficiency
_COMPILED = {}
for name, patterns in [
    ("decision", _DECISION_PATTERNS),
    ("identity", _IDENTITY_PATTERNS),
    ("preference", _PREFERENCE_PATTERNS),
    ("financial", _FINANCIAL_PATTERNS),
    ("project", _PROJECT_PATTERNS),
    ("ai_feedback", _AI_FEEDBACK_PATTERNS),
    ("noise", _NOISE_PATTERNS),
]:
    _COMPILED[name] = [re.compile(p, re.IGNORECASE) for p in patterns]


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------

def _count_matches(text: str, category: str) -> int:
    """Count how many patterns in a category match the text."""
    return sum(1 for pattern in _COMPILED[category] if pattern.search(text))


def grade_entry(entry: dict) -> int:
    """
    Score an extracted nugget 1-10.

    8-10: Explicitly stated, actionable, changes AI behavior
          - Direct decisions ("I decided to...", "I'm doing X")
          - Stated preferences ("I hate...", "always do X")
          - Factual identity data (name, profession, tools)
          - Financial data (income, rates, costs)
          - Project statuses with clear outcomes

    5-7: Useful context, not critical
          - One-time mentions of preferences
          - Soft patterns inferred from behavior
          - Historical context that might inform future
          - Emotional processing without clear conclusion

    1-4: Noise
          - Debugging sessions
          - "Hi" / "Thanks" / pleasantries
          - Abandoned mid-conversation topics
          - AI-generated content the user didn't endorse
          - Task-specific instructions unlikely to recur
    """
    text = entry.get("text", "")
    role = entry.get("role", "user")

    # Assistant messages are generally less memory-worthy than user messages
    # (we care about what the USER said/decided, not what AI generated)
    role_penalty = 2 if role == "assistant" else 0

    # System messages are noise
    if role == "system":
        return 1

    # Start with baseline score
    score = 5

    # --- Positive signals ---

    # Decision language is the strongest signal
    decision_hits = _count_matches(text, "decision")
    if decision_hits >= 2:
        score += 4
    elif decision_hits == 1:
        score += 3

    # Identity markers
    identity_hits = _count_matches(text, "identity")
    if identity_hits >= 2:
        score += 4
    elif identity_hits == 1:
        score += 2

    # Preference markers
    preference_hits = _count_matches(text, "preference")
    if preference_hits >= 2:
        score += 3
    elif preference_hits == 1:
        score += 2

    # Financial data
    financial_hits = _count_matches(text, "financial")
    if financial_hits >= 2:
        score += 4
    elif financial_hits == 1:
        score += 2

    # Project status
    project_hits = _count_matches(text, "project")
    if project_hits >= 1:
        score += 2

    # AI feedback — very high value
    ai_hits = _count_matches(text, "ai_feedback")
    if ai_hits >= 1:
        score += 3

    # Emotional depth: long messages with many personal pronouns
    pronoun_count = len(_PERSONAL_PRONOUNS.findall(text))
    word_count = len(text.split())
    if word_count > 100 and pronoun_count > 10:
        score += 2  # Deep personal message
    elif word_count > 50 and pronoun_count > 5:
        score += 1

    # --- Negative signals ---

    # Noise patterns
    noise_hits = _count_matches(text, "noise")
    if noise_hits >= 2:
        score -= 4
    elif noise_hits == 1:
        score -= 2

    # Very short messages are usually noise
    if word_count < 5:
        score -= 3
    elif word_count < 15:
        score -= 1

    # Apply role penalty (assistant messages worth less)
    score -= role_penalty

    # Clamp to 1-10
    return max(1, min(10, score))


def _detect_language_warning(messages: list[dict]) -> str | None:
    """Check if messages are predominantly non-English. Returns warning or None.

    Grading patterns are English-only. Non-English messages will score
    artificially low, causing under-extraction. This warns the user.
    """
    if not messages:
        return None
    # Sample up to 20 messages, check for common English words
    sample = messages[:20]
    english_markers = {"the", "and", "is", "to", "in", "for", "of", "a", "it", "that"}
    english_count = 0
    for msg in sample:
        words = set(msg.get("text", "").lower().split()[:30])
        if words & english_markers:
            english_count += 1
    ratio = english_count / len(sample)
    if ratio < 0.3:
        return (
            f"WARNING: Only {english_count}/{len(sample)} sampled messages appear English. "
            f"Grading patterns are English-only — non-English messages may score artificially low."
        )
    return None


def grade_messages(messages: list[dict]) -> list[dict]:
    """
    Grade a list of messages in-place and return them.
    Adds a 'grade' key to each message dict.
    Prints a warning if messages appear to be non-English.
    """
    warning = _detect_language_warning(messages)
    if warning:
        print(f"  [LANG] {warning}")
    for msg in messages:
        msg["grade"] = grade_entry(msg)
    return messages
