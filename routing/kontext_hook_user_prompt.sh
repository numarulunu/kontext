#!/usr/bin/env bash
# Kontext UserPromptSubmit hook: per-message topic routing.
#
# Fires on every user prompt. Extracts the message text, routes against
# topic keywords only (CWD already handled at session start), dedup's
# against state so we only inject files not yet loaded this session.
# Silent when nothing new to add.

set -u

if [ -n "${KONTEXT_SKIP_HOOKS:-}" ]; then
  exit 0
fi

INPUT=$(cat)

MSG=$(echo "$INPUT" | python -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(d.get("prompt","") or d.get("message","") or "")
' 2>/dev/null || echo "")

if [ -z "$MSG" ]; then
  exit 0
fi

# Skip trivial prompts: <3 chars (after trim) or pure slash command.
# These are usually CLI control (/clear, /help, /model) that shouldn't
# trigger memory routing or pollute session state.
TRIMMED=$(echo "$MSG" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
MSG_LEN=${#TRIMMED}
if [ "$MSG_LEN" -lt 3 ]; then
  exit 0
fi
case "$TRIMMED" in
  /*) exit 0 ;;
esac

CWD="${CLAUDE_PROJECT_DIR:-$PWD}"

ROUTE_ERR=$(mktemp 2>/dev/null || echo "$HOME/.claude/_route_err_user.tmp")
ROUTE_OUTPUT=$(python "$HOME/.claude/kontext_route.py" --cwd "$CWD" --message "$MSG" 2>"$ROUTE_ERR")
ROUTE_EXIT=$?
if [ "$ROUTE_EXIT" -ne 0 ] || [ -s "$ROUTE_ERR" ]; then
  ERR_SUMMARY=$(head -c 200 "$ROUTE_ERR" | tr '\n' ' ' | tr '"' "'")
  rm -f "$ROUTE_ERR"
  printf '{"systemMessage":"[Kontext router FAILED on prompt: exit=%s err=%s]"}\n' "$ROUTE_EXIT" "$ERR_SUMMARY"
  exit 0
fi
rm -f "$ROUTE_ERR"

PARSED=$(echo "$ROUTE_OUTPUT" | python -c '
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
fuzzy = d.get("fuzzy_fired", False)
prefix = "[Kontext fuzzy-fallback] Message matched a topic keyword as substring but no exact rule. Loading identity files — the user may be referring to themselves obliquely. Treat as personal context." if fuzzy else "[Kontext topic-route] The user message matched routing rules. These memory files were NOT previously loaded this session and are now in context. Do NOT re-read them."
body = "\n\n".join(parts)
print(json.dumps({"additionalContext": prefix + "\n\n" + body}))
' 2>/dev/null)

if [ -z "$PARSED" ]; then
  exit 0
fi

echo "$PARSED"
