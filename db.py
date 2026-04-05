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
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_entries_file ON entries(file);
            CREATE INDEX IF NOT EXISTS idx_entries_tier ON entries(tier);
            CREATE INDEX IF NOT EXISTS idx_entries_grade ON entries(grade);
            CREATE INDEX IF NOT EXISTS idx_entries_fact ON entries(fact);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_a ON relations(entity_a);
            CREATE INDEX IF NOT EXISTS idx_relations_entity_b ON relations(entity_b);
        """)
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

    def save_session(self, project: str = "", status: str = "", next_step: str = "", key_decisions: str = ""):
        self._execute(
            "INSERT INTO sessions (project, status, next_step, key_decisions) VALUES (?, ?, ?, ?)",
            (project, status, next_step, key_decisions)
        )

    def get_latest_session(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

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
        """Public SQL execute -- use for bulk operations like DELETE FROM."""
        return self.conn.execute(sql, params)

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
        """Find potential contradictions: entries in the same file with overlapping keywords but different values."""
        sql = "SELECT id, file, fact FROM entries WHERE tier != 'cold'"
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

        noise_words = {"the", "and", "for", "with", "from", "that", "this", "was", "are", "has", "have", "been"}

        for file_name, file_entries in by_file.items():
            for i, e1 in enumerate(file_entries):
                words_1 = {w.lower() for w in e1[2].split() if len(w) >= 4 and w.lower() not in noise_words}
                for e2 in file_entries[i + 1:]:
                    pair_key = (min(e1[0], e2[0]), max(e1[0], e2[0]))
                    if pair_key in seen_pairs:
                        continue
                    words_2 = {w.lower() for w in e2[2].split() if len(w) >= 4 and w.lower() not in noise_words}
                    shared = words_1 & words_2
                    if len(shared) >= 2 and e1[2] != e2[2]:
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
