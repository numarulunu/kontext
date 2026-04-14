# db.py
"""
Kontext Database -- SQLite backend for memory storage.

Single source of truth for all memory entries, relations, conflicts, and session state.
Flat markdown files are generated FROM this database, not the other way around.
"""

import sqlite3
import os
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
import struct


# --- Schema migrations ---
#
# Each migration is (version, callable(conn)). Migrations are run in order,
# only if their version > current schema_version. Bodies must be idempotent
# (safe to re-run on a fresh DB) — the version gate is the primary guard but
# IF NOT EXISTS / IF EXISTS clauses are belt-and-suspenders.

def _migration_1_session_summary_cols(conn):
    """Add summary + files_touched columns to sessions (older schemas lacked them)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    for col in ("summary", "files_touched"):
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT ''")


def _migration_2_dedup_and_unique_indexes(conn):
    """Dedup duplicate rows then install UNIQUE indexes for race-safe inserts."""
    conn.executescript("""
        DELETE FROM entries WHERE id NOT IN (
            SELECT MIN(id) FROM entries GROUP BY file, fact
        );
        DELETE FROM relations WHERE id NOT IN (
            SELECT MIN(id) FROM relations GROUP BY entity_a, relation, entity_b
        );
        DELETE FROM conflicts WHERE id NOT IN (
            SELECT MIN(id) FROM conflicts GROUP BY file, entry_a, entry_b
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_unique
            ON entries(file, fact);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique
            ON relations(entity_a, relation, entity_b);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conflicts_unique
            ON conflicts(file, entry_a, entry_b);
    """)


def _has_fts5(conn) -> bool:
    """Detect FTS5 support in the running SQLite build."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except Exception:
        return False


def _migration_3_fts5_entries(conn):
    """Build a trigram-tokenized FTS5 index over entries.fact + sync triggers.

    Skipped silently if the SQLite build lacks FTS5 — search_entries falls back
    to LIKE in that case.
    """
    if not _has_fts5(conn):
        return
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            fact,
            content='entries',
            content_rowid='id',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, fact) VALUES (new.id, new.fact);
        END;
        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, fact) VALUES('delete', old.id, old.fact);
        END;
        CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, fact) VALUES('delete', old.id, old.fact);
            INSERT INTO entries_fts(rowid, fact) VALUES (new.id, new.fact);
        END;
    """)
    # Backfill from existing rows (the contentless 'rebuild' command handles this)
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")


def _migration_4_access_count(conn):
    """Add access_count to entries for usage-driven ranking."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "access_count" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN access_count INTEGER DEFAULT 0")


def _migration_5_tool_events(conn):
    """Capture PostToolUse events for session intelligence."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tool_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            tool_name TEXT NOT NULL,
            summary TEXT NOT NULL,
            file_path TEXT DEFAULT NULL,
            grade REAL DEFAULT 5.0,
            promoted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tool_events_session ON tool_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_tool_events_created ON tool_events(created_at);
    """)


def _migration_6_user_prompts(conn):
    """User prompt history with FTS5 for searchable session context."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_prompts_session ON user_prompts(session_id);
        CREATE INDEX IF NOT EXISTS idx_user_prompts_created ON user_prompts(created_at);
    """)
    if not _has_fts5(conn):
        return
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS user_prompts_fts USING fts5(
            content,
            content='user_prompts',
            content_rowid='id',
            tokenize='trigram'
        );
        CREATE TRIGGER IF NOT EXISTS prompts_ai AFTER INSERT ON user_prompts BEGIN
            INSERT INTO user_prompts_fts(rowid, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS prompts_ad AFTER DELETE ON user_prompts BEGIN
            INSERT INTO user_prompts_fts(user_prompts_fts, rowid, content)
                VALUES('delete', old.id, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS prompts_au AFTER UPDATE ON user_prompts BEGIN
            INSERT INTO user_prompts_fts(user_prompts_fts, rowid, content)
                VALUES('delete', old.id, old.content);
            INSERT INTO user_prompts_fts(rowid, content) VALUES(new.id, new.content);
        END;
    """)
    conn.execute("INSERT INTO user_prompts_fts(user_prompts_fts) VALUES('rebuild')")


def _migration_7_session_intelligence(conn):
    """Add investigated + learned columns to sessions for richer auto-summaries."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    for col in ("investigated", "learned"):
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT ''")


MIGRATIONS = [
    (1, _migration_1_session_summary_cols),
    (2, _migration_2_dedup_and_unique_indexes),
    (3, _migration_3_fts5_entries),
    (4, _migration_4_access_count),
    (5, _migration_5_tool_events),
    (6, _migration_6_user_prompts),
    (7, _migration_7_session_intelligence),
]
LATEST_SCHEMA_VERSION = max(v for v, _ in MIGRATIONS)


class _Transaction:
    """Context manager for SQLite transactions with rollback on failure.

    Also sets db._in_batch so nested _execute() calls skip their auto-commit —
    this lets phases/methods that call _execute() compose into one atomic unit.
    """

    def __init__(self, db):
        self.db = db
        self.conn = db.conn
        self._prev_batch = False

    def __enter__(self):
        self._prev_batch = self.db._in_batch
        self.db._in_batch = True
        # Only open a real transaction at the outermost level.
        if not self._prev_batch:
            self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db._in_batch = self._prev_batch
        if self._prev_batch:
            # Nested — let the outer scope decide commit/rollback.
            return False
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False  # re-raise exceptions


class KontextDB:
    """SQLite-backed memory database."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent / "kontext.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")  # 2-10x faster writes; WAL durability retained
        self.conn.execute("PRAGMA cache_size=-16000")   # 16 MB page cache
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._in_batch = False  # True inside a transaction() context — suppresses _execute auto-commits
        self._embed_cache = None  # Lazy-built dict: {id: (file, fact, source, grade, tier, vec_tuple)}
        self._fts_enabled = False  # set after _create_tables → _migrate runs
        self._create_tables()
        self._fts_enabled = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries_fts'"
        ).fetchone() is not None

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0);

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                fact TEXT NOT NULL,
                source TEXT DEFAULT '',
                grade REAL DEFAULT 5,
                tier TEXT DEFAULT 'active' CHECK(tier IN ('active', 'historical', 'cold')),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                last_accessed TEXT DEFAULT (datetime('now')),
                embedding BLOB DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_a TEXT NOT NULL,
                relation TEXT NOT NULL,
                entity_b TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                entry_a TEXT NOT NULL,
                entry_b TEXT NOT NULL,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'resolved')),
                resolution TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT DEFAULT '',
                status TEXT DEFAULT '',
                next_step TEXT DEFAULT '',
                key_decisions TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                files_touched TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS file_meta (
                filename TEXT PRIMARY KEY,
                file_type TEXT DEFAULT 'user',
                description TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_entries_file ON entries(file);
            CREATE INDEX IF NOT EXISTS idx_entries_tier ON entries(tier);
            CREATE INDEX IF NOT EXISTS idx_entries_grade ON entries(grade);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_a ON relations(entity_a);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_b ON relations(entity_b);
        """)
        # Drop the useless leading-wildcard-LIKE index if it exists from older schemas.
        self.conn.execute("DROP INDEX IF EXISTS idx_entries_fact")
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """Run any pending migrations from MIGRATIONS, in order, idempotently.

        Each migration is wrapped in a transaction and bumps schema_version on
        success. Already-applied migrations are skipped via the version check.
        """
        # Legacy DBs (pre-framework) won't have schema_version — create it.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL DEFAULT 0
            )
        """)
        self.conn.execute("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)")
        self.conn.commit()

        current = self.conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        for version, fn in MIGRATIONS:
            if version <= current:
                continue
            with self.transaction() as conn:
                fn(conn)
                conn.execute("UPDATE schema_version SET version = ? WHERE id = 1", (version,))
            current = version

    def _execute(self, sql, params=()):
        cursor = self.conn.execute(sql, params)
        if not self._in_batch:
            self.conn.commit()
        return cursor

    def transaction(self):
        """Context manager for explicit transactions. Use for multi-step operations.

        Inside the block, any call to self._execute() defers its commit — the
        whole block commits atomically on success and rolls back on exception.
        Nested transactions compose (inner ones defer to the outermost).

        Usage:
            with db.transaction():
                db.add_entry(...)   # normally auto-commits; now deferred
                db.add_entry(...)
            # single commit on success
        """
        return _Transaction(self)

    def list_tables(self):
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cursor.fetchall()]

    # --- Entries ---

    def add_entry(self, file: str, fact: str, source: str = "", grade: float = 5, tier: str = "active") -> int:
        """Add an entry. Race-safe via UNIQUE(file, fact) index + INSERT OR IGNORE."""
        self._execute(
            "INSERT OR IGNORE INTO entries (file, fact, source, grade, tier) VALUES (?, ?, ?, ?, ?)",
            (file, fact, source, grade, tier)
        )
        row = self.conn.execute(
            "SELECT id FROM entries WHERE file = ? AND fact = ?", (file, fact)
        ).fetchone()
        return row[0] if row else 0

    def update_entry(self, entry_id: int, **kwargs):
        """Update specific fields of an entry."""
        allowed = {"fact", "source", "grade", "tier", "file", "embedding"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [entry_id]
        self._execute(f"UPDATE entries SET {set_clause} WHERE id = ?", values)
        if "fact" in updates or "file" in updates:
            self._embed_cache = None  # fact text changed → cached copy stale

    def get_entry(self, entry_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row:
            # Write amplification fix: only bump last_accessed if stale >1 hour.
            # last_accessed is only consumed by decay/purge, so sub-hour precision is worthless.
            prev = row["last_accessed"]
            stale = (
                not prev
                or self.conn.execute(
                    "SELECT datetime('now', '-1 hour') > ?", (prev,)
                ).fetchone()[0] == 1
            )
            if stale:
                self._execute(
                    "UPDATE entries SET last_accessed = datetime('now') WHERE id = ?",
                    (entry_id,),
                )
            return dict(row)
        return None

    def get_entries(self, file: str = None, tier: str = None, min_grade: float = None) -> list[dict]:
        sql = "SELECT * FROM entries WHERE 1=1"
        params = []
        if file:
            sql += " AND file = ?"
            params.append(file)
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        if min_grade is not None:
            sql += " AND grade >= ?"
            params.append(min_grade)
        sql += " ORDER BY grade DESC, updated_at DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def search_entries(self, query: str, limit: int = 20,
                       file: str = None, tier: str = None,
                       min_grade: float = None) -> list[dict]:
        """Substring search on fact content with optional filters.

        Uses FTS5 trigram index when available (O(log n) on indexed prefixes
        of 3+ chars), falling back to escaped LIKE on older SQLite builds.
        """
        if self._fts_enabled and query and query.strip():
            # Wrap as a quoted phrase so FTS5 operators (", *, :, (, ), -, AND, OR)
            # in user input are treated as literal trigrams. Escape embedded
            # quotes by doubling them per FTS5 syntax.
            phrase = '"' + query.replace('"', '""') + '"'
            sql = (
                "SELECT e.* FROM entries e "
                "JOIN entries_fts f ON e.id = f.rowid "
                "WHERE f.fact MATCH ?"
            )
            params: list = [phrase]
            if file:
                sql += " AND e.file = ?"
                params.append(file)
            if tier:
                sql += " AND e.tier = ?"
                params.append(tier)
            if min_grade is not None:
                sql += " AND e.grade >= ?"
                params.append(min_grade)
            sql += " ORDER BY e.grade DESC LIMIT ?"
            params.append(limit)
            try:
                return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
            except sqlite3.OperationalError:
                pass  # Trigram <3 chars or other FTS edge case → fall through to LIKE

        safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = "SELECT * FROM entries WHERE fact LIKE ? ESCAPE '\\'"
        params = [f"%{safe_query}%"]
        if file:
            sql += " AND file = ?"
            params.append(file)
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        if min_grade is not None:
            sql += " AND grade >= ?"
            params.append(min_grade)
        sql += " ORDER BY grade DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def delete_entry(self, entry_id: int):
        self._execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._embed_cache = None  # invalidate

    def list_files(self) -> dict:
        """Return dict of {filename: entry_count}."""
        rows = self.conn.execute(
            "SELECT file, COUNT(*) as cnt FROM entries GROUP BY file ORDER BY cnt DESC"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_recent_changes(self, hours: int = 24) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM entries WHERE updated_at >= datetime('now', ? || ' hours') ORDER BY updated_at DESC",
            (f"-{int(hours)}",)
        ).fetchall()]

    def decay_scores(self, days_threshold: int = 60, decay_amount: float = 0.5):
        """Reduce grade of entries not accessed in days_threshold days. Minimum grade: 1."""
        self._execute("""
            UPDATE entries SET
                grade = MAX(1, grade - ?),
                tier = CASE
                    WHEN MAX(1, grade - ?) < 5 THEN 'cold'
                    WHEN MAX(1, grade - ?) < 8 THEN 'historical'
                    ELSE tier
                END
            WHERE last_accessed < datetime('now', ? || ' days')
            AND grade > 1
        """, (decay_amount, decay_amount, decay_amount, f"-{int(days_threshold)}"))

    # --- Sessions ---

    def save_session(self, project: str = "", status: str = "", next_step: str = "",
                     key_decisions: str = "", summary: str = "", files_touched: str = ""):
        self._execute(
            "INSERT INTO sessions (project, status, next_step, key_decisions, summary, files_touched) VALUES (?, ?, ?, ?, ?, ?)",
            (project, status, next_step, key_decisions, summary, files_touched)
        )
        self._export_last_session(project, status, next_step, key_decisions, summary, files_touched)

    def _export_last_session(self, project: str, status: str, next_step: str,
                             key_decisions: str, summary: str = "", files_touched: str = ""):
        """Write _last_session.md so SessionStart shell hooks can read it without MCP."""
        # Only export when using the production database — test DBs must not
        # overwrite real session state (the cause of the "Project 9" bug).
        prod_db = str(Path(__file__).parent / "kontext.db")
        if os.path.abspath(self.db_path) != os.path.abspath(prod_db):
            return
        target = Path.home() / ".claude" / "_last_session.md"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        parts = [
            f"---\ndate: {today}",
            f"project: {project}",
            f"status: {status}",
            f"next: {next_step}",
            f"key_decisions: {key_decisions}",
        ]
        if summary:
            parts.append(f"summary: {summary}")
        if files_touched:
            parts.append(f"files_touched: {files_touched}")
        parts.append("---\n")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            pass  # Non-critical — DB is the source of truth

    def get_latest_session(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def purge_old_sessions(self, keep: int = 20):
        """Delete all but the most recent `keep` sessions."""
        self._execute("""
            DELETE FROM sessions WHERE id NOT IN (
                SELECT id FROM sessions ORDER BY id DESC LIMIT ?
            )
        """, (keep,))

    # --- File Metadata ---

    def set_file_meta(self, filename: str, file_type: str = "user", description: str = ""):
        self._execute("""
            INSERT INTO file_meta (filename, file_type, description, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(filename) DO UPDATE SET
                file_type = excluded.file_type,
                description = excluded.description,
                updated_at = datetime('now')
        """, (filename, file_type, description))

    def get_file_meta(self, filename: str) -> dict:
        row = self.conn.execute(
            "SELECT filename, file_type, description FROM file_meta WHERE filename = ?",
            (filename,)
        ).fetchone()
        if row:
            return {"filename": row[0], "file_type": row[1], "description": row[2]}
        return {"filename": filename, "file_type": "user", "description": ""}

    def get_all_file_meta(self) -> dict:
        rows = self.conn.execute("SELECT filename, file_type, description FROM file_meta").fetchall()
        return {r[0]: {"file_type": r[1], "description": r[2]} for r in rows}

    def migrate_file_meta(self, file_meta_dict: dict):
        """Bulk import from a dict of {filename: (file_type, description)}."""
        for filename, (file_type, description) in file_meta_dict.items():
            self.set_file_meta(filename, file_type, description)

    # --- Relations (knowledge graph) ---

    def add_relation(self, entity_a: str, relation: str, entity_b: str, confidence: float = 1.0, source: str = ""):
        """Add a relation. Race-safe via UNIQUE(entity_a, relation, entity_b) + INSERT OR IGNORE."""
        self._execute(
            "INSERT OR IGNORE INTO relations (entity_a, relation, entity_b, confidence, source) VALUES (?, ?, ?, ?, ?)",
            (entity_a, relation, entity_b, confidence, source)
        )
        row = self.conn.execute(
            "SELECT id FROM relations WHERE entity_a = ? AND relation = ? AND entity_b = ?",
            (entity_a, relation, entity_b)
        ).fetchone()
        return row[0] if row else 0

    def get_relations(self, entity: str) -> list[dict]:
        """Get all relations where entity is either subject or object."""
        rows = self.conn.execute(
            "SELECT * FROM relations WHERE entity_a = ? OR entity_b = ? ORDER BY confidence DESC",
            (entity, entity)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_relations(self) -> list[dict]:
        """Get every relation in the graph."""
        rows = self.conn.execute("SELECT * FROM relations").fetchall()
        return [dict(r) for r in rows]

    def delete_relation(self, relation_id: int):
        """Delete a single relation by ID."""
        self._execute("DELETE FROM relations WHERE id = ?", (relation_id,))

    def execute(self, sql: str, params=()):
        """Public SQL execute with auto-commit. Use for bulk operations like DELETE FROM.

        Note: this commits every call. For non-committing access, use db.conn.execute directly.
        """
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor

    def query_graph(self, entity: str, depth: int = 2) -> list[dict]:
        """Traverse the knowledge graph up to depth hops from an entity."""
        visited = set()
        results = []
        queue = deque([(entity, 0)])

        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)

            rels = self.get_relations(current)
            for r in rels:
                results.append(r)
                other = r["entity_b"] if r["entity_a"] == current else r["entity_a"]
                if other not in visited:
                    queue.append((other, d + 1))

        return results

    # --- Conflicts ---

    def add_conflict(self, file: str, entry_a: str, entry_b: str) -> int:
        """Race-safe via UNIQUE(file, entry_a, entry_b) + INSERT OR IGNORE."""
        self._execute(
            "INSERT OR IGNORE INTO conflicts (file, entry_a, entry_b) VALUES (?, ?, ?)",
            (file, entry_a, entry_b)
        )
        row = self.conn.execute(
            "SELECT id FROM conflicts WHERE file = ? AND entry_a = ? AND entry_b = ?",
            (file, entry_a, entry_b)
        ).fetchone()
        return row[0] if row else 0

    def get_pending_conflicts(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM conflicts WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()]

    def detect_conflicts(self, file: str = None) -> list[dict]:
        """Find potential contradictions among active-tier entries.

        TEMPORAL-AWARE: if BOTH entries in a pair carry dated sources
        (e.g. '[Claude 2026-03]' and '[Claude 2026-04]'), they represent
        timeline evolution, not contradiction — skip the pair.
        Historical-tier entries are excluded by the query itself.
        """
        import re

        # Only consider ACTIVE entries — historical ones are already resolved evolution
        sql = "SELECT id, file, fact, source, tier FROM entries WHERE tier = 'active'"
        params = []
        if file:
            sql += " AND file = ?"
            params.append(file)
        sql += " ORDER BY file, id"

        entries = self.conn.execute(sql, params).fetchall()
        conflicts = []
        seen_pairs = set()

        by_file = {}
        for e in entries:
            by_file.setdefault(e[1], []).append(e)

        # Stopwords + common entity nouns that shouldn't trigger on their own
        noise_words = {
            "the", "and", "for", "with", "from", "that", "this", "was", "are",
            "has", "have", "been", "user", "ionut", "ionuț", "claude", "vocality",
            "this", "that", "with", "into", "over", "than", "then", "when", "what",
            "which", "they", "them", "their", "there", "would", "could", "should",
        }
        date_re = re.compile(r"\[[^\]]*\d{4}[^\]]*\]")  # matches [Claude 2026-04] etc.

        def is_dated(source: str, fact: str) -> bool:
            return bool(date_re.search(source or "") or date_re.search(fact or ""))

        def value_words(text: str) -> set:
            # Keep tokens that look like values: numbers, prices, %s, or 5+ char words
            out = set()
            for raw in text.split():
                w = raw.strip(".,;:()[]\"'").lower()
                if not w or w in noise_words:
                    continue
                if any(c.isdigit() for c in w):
                    out.add(w)
                elif len(w) >= 5:
                    out.add(w)
            return out

        # Wrap the whole scan+insert in a single transaction: each add_conflict()
        # otherwise auto-commits per row, producing O(n) commits and a large
        # write-lock window. UNIQUE(file, entry_a, entry_b) + INSERT OR IGNORE
        # handles re-detection idempotently.
        with self.transaction():
            for file_name, file_entries in by_file.items():
                for i, e1 in enumerate(file_entries):
                    words_1 = value_words(e1[2])
                    if len(words_1) < 2:
                        continue
                    e1_dated = is_dated(e1[3], e1[2])
                    for e2 in file_entries[i + 1:]:
                        if e1[2] == e2[2]:
                            continue
                        # Both dated → timeline evolution, not a conflict
                        if e1_dated and is_dated(e2[3], e2[2]):
                            continue
                        pair_key = (min(e1[0], e2[0]), max(e1[0], e2[0]))
                        if pair_key in seen_pairs:
                            continue
                        words_2 = value_words(e2[2])
                        shared = words_1 & words_2
                        nums_1 = {w for w in words_1 if any(c.isdigit() for c in w)}
                        nums_2 = {w for w in words_2 if any(c.isdigit() for c in w)}
                        numeric_drift = bool(
                            (nums_1 or nums_2) and (nums_1 ^ nums_2)
                        )
                        if (len(shared) >= 2 and numeric_drift) or len(shared) >= 3:
                            seen_pairs.add(pair_key)
                            self.add_conflict(file=file_name, entry_a=e1[2], entry_b=e2[2])
                            conflicts.append({
                                "file": file_name,
                                "entry_a": e1[2],
                                "entry_b": e2[2],
                                "entry_a_id": e1[0],
                                "entry_b_id": e2[0],
                                "shared_words": sorted(shared),
                            })
        return conflicts

    def resolve_conflict(self, conflict_id: int, resolution: str):
        self._execute(
            "UPDATE conflicts SET status = 'resolved', resolution = ?, resolved_at = datetime('now') WHERE id = ?",
            (resolution, conflict_id)
        )


    def get_entry_by_fact(self, file: str, fact: str) -> dict | None:
        """Exact-match lookup by (file, fact). Used by dream.phase_resolve to avoid
        LIKE wildcards and the O(n) post-filter in search_entries."""
        row = self.conn.execute(
            "SELECT * FROM entries WHERE file = ? AND fact = ?", (file, fact)
        ).fetchone()
        return dict(row) if row else None

    # --- Embeddings ---

    def store_embedding(self, entry_id: int, embedding: list[float]):
        """Store an embedding vector as a BLOB (packed floats)."""
        blob = struct.pack(f'{len(embedding)}f', *embedding)
        self._execute("UPDATE entries SET embedding = ? WHERE id = ?", (blob, entry_id))
        # Invalidate the semantic cache — the vector for this id changed.
        self._embed_cache = None

    def get_embedding(self, entry_id: int) -> list[float] | None:
        """Retrieve an embedding vector from BLOB storage."""
        row = self.conn.execute("SELECT embedding FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row and row[0]:
            blob = row[0]
            if len(blob) % 4 != 0 or len(blob) == 0:
                return None  # Malformed BLOB — skip instead of crash
            count = len(blob) // 4
            return list(struct.unpack(f'{count}f', blob))
        return None

    def _ensure_embed_cache(self):
        """Build the deserialized embedding cache if not already present."""
        if self._embed_cache is not None:
            return
        import math
        cache = {}
        rows = self.conn.execute(
            "SELECT id, file, fact, source, grade, tier, embedding FROM entries "
            "WHERE embedding IS NOT NULL"
        ).fetchall()
        for row in rows:
            blob = row[6]
            if not blob or len(blob) % 4 != 0:
                continue
            count = len(blob) // 4
            vec = struct.unpack(f'{count}f', blob)
            mag = math.sqrt(sum(x * x for x in vec))
            if mag == 0:
                continue
            cache[row[0]] = {
                "file": row[1], "fact": row[2], "source": row[3],
                "grade": row[4], "tier": row[5],
                "vec": vec, "mag": mag,
            }
        self._embed_cache = cache

    def semantic_search(self, query_embedding: list[float], limit: int = 10,
                        min_grade: float = 0, file: str = None) -> list[dict]:
        """Find entries most similar to query_embedding using cosine similarity.

        Uses an in-process cache of deserialized vectors — avoids hitting disk
        and re-unpacking every BLOB on every call. Cache is invalidated on
        store_embedding() and rebuilt lazily on the next query.
        """
        import math

        query_mag = math.sqrt(sum(x * x for x in query_embedding))
        if query_mag == 0:
            return []

        self._ensure_embed_cache()
        results = []
        for entry_id, e in self._embed_cache.items():
            if e["grade"] < min_grade:
                continue
            if file and e["file"] != file:
                continue
            dot = sum(a * b for a, b in zip(query_embedding, e["vec"]))
            similarity = dot / (query_mag * e["mag"])
            results.append({
                "id": entry_id, "file": e["file"], "fact": e["fact"],
                "source": e["source"], "grade": e["grade"], "tier": e["tier"],
                "score": similarity,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # -------------------------------------------------------------------------
    # Tool events
    # -------------------------------------------------------------------------

    def add_tool_event(self, session_id: str, tool_name: str, summary: str,
                       file_path: str = None, grade: float = 5.0) -> int:
        """Record a PostToolUse event. Returns the new row id."""
        cursor = self._execute(
            "INSERT INTO tool_events (session_id, tool_name, summary, file_path, grade)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id or "", tool_name, summary[:500], file_path, grade),
        )
        return cursor.lastrowid

    def get_tool_events(self, session_id: str = None, promoted: bool = False,
                        since_hours: float = None, limit: int = 100) -> list[dict]:
        """Fetch tool events. Filters: session_id, promoted flag, time window."""
        conditions = ["promoted = ?"]
        params: list = [1 if promoted else 0]
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if since_hours is not None:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f"-{since_hours} hours")
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT * FROM tool_events WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def promote_tool_event(self, event_id: int, file: str, fact: str) -> None:
        """Promote a tool_event into a permanent entries row and mark it promoted."""
        event = self.conn.execute(
            "SELECT * FROM tool_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            raise ValueError(f"tool_event {event_id} not found")
        with self.transaction():
            self.add_entry(file=file, fact=fact, source="[tool_event]",
                           grade=event["grade"], tier="active")
            self._execute(
                "UPDATE tool_events SET promoted = 1 WHERE id = ?", (event_id,)
            )

    # -------------------------------------------------------------------------
    # User prompts
    # -------------------------------------------------------------------------

    def add_user_prompt(self, session_id: str, content: str) -> int:
        """Log a user prompt. Content is hard-capped at 2000 chars. Returns row id."""
        if len(content) > 2000:
            content = content[:2000]
        cursor = self._execute(
            "INSERT INTO user_prompts (session_id, content) VALUES (?, ?)",
            (session_id or "", content),
        )
        return cursor.lastrowid

    def search_prompts(self, query: str = "", limit: int = 20,
                       hours: float = None) -> list[dict]:
        """FTS5 (or LIKE fallback) search over user_prompts. Optionally restrict to last N hours."""
        params: list = []
        time_filter = ""        # used by LIKE and unconditional branches (no JOIN)
        fts_time_filter = ""    # used by FTS branch (has JOIN, needs table qualifier)
        if hours is not None:
            time_filter = "AND created_at >= datetime('now', ?)"
            fts_time_filter = "AND up.created_at >= datetime('now', ?)"
            params.append(f"-{hours} hours")

        fts_available = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_prompts_fts'"
        ).fetchone()

        if fts_available and query:
            safe_q = '"' + query.replace('"', '""') + '"'
            rows = self.conn.execute(
                f"SELECT up.* FROM user_prompts up"
                f" JOIN user_prompts_fts fts ON fts.rowid = up.id"
                f" WHERE user_prompts_fts MATCH ? {fts_time_filter}"
                f" ORDER BY up.created_at DESC LIMIT ?",
                [safe_q] + params + [limit],
            ).fetchall()
        elif query:
            rows = self.conn.execute(
                f"SELECT * FROM user_prompts WHERE content LIKE ? {time_filter}"
                f" ORDER BY created_at DESC LIMIT ?",
                [f"%{query}%"] + params + [limit],
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM user_prompts WHERE 1=1 {time_filter}"
                f" ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_prompts(self, hours: float = 24, limit: int = 50) -> list[dict]:
        """Return the most recent user prompts within the last N hours."""
        rows = self.conn.execute(
            "SELECT * FROM user_prompts"
            " WHERE created_at >= datetime('now', ?)"
            " ORDER BY created_at DESC LIMIT ?",
            (f"-{hours} hours", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Access count
    # -------------------------------------------------------------------------

    def bump_access_count(self, entry_id: int) -> None:
        """Increment access_count and refresh last_accessed for a single entry."""
        self._execute(
            "UPDATE entries SET access_count = access_count + 1,"
            " last_accessed = datetime('now') WHERE id = ?",
            (entry_id,),
        )

    # -------------------------------------------------------------------------
    # File stats (for progressive disclosure)
    # -------------------------------------------------------------------------

    def get_file_stats(self) -> dict[str, dict]:
        """Return per-file aggregates: fact_count, top_grade, access_sum, top_fact preview."""
        rows = self.conn.execute("""
            SELECT
                e1.file,
                COUNT(*) AS fact_count,
                MAX(e1.grade) AS top_grade,
                COALESCE(SUM(e1.access_count), 0) AS access_sum,
                (SELECT fact FROM entries e2
                 WHERE e2.file = e1.file
                 ORDER BY e2.grade DESC, e2.access_count DESC
                 LIMIT 1) AS top_fact
            FROM entries e1
            GROUP BY e1.file
        """).fetchall()
        return {row["file"]: dict(row) for row in rows}

    # -------------------------------------------------------------------------
    # Session utilities
    # -------------------------------------------------------------------------

    def get_latest_session_id(self) -> int | None:
        """Return the integer primary key of the most recently created session row."""
        row = self.conn.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.conn.close()
