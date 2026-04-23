#!/usr/bin/env python3
"""Kontext routing engine — maps topic/CWD to memory files.

Used by SessionStart and UserPromptSubmit hooks. Loads the routing config
from ~/.claude/kontext_routing.yaml, matches keywords against a message or
CWD against path fragments, returns a deduplicated ordered list of memory
file paths to inject as additionalContext.

Stdlib-only. No PyYAML dependency — uses a tiny hand-rolled parser sufficient
for the fixed schema. If PyYAML is available, it's used as a faster path.

CLI:
    python kontext_route.py --cwd "/path/to/cwd" --message "some user text"
        → JSON: {"files": [absolute paths], "suggested_skip": bool}

    python kontext_route.py --all
        → JSON with every memory file under memory_root

Env:
    KONTEXT_ROUTE_STATE  — path to session state file (default
        ~/.claude/_kontext_loaded.json). Used by hooks to avoid
        reloading files already injected earlier in the same session.
    KONTEXT_ROUTE_RESET  — if set, wipe state before routing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.claude/kontext_routing.yaml"))
DEFAULT_STATE_PATH = Path(os.path.expanduser("~/.claude/_kontext_loaded.json"))
DEFAULT_LOG_PATH = Path(os.path.expanduser("~/.claude/_kontext_route.log"))
STATE_TTL_SECONDS = 1800  # 30 min — re-inject if older to catch mid-session memory updates
LOG_MAX_LINES = 10000


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        return _parse_yaml_fallback(path.read_text(encoding="utf-8"))


def _parse_yaml_fallback(text: str) -> dict:
    """Minimal YAML subset parser for our fixed schema.

    Handles: top-level scalars, lists of dicts, scalar values, bracketed
    inline lists. Not a general YAML parser — only enough for
    kontext_routing.yaml.
    """
    result: dict = {}
    lines = [ln.rstrip() for ln in text.splitlines()]
    i = 0

    def strip_quotes(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s

    def parse_inline_list(s: str) -> list[str]:
        inner = s.strip()[1:-1]
        out: list[str] = []
        buf: list[str] = []
        in_quote: str | None = None
        for ch in inner:
            if in_quote:
                if ch == in_quote:
                    in_quote = None
                else:
                    buf.append(ch)
            elif ch in ("'", '"'):
                in_quote = ch
            elif ch == ",":
                token = "".join(buf).strip()
                if token:
                    out.append(strip_quotes(token))
                buf = []
            else:
                buf.append(ch)
        token = "".join(buf).strip()
        if token:
            out.append(strip_quotes(token))
        return out

    current_list_key: str | None = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        if indent == 0 and ":" in stripped:
            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "" or rest is None:
                peek = i + 1
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek < len(lines) and lines[peek].lstrip().startswith("- "):
                    current_list_key = key
                    result[key] = []
                    i += 1
                    continue
                else:
                    result[key] = None
                    i += 1
                    continue
            if rest.startswith("["):
                result[key] = parse_inline_list(rest)
            elif rest.isdigit():
                result[key] = int(rest)
            else:
                result[key] = strip_quotes(rest)
            current_list_key = None
            i += 1
            continue

        if stripped.startswith("- ") and current_list_key:
            content = stripped[2:].strip()
            # Bare-string list item (e.g. `  - user_core.md`) has no `:` —
            # emit a string, not a {name: None} dict. This is what breaks
            # `always_load`/`default_fallback`/`fuzzy_fallback` when the
            # fallback parser is used (PyYAML missing).
            if ":" not in content:
                if content.startswith("[") and content.endswith("]"):
                    result[current_list_key].append(parse_inline_list(content))
                else:
                    result[current_list_key].append(strip_quotes(content))
                i += 1
                continue
            item: dict = {}
            first_key, _, first_val = content.partition(":")
            first_key = first_key.strip()
            first_val = first_val.strip()
            if first_val.startswith("["):
                item[first_key] = parse_inline_list(first_val)
            elif first_val:
                item[first_key] = strip_quotes(first_val)
            else:
                item[first_key] = None
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip() or nxt.strip().startswith("#"):
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent == 0 or nxt.lstrip().startswith("- "):
                    break
                k, _, v = nxt.strip().partition(":")
                k = k.strip()
                v = v.strip()
                if v.startswith("["):
                    item[k] = parse_inline_list(v)
                elif v.isdigit():
                    item[k] = int(v)
                else:
                    item[k] = strip_quotes(v)
                i += 1
            result[current_list_key].append(item)
            continue

        i += 1

    return result


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return _load_yaml(CONFIG_PATH) or {}
    except Exception as e:
        sys.stderr.write(f"[kontext_route] config parse error: {e}\n")
        return {}


def _resolve_memory_root(cfg: dict) -> Path:
    root = cfg.get("memory_root") or "~/.claude/projects/C--Users-Gaming-PC-Desktop-Claude-Personal-Context/memory"
    return Path(os.path.expanduser(root))


def _match_keyword(message_lower: str, kw: str) -> bool:
    kw_lower = kw.lower().strip()
    if not kw_lower:
        return False
    if " " in kw_lower or "-" in kw_lower:
        return kw_lower in message_lower
    return re.search(rf"(?<!\w){re.escape(kw_lower)}(?!\w)", message_lower) is not None


def _match_cwd(cwd: str, fragment: str) -> bool:
    cwd_norm = cwd.replace("\\", "/").lower()
    frag_norm = fragment.replace("\\", "/").lower()
    return frag_norm in cwd_norm


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"loaded_files": {}}
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"[kontext_route] state read error: {e}\n")
        return {"loaded_files": {}}
    if not text.strip():
        return {"loaded_files": {}}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[kontext_route] state file CORRUPT — rebuilding: {e}\n")
        try:
            backup = state_path.parent / (state_path.name + ".corrupt.bak")
            state_path.replace(backup)
        except OSError:
            try:
                state_path.unlink()
            except OSError:
                pass
        return {"loaded_files": {}}
    if not isinstance(raw, dict):
        sys.stderr.write("[kontext_route] state root not dict — rebuilding\n")
        return {"loaded_files": {}}
    lf = raw.get("loaded_files")
    if isinstance(lf, list):
        now = int(time.time())
        raw["loaded_files"] = {f: now for f in lf}
    elif not isinstance(lf, dict):
        raw["loaded_files"] = {}
    return raw


_SUFFIXES = ("ings", "ing", "edly", "ed", "ies", "ied", "ier", "iest",
             "ness", "ments", "ment", "tions", "tion", "ers", "er",
             "ly", "ous", "ses", "es", "s")


def _stem(word: str) -> str:
    """Crude suffix-strip stemmer. Good enough to match teaching↔teach,
    kids↔kid, anxious↔anxious. Stops at 3-char minimum to avoid over-stripping."""
    w = word.lower().strip()
    if len(w) <= 3:
        return w
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Zăâîșțéíáúüöö'-]+", text.lower()) if t]


def _bigrams(tokens: list[str]) -> set[str]:
    return {f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)}


def _fuzzy_topic_match(message_lower: str, all_topic_kws: set[str]) -> bool:
    """Multi-strategy fuzzy match:
    1. Substring (cheap, catches 'plansomething')
    2. Stem-equality (catches teaching↔teach, kids↔kid)
    3. Bigram match against multi-word keywords
    """
    if any(kw in message_lower for kw in all_topic_kws):
        return True
    tokens = _tokenize(message_lower)
    if not tokens:
        return False
    msg_stems = {_stem(t) for t in tokens if len(t) >= 3}
    for kw in all_topic_kws:
        if " " in kw:
            continue
        if _stem(kw) in msg_stems:
            return True
    msg_bigrams = _bigrams(tokens)
    for kw in all_topic_kws:
        if " " in kw and kw in msg_bigrams:
            return True
    return False


def _hash_message(message: str) -> str:
    import hashlib
    return hashlib.sha1(message.encode("utf-8", errors="replace")).hexdigest()[:10]


def _append_log(entry: dict, log_path: Path = DEFAULT_LOG_PATH) -> None:
    """Append a JSONL line to the routing log. Rotates at LOG_MAX_LINES."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                if len(lines) > LOG_MAX_LINES:
                    keep = lines[-LOG_MAX_LINES // 2:]
                    log_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
            except OSError:
                pass
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _save_state_atomic(state_path: Path, state: dict) -> None:
    """Atomic write: tmp file + rename. Prevents partial writes from corrupting state."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(state_path)
    except OSError as e:
        sys.stderr.write(f"[kontext_route] atomic state write error: {e}\n")


def _fresh_loaded_set(state: dict, memory_root: Path,
                      ttl: int = STATE_TTL_SECONDS) -> set[str]:
    """Return loaded files that are still fresh — both within TTL AND not modified
    on disk since they were loaded. Files re-exported by kontext_write get
    re-injected on the next prompt."""
    now = int(time.time())
    lf = state.get("loaded_files", {}) or {}
    if not isinstance(lf, dict):
        return set()
    fresh: set[str] = set()
    for fname, ts in lf.items():
        if not isinstance(ts, (int, float)):
            continue
        if now - ts >= ttl:
            continue
        path = memory_root / fname
        try:
            mtime = path.stat().st_mtime
            if mtime > ts:
                continue
        except OSError:
            pass
        fresh.add(fname)
    return fresh


def _save_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        sys.stderr.write(f"[kontext_route] state write error: {e}\n")


def route(message: str = "", cwd: str = "", reset: bool = False,
          state_path: Path | None = None, commit_state: bool = True,
          log_decision: bool = True) -> dict:
    """Core routing function.

    Returns dict:
      files          — list of absolute paths to load (new, not yet loaded)
      suggested_skip — bool; true if this turn looks like a pure code task
      matched_topics — list of keyword-group labels that fired (for debug)
      skipped_cwd    — True if CWD rule matched but files already loaded
      fuzzy_fired    — True if fuzzy fallback fired
    """
    t_start = time.time()
    cfg = load_config()
    if not cfg:
        return {"files": [], "suggested_skip": False, "matched_topics": [], "skipped_cwd": False}

    memory_root = _resolve_memory_root(cfg)
    state_path = state_path or DEFAULT_STATE_PATH
    if reset and state_path.exists():
        try:
            state_path.unlink()
        except Exception:
            pass
    state = _load_state(state_path)
    already_loaded = _fresh_loaded_set(state, memory_root)

    message_lower = (message or "").lower()

    to_load: list[str] = []
    matched: list[str] = []

    for f in cfg.get("always_load", []) or []:
        to_load.append(f)

    skipped_cwd = False
    for rule in cfg.get("cwd_routes", []) or []:
        fragments = rule.get("path_contains", []) or []
        if isinstance(fragments, str):
            fragments = [fragments]
        if cwd and any(_match_cwd(cwd, frag) for frag in fragments):
            for f in rule.get("files", []) or []:
                to_load.append(f)
            matched.append(f"cwd:{fragments[0]}")
            break

    code_kws = cfg.get("code_task_keywords", []) or []
    code_ctx_kws = cfg.get("code_context_keywords", []) or []
    has_code_kw = any(_match_keyword(message_lower, kw) for kw in code_kws)
    has_code_ctx = any(_match_keyword(message_lower, kw) for kw in code_ctx_kws)
    is_code_task = has_code_kw and has_code_ctx

    if not is_code_task and message_lower:
        for rule in cfg.get("topic_routes", []) or []:
            kws = rule.get("keywords", []) or []
            anti_kws = rule.get("anti_keywords", []) or []
            if any(_match_keyword(message_lower, kw) for kw in kws):
                if any(_match_keyword(message_lower, akw) for akw in anti_kws):
                    continue
                for f in rule.get("files", []) or []:
                    to_load.append(f)
                matched.append(f"topic:{kws[0] if kws else '?'}")

    fuzzy_fired = False
    if not is_code_task and not matched and message_lower:
        # Gate fuzzy on code_context: if message looks technical (column, function, schema),
        # skip fuzzy entirely even if a topic-keyword substring appears.
        if has_code_ctx:
            for f in cfg.get("default_fallback", []) or []:
                to_load.append(f)
            matched.append("fallback")
        else:
            all_topic_kws: set[str] = set()
            for rule in cfg.get("topic_routes", []) or []:
                for kw in rule.get("keywords", []) or []:
                    if kw and len(kw) >= 3:
                        all_topic_kws.add(kw.lower().strip())
            if _fuzzy_topic_match(message_lower, all_topic_kws):
                fuzzy_files = cfg.get("fuzzy_fallback", []) or cfg.get("default_fallback", []) or []
                for f in fuzzy_files:
                    to_load.append(f)
                matched.append("fuzzy-fallback")
                fuzzy_fired = True
            else:
                for f in cfg.get("default_fallback", []) or []:
                    to_load.append(f)
                matched.append("fallback")

    max_files = int(cfg.get("max_files_per_turn", 8) or 8)
    token_budget = int(cfg.get("token_budget_hint", 4000) or 4000)
    char_budget = token_budget * 4  # ~4 chars/token approximation

    seen: set[str] = set()
    dedup: list[str] = []
    for f in to_load:
        if f in seen:
            continue
        seen.add(f)
        if f in already_loaded:
            continue
        dedup.append(f)

    abs_paths: list[str] = []
    chars_used = 0
    for f in dedup:
        if len(abs_paths) >= max_files:
            break
        p = memory_root / f
        if not p.exists():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if abs_paths and chars_used + size > char_budget:
            continue
        abs_paths.append(str(p))
        chars_used += size

    if commit_state and abs_paths:
        now = int(time.time())
        lf = state.get("loaded_files", {})
        if not isinstance(lf, dict):
            lf = {}
        loaded_basenames = {os.path.basename(p) for p in abs_paths}
        for f in loaded_basenames:
            lf[f] = now
        state["loaded_files"] = lf
        _save_state_atomic(state_path, state)

    elapsed_ms = int((time.time() - t_start) * 1000)
    result = {
        "files": abs_paths,
        "suggested_skip": is_code_task,
        "matched_topics": matched,
        "skipped_cwd": skipped_cwd,
        "fuzzy_fired": fuzzy_fired,
        "elapsed_ms": elapsed_ms,
    }
    if log_decision:
        _append_log({
            "ts": int(time.time()),
            "msg_hash": _hash_message(message),
            "msg_preview": message[:60],
            "cwd_tail": cwd[-40:] if cwd else "",
            "matched": matched,
            "files": [os.path.basename(p) for p in abs_paths],
            "fuzzy": fuzzy_fired,
            "code_skip": is_code_task,
            "ms": elapsed_ms,
        })
    return result


def list_all_files() -> dict:
    cfg = load_config()
    if not cfg:
        return {"files": []}
    memory_root = _resolve_memory_root(cfg)
    files = sorted(str(p) for p in memory_root.glob("*.md") if p.name != "MEMORY.md")
    return {"files": files}


def validate_config() -> dict:
    """Validate kontext_routing.yaml: every referenced file exists, no empty
    keywords, no orphan files in memory_root."""
    cfg = load_config()
    if not cfg:
        return {"ok": False, "errors": ["config not loaded"], "warnings": []}

    memory_root = _resolve_memory_root(cfg)
    errors: list[str] = []
    warnings: list[str] = []

    if not memory_root.exists():
        errors.append(f"memory_root does not exist: {memory_root}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    available = {p.name for p in memory_root.glob("*.md")}
    referenced: set[str] = set()

    def check_file_list(label: str, files: list) -> None:
        if not files:
            return
        for f in files:
            referenced.add(f)
            if f not in available:
                errors.append(f"{label} references missing file: {f}")

    check_file_list("always_load", cfg.get("always_load", []) or [])
    check_file_list("default_fallback", cfg.get("default_fallback", []) or [])
    check_file_list("fuzzy_fallback", cfg.get("fuzzy_fallback", []) or [])

    for i, rule in enumerate(cfg.get("topic_routes", []) or []):
        kws = rule.get("keywords", []) or []
        if not kws:
            errors.append(f"topic_routes[{i}] has no keywords")
        for kw in kws:
            if not kw or not str(kw).strip():
                errors.append(f"topic_routes[{i}] has empty keyword")
        check_file_list(f"topic_routes[{i}]", rule.get("files", []) or [])
        for akw in rule.get("anti_keywords", []) or []:
            if not akw or not str(akw).strip():
                errors.append(f"topic_routes[{i}] has empty anti_keyword")

    for i, rule in enumerate(cfg.get("cwd_routes", []) or []):
        frags = rule.get("path_contains", []) or []
        if isinstance(frags, str):
            frags = [frags]
        if not frags:
            errors.append(f"cwd_routes[{i}] has no path_contains")
        check_file_list(f"cwd_routes[{i}]", rule.get("files", []) or [])

    orphans = available - referenced - {"MEMORY.md"}
    for f in sorted(orphans):
        warnings.append(f"orphan: {f} not referenced by any rule")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "files_referenced": len(referenced), "files_available": len(available)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default="", help="Working directory of the session")
    ap.add_argument("--message", default="", help="User message text")
    ap.add_argument("--reset", action="store_true", help="Wipe session-loaded state first")
    ap.add_argument("--all", action="store_true", help="List all memory files (heavy mode)")
    ap.add_argument("--no-commit", action="store_true",
                    help="Do not update loaded-state file (dry-run)")
    ap.add_argument("--state-path", default="", help="Override state file path")
    ap.add_argument("--validate", action="store_true",
                    help="Validate routing config; exit 0 if clean, 1 if errors")
    args = ap.parse_args()

    if args.validate:
        result = validate_config()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if args.all:
        print(json.dumps(list_all_files()))
        return 0

    state_path = Path(args.state_path) if args.state_path else None
    result = route(
        message=args.message,
        cwd=args.cwd,
        reset=args.reset,
        state_path=state_path,
        commit_state=not args.no_commit,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _exc:  # noqa: BLE001 — forensic log of any router crash
        import traceback as _tb
        try:
            _LOG_PATH = Path(__file__).resolve().parent / "_kontext_route.log"
            _line = {
                "ts": int(time.time()),
                "error": repr(_exc),
                "argv": sys.argv[1:],
                "tb_tail": _tb.format_exc().strip().splitlines()[-3:],
            }
            with _LOG_PATH.open("a", encoding="utf-8") as _f:
                _f.write(json.dumps(_line) + "\n")
        except Exception:
            pass
        raise
