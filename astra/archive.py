"""SQLite store for memories the user explicitly chose to retain."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class Archive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self):
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.commit()
        finally:
            db.close()

    def list_memories(self, limit=40):
        db = self._connect()
        try:
            rows = db.execute("SELECT id, note, created_at FROM memories ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def remember(self, note: str):
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
            raise ValueError("Memory must contain 1–1000 characters")
        db = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = db.execute("INSERT INTO memories (note, created_at) VALUES (?, ?)", (note.strip(), now))
            db.commit()
            return {"id": cursor.lastrowid, "note": note.strip(), "created_at": now}
        finally:
            db.close()

    def forget(self, memory_id: int):
        if not isinstance(memory_id, int) or memory_id <= 0:
            raise ValueError("Invalid memory ID")
        db = self._connect()
        try:
            cursor = db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            db.commit()
            return cursor.rowcount > 0
        finally:
            db.close()
