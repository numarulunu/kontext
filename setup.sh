#!/bin/bash
set -euo pipefail

# setup.sh — Initialize Kontext memory system for Claude Code
# Creates the memory directory, MEMORY.md index, starter CLAUDE.md with
# retrieval protocol, and a SessionStart hook for digest detection.
#
# Usage: ./setup.sh

echo "=== Kontext — Memory Library Setup ==="
echo ""

CLAUDE_DIR="$HOME/.claude"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Verify Claude Code is installed ---
if [ ! -d "$CLAUDE_DIR" ]; then
    echo "ERROR: Claude Code config not found at $CLAUDE_DIR"
    echo "Install Claude Code CLI first: https://claude.ai/code"
    exit 1
fi

# --- Find the current project's memory directory ---
# Claude Code stores project-specific data in ~/.claude/projects/<encoded-path>/
# The encoding format is: C--Users-Name-Desktop-Project (drive letter, double-dash for path separators)
# We find the EXISTING project with the most memory files (the user's main library)

PROJECT_DIR=""
BEST_COUNT=0

for d in "$CLAUDE_DIR/projects"/*/; do
    if [ -d "$d/memory" ] && [ -f "$d/memory/MEMORY.md" ]; then
        COUNT=$(find "$d/memory" -name "*.md" -type f 2>/dev/null | wc -l)
        if [ "$COUNT" -gt "$BEST_COUNT" ]; then
            BEST_COUNT=$COUNT
            PROJECT_DIR="${d%/}"
        fi
    fi
done

# If no existing memory found, find any project dir (user hasn't used memory yet)
if [ -z "$PROJECT_DIR" ]; then
    for d in "$CLAUDE_DIR/projects"/*/; do
        if [ -d "$d" ]; then
            PROJECT_DIR="${d%/}"
            echo "  No existing memory found. Using: $PROJECT_DIR"
            break
        fi
    done
fi

if [ -z "$PROJECT_DIR" ]; then
    echo "  ERROR: No Claude Code projects found. Use Claude Code at least once first."
    exit 1
fi

echo "  Found project: $PROJECT_DIR (${BEST_COUNT} memory files)"

MEMORY_DIR="$PROJECT_DIR/memory"

# --- Detect existing memory system ---
if [ "$BEST_COUNT" -gt 5 ]; then
    echo ""
    echo "  You already have a memory system with ${BEST_COUNT} files."
    echo "  Kontext will ADD management features (skill, hooks, semantic search)"
    echo "  without overwriting your existing memory files."
    echo ""
    read -p "  Continue? [y/n]: " CONTINUE
    if [ "$CONTINUE" != "y" ]; then
        echo "  Aborted. Your existing system is untouched."
        exit 0
    fi
fi

mkdir -p "$MEMORY_DIR"
echo "  Memory directory: $MEMORY_DIR"

# --- Create MEMORY.md index if it doesn't exist ---
if [ ! -f "$MEMORY_DIR/MEMORY.md" ]; then
    cp "$SCRIPT_DIR/templates/MEMORY.md" "$MEMORY_DIR/MEMORY.md"
    echo "  Created MEMORY.md index"
else
    echo "  MEMORY.md already exists — skipping"
fi

# --- Create starter memory files ---
for tmpl in "$SCRIPT_DIR/templates/memory/"*.md; do
    filename=$(basename "$tmpl")
    if [ ! -f "$MEMORY_DIR/$filename" ]; then
        cp "$tmpl" "$MEMORY_DIR/$filename"
        echo "  Created $filename"
    else
        echo "  $filename already exists — skipping"
    fi
done

# --- Install CLAUDE.md retrieval protocol ---
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
if [ -f "$CLAUDE_MD" ]; then
    # Check if retrieval protocol already exists
    if grep -q "MEMORY RETRIEVAL" "$CLAUDE_MD" 2>/dev/null || grep -q "STARTUP SEQUENCE" "$CLAUDE_MD" 2>/dev/null; then
        echo "  CLAUDE.md already has retrieval protocol — skipping"
    else
        echo ""
        echo "  Your CLAUDE.md exists but doesn't have the Kontext retrieval protocol."
        echo "  The protocol tells Claude to automatically read memory files at conversation start."
        echo ""
        echo "  Options:"
        echo "    1) Prepend the protocol to your existing CLAUDE.md (recommended)"
        echo "    2) Skip — add it manually later"
        echo ""
        read -p "  Choice [1/2]: " choice
        if [ "$choice" = "1" ]; then
            # Prepend protocol to existing CLAUDE.md
            TEMP=$(mktemp)
            cat "$SCRIPT_DIR/templates/CLAUDE-protocol.md" "$CLAUDE_MD" > "$TEMP"
            mv "$TEMP" "$CLAUDE_MD"
            echo "  Retrieval protocol prepended to CLAUDE.md"
        else
            echo "  Skipped. Copy from templates/CLAUDE-protocol.md when ready."
        fi
    fi
else
    cp "$SCRIPT_DIR/templates/CLAUDE-protocol.md" "$CLAUDE_MD"
    echo "  Created CLAUDE.md with retrieval protocol"
fi

# --- Install cross-session sync hook ---
echo ""
echo "  Installing cross-session memory sync hook..."

# Auto-detect Python
PYTHON=""
for cmd in python3 python py /c/Python314/python /c/Python312/python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -n "$PYTHON" ]; then
    $PYTHON "$SCRIPT_DIR/install_hooks.py"
else
    echo "  WARNING: Python not found. Skipping hook installation."
    echo "  Run 'python install_hooks.py' manually when Python is available."
fi

# --- Install semantic search (required — core to retrieval quality) ---
echo ""
if [ -z "$PYTHON" ]; then
    echo "  ERROR: Python not found. Semantic search is required and cannot be installed."
    exit 1
elif $PYTHON -c "import sentence_transformers" 2>/dev/null; then
    echo "  Semantic search already installed"
else
    echo "  Installing semantic search (sentence-transformers + torch, a few minutes)..."
    $PYTHON -m pip install sentence-transformers -q 2>&1 | tail -1
    echo "  Semantic search installed"
fi

# --- Pre-warm the embedding model so the first query is instant ---
echo "  Pre-warming embedding model (one-time cache, ~1 min)..."
$PYTHON -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2').encode('warmup')" 2>&1 | tail -1 || true
echo "  Model cached. First semantic query will be instant."

# --- Install `kontext` terminal command ---
echo ""
if [ -z "$PYTHON" ]; then
    echo "  Skipping 'kontext' CLI install (Python not found)."
else
    echo "  Installing 'kontext' terminal command..."
    (cd "$SCRIPT_DIR" && $PYTHON -m pip install -e . -q 2>&1 | tail -3) || true
    if command -v kontext &>/dev/null; then
        echo "  'kontext' installed. Type 'kontext' in a terminal to open the dashboard."
    else
        echo "  'kontext' command installed, but not on PATH."
        echo "  You may need to add your user-scripts dir to PATH (pip will print its location)."
        echo "  Fallback: run 'python -m cloud.server' from the project directory."
    fi
fi

# --- Install /kontext skill ---
SKILL_DIR="$CLAUDE_DIR/skills/kontext"
mkdir -p "$SKILL_DIR"
if [ ! -f "$SKILL_DIR/SKILL.md" ] || [ "$SCRIPT_DIR/SKILL.md" -nt "$SKILL_DIR/SKILL.md" ]; then
    cp "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR/SKILL.md"
    echo "  /kontext skill installed at $SKILL_DIR"
else
    echo "  /kontext skill already up to date"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Kontext is ready. Your memory library is at:"
echo "  $MEMORY_DIR"
echo ""
echo "Next steps:"
echo "  1. Open Claude Code in any project directory"
echo "  2. Claude will automatically read your memory files before responding"
echo "  3. Tell Claude about yourself — it will save to memory files"
echo "  4. Over time, Claude gets better at working with you"
echo ""
echo "To add a new memory file:"
echo "  Create a .md file in $MEMORY_DIR with frontmatter (name, description, type)"
echo "  Add a one-line entry to MEMORY.md"
echo ""
