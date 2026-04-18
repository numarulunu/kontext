#!/usr/bin/env bash
# Kontext PostCompact hook: re-inject always_load files after /compact.
#
# /compact preserves loaded-state cache but flushes Claude's context. Without
# this hook, after a compact the user_core.md content is gone but the router
# thinks it's still loaded (because state file says so) and won't re-inject
# on subsequent prompts. This hook forces a re-injection by reading the
# always_load files directly and surfacing them as additionalContext.
#
# Silent on failure. Never blocks.

set -u

if [ -n "${KONTEXT_SKIP_HOOKS:-}" ]; then
  echo '{"suppressOutput":true}'
  exit 0
fi

ROUTE_ERR=$(mktemp 2>/dev/null || echo "$HOME/.claude/_route_err_compact.tmp")
ALL_OUTPUT=$(python "$HOME/.claude/kontext_route.py" --reset --cwd "$PWD" --no-commit 2>"$ROUTE_ERR")
ROUTE_EXIT=$?
if [ "$ROUTE_EXIT" -ne 0 ] || [ -s "$ROUTE_ERR" ]; then
  ERR_SUMMARY=$(head -c 200 "$ROUTE_ERR" | tr '\n' ' ' | tr '"' "'")
  rm -f "$ROUTE_ERR"
  printf '{"systemMessage":"[Kontext post-compact router FAILED: exit=%s err=%s]"}\n' "$ROUTE_EXIT" "$ERR_SUMMARY"
  exit 0
fi
rm -f "$ROUTE_ERR"

BODY=$(echo "$ALL_OUTPUT" | python -c '
import sys, json, os
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
files = d.get("files", [])
if not files:
    sys.exit(0)
parts = []
for p in files:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        parts.append(f"=== {os.path.basename(p)} ===\n{content}")
    except Exception:
        continue
if not parts:
    sys.exit(0)
print("\n\n".join(parts))
' 2>/dev/null)

if [ -z "$BODY" ]; then
  echo '{"suppressOutput":true}'
  exit 0
fi

python -c '
import json, sys
body = sys.stdin.read()
msg = "[Kontext post-compact reload] /compact flushed your context. Re-injecting always_load + CWD-routed memory so you remain aware of the user.\n\n" + body
print(json.dumps({"additionalContext": msg}))
' <<< "$BODY"
