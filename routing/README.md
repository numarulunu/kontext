# Kontext Routing

Auto-loads relevant memory files into Claude's context based on the user's
message and working directory. Solves the "Claude doesn't know about me" problem
by routing per-prompt instead of relying on Claude to remember to query.

## How it works

Three Claude Code hooks fire automatically:

- **SessionStart** — routes by CWD + initial message, injects matching memory files
- **UserPromptSubmit** — routes per-prompt by keyword/stem/bigram match
- **PostCompact** — re-injects memory after `/compact` flushes context

A YAML config (`kontext_routing.yaml`) maps keywords and CWD paths to memory
files. The router has 5 layers of matching:

1. **Always-load** files (e.g. `user_core.md`)
2. **CWD routes** (working dir → project files)
3. **Topic keyword routes** with anti-keyword filters
4. **Fuzzy fallback** — substring + stem + bigram match against keyword union
5. **Default fallback** — identity files when nothing else fires

Code suppression: when message contains both a code keyword (`fix`, `debug`)
AND a code-context keyword (`function`, `pytest`, `column`), topic routing is
skipped to avoid loading personal context for technical questions.

## Setup

1. Copy hook scripts to `~/.claude/`:
   ```
   cp kontext_route.py ~/.claude/
   cp kontext_route_test.py ~/.claude/
   cp kontext_audit_keywords.py ~/.claude/
   cp kontext_hook_*.sh ~/.claude/
   ```

2. Copy the example config and customize:
   ```
   cp kontext_routing.example.yaml ~/.claude/kontext_routing.yaml
   # Edit memory_root, topic_routes, cwd_routes for your projects
   ```

3. Register the hooks in `~/.claude/settings.json`:
   ```json
   {
     "hooks": {
       "SessionStart": [
         {"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/kontext_hook_session_start.sh\""}]}
       ],
       "UserPromptSubmit": [
         {"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/kontext_hook_user_prompt.sh\"", "timeout": 2}]}
       ],
       "PostCompact": [
         {"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/kontext_hook_post_compact.sh\"", "timeout": 5}]}
       ]
     }
   }
   ```

4. Validate the config:
   ```
   python ~/.claude/kontext_route.py --validate
   ```

## Maintenance

- `python ~/.claude/kontext_route.py --validate` — verify all referenced files exist
- `python ~/.claude/kontext_audit_keywords.py` — propose missing keywords from memory descriptions
- `python ~/.claude/kontext_route_test.py` — run 51 fixture tests

## Telemetry

Every routing decision logs to `~/.claude/_kontext_route.log` (JSONL, rotates
at ~2MB). Inspect when routing feels off:

```
tail -20 ~/.claude/_kontext_route.log | python -m json.tool
```

## Config schema

See `kontext_routing.example.yaml` for the full schema with comments.

## Reliability

51 fixture tests cover: explicit keyword matches, CWD routing, anti-keyword
suppression, code task suppression, multi-topic prompts, fuzzy stem matching,
oblique phrasing, Romanian phrasing, fallback paths.

Per-prompt routing latency: 28-60ms. Hook timeout budget: 2000ms.
