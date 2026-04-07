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


class KontextDB:
    """SQLite-backed memory database."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent / "kontext.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
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
            CREATE INDEX IF NOT EXISTS idx_entries_fact ON entries(fact);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_a ON relations(entity_a);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_b ON relations(entity_b);
        """)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """Add columns that may not exist in older databases."""
        cursor = self.conn.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cursor.fetchall()}
        for col in ("summary", "files_touched"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT ''")
        self.conn.commit()

    def _execute(self, sql, params=()):
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor

    def list_tables(self):
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cursor.fetchall()]

    # --- Entries ---

    def add_entry(self, file: str, fact: str, source: str = "", grade: float = 5, tier: str = "active") -> int:
        """Add an entry. Skips if exact duplicate (same file + fact) exists."""
        existing = self.conn.execute(
            "SELECT id FROM entries WHERE file = ? AND fact = ?", (file, fact)
        ).fetchone()
        if existing:
            return existing[0]

        cursor = self._execute(
            "INSERT INTO entries (file, fact, source, grade, tier) VALUES (?, ?, ?, ?, ?)",
            (file, fact, source, grade, tier)
        )
        return cursor.lastrowid

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

    def get_entry(self, entry_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row:
            self._execute("UPDATE entries SET last_accessed = datetime('now') WHERE id = ?", (entry_id,))
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

    def search_entries(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search on fact content."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM entries WHERE fact LIKE ? ORDER BY grade DESC LIMIT ?",
            (f"%{query}%", limit)
        ).fetchall()]

    def delete_entry(self, entry_id: int):
        self._execute("DELETE FROM entries WHERE id = ?", (entry_id,))

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
        """Add a relation. Skips if exact duplicate exists."""
        existing = self.conn.execute(
            "SELECT id FROM relations WHERE entity_a = ? AND relation = ? AND entity_b = ?",
            (entity_a, relation, entity_b)
        ).fetchone()
        if existing:
            return existing[0]
        cursor = self._execute(
            "INSERT INTO relations (entity_a, relation, entity_b, confidence, source) VALUES (?, ?, ?, ?, ?)",
            (entity_a, relation, entity_b, confidence, source)
        )
        return cursor.lastrowid

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
        cursor = self._execute(
            "INSERT INTO conflicts (file, entry_a, entry_b) VALUES (?, ?, ?)",
            (file, entry_a, entry_b)
        )
        return cursor.lastrowid

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
                    # Conflict signal: shared value words AND a numeric token
                    # in either entry that differs from the other (quantitative drift).
                    # ≥2 shared words with numeric drift is enough (e.g. "Active students: 27"
                    # vs "Active students: 24"). ≥3 shared words without drift also qualifies.
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


    # --- Embeddings ---

    def store_embedding(self, entry_id: int, embedding: list[float]):
        """Store an embedding vector as a BLOB (packed floats)."""
        blob = struct.pack(f'{len(embedding)}f', *embedding)
        self._execute("UPDATE entries SET embedding = ? WHERE id = ?", (blob, entry_id))

    def get_embedding(self, entry_id: int) -> list[float] | None:
        """Retrieve an embedding vector from BLOB storage."""
        row = self.conn.execute("SELECT embedding FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row and row[0]:
            blob = row[0]
            count = len(blob) // 4  # 4 bytes per float
            return list(struct.unpack(f'{count}f', blob))
        return None

    def semantic_search(self, query_embedding: list[float], limit: int = 10, min_grade: float = 0) -> list[dict]:
        """Find entries most similar to query_embedding using cosine similarity."""
        import math

        query_mag = math.sqrt(sum(x * x for x in query_embedding))
        if query_mag == 0:
            return []

        rows = self.conn.execute(
            "SELECT id, file, fact, source, grade, tier, embedding FROM entries WHERE embedding IS NOT NULL AND grade >= ?",
            (min_grade,)
        ).fetchall()

        results = []
        for row in rows:
            blob = row[6]
            count = len(blob) // 4
            entry_vec = struct.unpack(f'{count}f', blob)

            dot = sum(a * b for a, b in zip(query_embedding, entry_vec))
            entry_mag = math.sqrt(sum(x * x for x in entry_vec))
            if entry_mag == 0:
                continue
            similarity = dot / (query_mag * entry_mag)

            results.append({
                "id": row[0], "file": row[1], "fact": row[2],
                "source": row[3], "grade": row[4], "tier": row[5],
                "score": similarity,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.conn.close()
