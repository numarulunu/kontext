#!/usr/bin/env bash
# Kontext SessionStart hook: CWD + initial-message routing.
#
# Reads hook input JSON from stdin. Resets loaded-state so a new session
# starts fresh, then runs the router using:
#   - CWD from $PWD (Claude injects it in the hook env)
#   - initial message from the hook input (empty on most session starts)
# Injects the routed files as additionalContext so Claude sees them
# before responding.
#
# Silent on failure. Never blocks the session.

set -u

if [ -n "${KONTEXT_SKIP_HOOKS:-}" ]; then
  echo '{"suppressOutput":true}'
  exit 0
fi

INPUT=$(cat)

if echo "$INPUT" | grep -q '"source":"compact"'; then
  echo '{"suppressOutput":true}'
  exit 0
fi

CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
MSG=$(echo "$INPUT" | python -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get("message","") or d.get("prompt","") or "")
except Exception:
    print("")' 2>/dev/null || echo "")

ROUTE_ERR=$(mktemp 2>/dev/null || echo "$HOME/.claude/_route_err.tmp")
ROUTE_OUTPUT=$(python "$HOME/.claude/kontext_route.py" --reset --cwd "$CWD" --message "$MSG" 2>"$ROUTE_ERR")
ROUTE_EXIT=$?
if [ "$ROUTE_EXIT" -ne 0 ] || [ -s "$ROUTE_ERR" ]; then
  ERR_SUMMARY=$(head -c 200 "$ROUTE_ERR" | tr '\n' ' ' | tr '"' "'")
  rm -f "$ROUTE_ERR"
  printf '{"systemMessage":"[Kontext router FAILED at session start: exit=%s err=%s] No memory auto-loaded. Use /kontext load-all manually."}\n' "$ROUTE_EXIT" "$ERR_SUMMARY"
  exit 0
fi
rm -f "$ROUTE_ERR"

FILES=$(echo "$ROUTE_OUTPUT" | python -c '
import sys, json, os
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
parts = []
for p in d.get("files", []):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        name = os.path.basename(p)
        parts.append(f"=== {name} ===\n{content}")
    except Exception:
        continue
print("\n\n".join(parts))
' 2>/dev/null)

if [ -z "$FILES" ]; then
  STATE_PATH="$HOME/.claude/_kontext_loaded.json"
  STATE_EMPTY=1
  if [ -f "$STATE_PATH" ]; then
    if python -c "import json,sys; d=json.load(open(r'$STATE_PATH')); sys.exit(0 if not d.get('loaded_files') else 1)" 2>/dev/null; then
      STATE_EMPTY=1
    else
      STATE_EMPTY=0
    fi
  fi
  if [ "$STATE_EMPTY" = "1" ]; then
    echo '{"systemMessage":"[Kontext] Session started with no memory matched (CWD unknown, no initial keyword). Run /kontext load-all if this session needs user context."}'
  else
    echo '{"suppressOutput":true}'
  fi
  exit 0
fi

python -c '
import json, sys
body = sys.stdin.read()
msg = "[Kontext auto-load] Loaded relevant memory files based on working directory and initial context. Treat this as if the routing table in CLAUDE.md had been followed. Do NOT re-read these files — they are already in context below.\n\n" + body
print(json.dumps({"additionalContext": msg}))
' <<< "$FILES"
