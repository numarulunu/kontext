#!/usr/bin/env python3
"""Validation harness for kontext_route.py.

Runs 30+ fixture prompts against the router and checks expected file outputs.
Use before/after each routing change to catch regressions.

Usage:
    python kontext_route_test.py            # run all, exit non-zero on failure
    python kontext_route_test.py --verbose  # show pass details too
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.claude"))
import kontext_route  # noqa: E402

# Each fixture: (label, message, cwd, must_contain, must_not_contain)
# must_contain = list of basenames that MUST appear in router output
# must_not_contain = list of basenames that MUST NOT appear
FIXTURES = [
    # --- Explicit keyword matches (should be 100%) ---
    ("vocality_brand", "help me with the vocality copy", "", ["project_vocality_brand.md"], []),
    ("luiza", "thinking about the wedding with luiza", "", ["user_luiza_dynamic.md"], []),
    ("pfa", "send me the latest anaf invoice format", "", ["user_financial_architecture.md"], []),
    ("students", "how do I teach singing technique to my students", "", ["user_vocal_expertise.md"], []),
    ("youtube", "draft a youtube script", "", ["project_vocality_content.md"], []),
    ("skool", "skool community pricing tier", "", ["project_vocality_content.md"], []),
    ("health", "my eczema is flaring after gym", "", ["user_health_protocols.md"], []),
    ("psychology", "feeling stuck on identity stuff", "", ["user_psychology.md", "user_blind_spots.md"], []),

    # --- CWD routing ---
    ("cwd_kontext", "", "C:/Users/Gaming PC/Desktop/Claude/Kontext", ["project_kontext_log.md"], []),
    ("cwd_vocality", "", "C:/Users/Gaming PC/Desktop/Claude/Vocality", ["project_vocality_skool_log.md"], []),
    ("cwd_mastermind", "", "C:/Users/Gaming PC/Desktop/Claude/Mastermind", ["project_mastermind_log.md"], []),
    ("cwd_parent_unknown", "", "C:/Users/Gaming PC/Desktop/Claude/RandomNew", ["project_goals.md"], []),

    # --- Anti-keyword traps (vocal_expertise should NOT load) ---
    ("anti_voice_of_brand", "what is the voice of brand for vocality", "", ["project_vocality_brand.md"], ["user_vocal_expertise.md"]),
    ("anti_voice_assistant", "build a voice assistant", "", [], ["user_vocal_expertise.md"]),
    ("anti_tone_of_voice", "what's the tone of voice for this copy", "", [], ["user_vocal_expertise.md"]),

    # --- Code suppression (must require both kw + ctx) ---
    ("code_real", "fix the pytest error in this function", "", [], ["project_vocality_brand.md", "user_vocal_expertise.md"]),
    ("code_real_2", "debug the docker container traceback", "", [], ["user_vocal_expertise.md"]),
    ("code_fake", "fix the vocality copy tone", "", ["project_vocality_brand.md"], []),
    ("code_fake_2", "refactor the singing curriculum", "", ["user_vocal_expertise.md"], []),

    # --- Multi-topic (should load >1 topic file) ---
    ("multi_vocality_youtube", "vocality youtube script for students", "",
     ["project_vocality_brand.md", "project_vocality_content.md", "user_vocal_expertise.md"], []),
    ("multi_money_goals", "revenue goals from pfa invoices", "",
     ["user_financial_architecture.md", "project_goals.md"], []),

    # --- Oblique phrasing (FAILS without semantic routing — these are F1 targets) ---
    ("oblique_gf", "should I tell my girlfriend about this", "", ["user_luiza_dynamic.md"], []),
    ("oblique_singing_thing", "the singing thing I do for income", "", ["user_vocal_expertise.md"], []),
    ("oblique_brand_voice", "what's our brand voice across socials", "", ["project_vocality_brand.md"], []),

    # --- Romanian phrasing ---
    ("ro_money", "facturi pentru anaf", "", ["user_financial_architecture.md"], []),
    ("ro_singing", "tehnică de cântat pentru studenți", "", ["user_vocal_expertise.md"], []),

    # --- Always-load guarantees ---
    ("always_user_core_v", "vocality", "", ["user_core.md"], []),
    ("always_user_core_p", "pfa", "", ["user_core.md"], []),

    # --- Fallback when nothing matches ---
    ("fallback_nothing", "what's the weather today", "", ["user_identity.md"], []),

    # --- Edge: pure code in known dir should still suppress ---
    ("cwd_kontext_code", "fix the pytest error in mcp_server.py function", "C:/Users/Gaming PC/Desktop/Claude/Kontext",
     ["project_kontext_log.md"], ["user_vocal_expertise.md"]),

    # --- F1 stress: oblique with NO known keyword anywhere ---
    ("F1_main_gig", "thoughts on my main gig long-term", "", ["project_goals.md"], []),
    ("F1_partner", "should I tell my partner about quitting", "", ["user_luiza_dynamic.md"], []),
    ("F1_kid_lessons", "the kids I teach online keep dropping out", "", ["user_vocal_expertise.md"], []),
    ("F1_anaf_oblique", "the romanian tax thing for solo founders", "", ["user_financial_architecture.md"], []),
    ("F1_revenue", "how do I 10x my income this year", "", ["project_goals.md"], []),

    # --- F2 stress: 5+ topic prompt should load all relevant ---
    ("F2_kitchen_sink", "vocality launch plan with luiza wedding pfa invoices and youtube content",
     "", ["project_vocality_brand.md", "user_luiza_dynamic.md", "user_financial_architecture.md", "project_vocality_content.md"], []),

    # --- Anti-keyword stress (negative tests) ---
    ("anti_identity_col", "add an identity column to the users table", "", [], ["user_psychology.md"]),
    ("anti_git_blocked", "the git push is blocked by precommit hook", "", [], ["user_blind_spots.md"]),
    ("anti_webhook", "set up a webhook for stripe", "", [], ["user_influences.md"]),
    ("anti_style_guide", "follow the css style guide", "", [], ["feedback_ai_interaction.md"]),
    ("anti_build_relationship", "how do I build a relationship with my new students", "",
     ["user_vocal_expertise.md"], ["tool_registry.md"]),
    ("anti_skin_tone", "the skin tone in this photo is off", "", [], ["feedback_ai_interaction.md"]),

    # --- Code-task fakeouts ---
    ("code_fakeout_branding", "fix the branding on this landing page", "",
     ["project_vocality_brand.md"], []),
    ("code_real_with_topic", "fix the pytest in vocality_test.py module", "C:/Users/Gaming PC/Desktop/Claude/Vocality",
     ["project_vocality_skool_log.md"], []),

    # --- Deep multi-route ---
    ("F3_health_goals", "scaling my income while my eczema flares", "",
     ["user_health_protocols.md", "project_goals.md"], []),
    ("F3_emotion_money", "feeling anxious about pfa taxes", "",
     ["user_psychology.md", "user_financial_architecture.md"], []),

    # --- Fuzzy fallback (substring hit, no clean match) ---
    ("fuzzy_substring", "what about my plansomething tonight", "",
     ["user_identity.md", "user_psychology.md"], []),

    # --- Fuzzy v2: stem matching ---
    ("fuzzyv2_kid", "the kid I work with online", "", ["user_vocal_expertise.md"], []),
    ("fuzzyv2_overwhelmed", "i am totally overwhelmed", "", ["user_psychology.md"], []),

    # --- Validator-found gap: now-routed orphans ---
    ("orphan_strengths", "what are my core strength areas", "", ["user_strengths.md"], []),
    ("orphan_smac", "what's the smac audit say", "", ["project_smac_audit.md"], []),
]


def run_test(label: str, message: str, cwd: str,
             must_contain: list[str], must_not_contain: list[str]) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        state_path = Path(f.name)
    try:
        result = kontext_route.route(
            message=message,
            cwd=cwd,
            reset=True,
            state_path=state_path,
            commit_state=False,
            log_decision=False,
        )
        files = result.get("files", [])
        basenames = {os.path.basename(p) for p in files}

        missing = [f for f in must_contain if f not in basenames]
        unexpected = [f for f in must_not_contain if f in basenames]

        if missing or unexpected:
            parts = []
            if missing:
                parts.append(f"MISSING: {missing}")
            if unexpected:
                parts.append(f"UNEXPECTED: {unexpected}")
            parts.append(f"GOT: {sorted(basenames)}")
            return False, " | ".join(parts)
        return True, f"OK ({sorted(basenames)})"
    finally:
        try:
            state_path.unlink()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    passed = 0
    failed = 0
    failures: list[tuple[str, str]] = []

    for fix in FIXTURES:
        label, message, cwd, must_contain, must_not_contain = fix
        ok, detail = run_test(label, message, cwd, must_contain, must_not_contain)
        if ok:
            passed += 1
            if args.verbose:
                print(f"  PASS  {label:30s} {detail}")
        else:
            failed += 1
            failures.append((label, detail))
            print(f"  FAIL  {label:30s} {detail}")

    print()
    print(f"=== {passed}/{passed + failed} passed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
