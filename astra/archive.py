"""SQLite store for memories the user explicitly chose to retain."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 2
MEMORY_KINDS = (
    "note", "preference", "decision", "episode", "procedure", "hypothesis", "fiction",
)
MEMORY_COLUMNS = (
    "id, note, created_at, kind, provenance, source, consented_at, revision, updated_at"
)
MAX_EXPORT_BYTES = 4 * 1024 * 1024
MAX_EXPORT_MEMORIES = 10000


class MemoryExportTooLarge(ValueError):
    """A complete JSON export exceeds the bounded HTTP export allowance."""


class MemoryConflict(RuntimeError):
    """The reviewed memory has changed since it was loaded."""


class MemoryNotFound(LookupError):
    """The memory selected for editing was deleted."""


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
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported memory schema version: {version}")
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL, created_at TEXT NOT NULL)")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
            if not {"id", "note", "created_at"} <= columns:
                raise RuntimeError("Memory database has an incompatible schema")
            if version == 0:
                if columns != {"id", "note", "created_at"}:
                    raise RuntimeError("Unversioned memory database has unexpected columns")
                db.execute(
                    "ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'note'"
                )
                db.execute(
                    "ALTER TABLE memories ADD COLUMN provenance TEXT NOT NULL DEFAULT 'legacy_user_note'"
                )
                db.execute(
                    "ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT ''"
                )
                db.execute("ALTER TABLE memories ADD COLUMN consented_at TEXT")
            elif not {"kind", "provenance", "source", "consented_at"} <= columns:
                raise RuntimeError("Memory database has an incomplete versioned schema")
            if version < 2:
                db.execute("ALTER TABLE memories ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
                db.execute("ALTER TABLE memories ADD COLUMN updated_at TEXT")
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif not {"revision", "updated_at"} <= columns:
                raise RuntimeError("Memory database has an incomplete versioned schema")
            db.commit()
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def list_memories(self, limit=40, *, include_fiction=True, before_id=None):
        if before_id is not None:
            self._validate_id(before_id)
        db = self._connect()
        try:
            conditions = [] if include_fiction else ["kind != 'fiction'"]
            parameters = []
            if before_id is not None:
                conditions.append("id < ?")
                parameters.append(before_id)
            condition = "WHERE " + " AND ".join(conditions) if conditions else ""
            parameters.append(max(1, min(limit, 100)))
            rows = db.execute(
                f"SELECT {MEMORY_COLUMNS} "
                f"FROM memories {condition} ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def memory_page(self, *, before_id=None):
        rows = self.list_memories(limit=41, before_id=before_id)
        return {
            "memories": rows[:40],
            "next_before_id": rows[39]["id"] if len(rows) > 40 else None,
        }

    def export_json(self) -> bytes:
        db = self._connect()
        try:
            db.execute("BEGIN")
            count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if count > MAX_EXPORT_MEMORIES:
                raise MemoryExportTooLarge("Archive exceeds JSON export entry limit; use a SQLite snapshot")
            metadata = {
                "format": "dpn-astra-memory-export",
                "format_version": 1,
                "schema_version": SCHEMA_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "memory_count": count,
            }
            parts = []
            size = 0

            def append(text):
                nonlocal size
                chunk = text.encode("utf-8")
                size += len(chunk)
                if size > MAX_EXPORT_BYTES:
                    raise MemoryExportTooLarge("Archive exceeds 4 MiB JSON export limit; use a SQLite snapshot")
                parts.append(chunk)

            append(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))[:-1])
            append(',"memories":[')
            for index, row in enumerate(db.execute(f"SELECT {MEMORY_COLUMNS} FROM memories ORDER BY id")):
                if index:
                    append(",")
                append(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            append("]}\n")
            return b"".join(parts)
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    @staticmethod
    def _validate_id(value):
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise ValueError("Invalid memory ID")

    @staticmethod
    def _validate_entry(note, kind, source):
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
            raise ValueError("Memory must contain 1–1000 characters")
        if not isinstance(kind, str) or kind not in MEMORY_KINDS:
            raise ValueError("Invalid memory kind")
        if not isinstance(source, str) or len(source.strip()) > 240:
            raise ValueError("Memory source must contain at most 240 characters")

    def remember(self, note: str, *, kind: str = "note", source: str = ""):
        self._validate_entry(note, kind, source)
        db = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "INSERT INTO memories "
                "(note, created_at, kind, provenance, source, consented_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (note.strip(), now, kind, "user_entry", source.strip(), now),
            )
            db.commit()
            return {
                "id": cursor.lastrowid,
                "note": note.strip(),
                "created_at": now,
                "kind": kind,
                "provenance": "user_entry",
                "source": source.strip(),
                "consented_at": now,
                "revision": 1,
                "updated_at": None,
            }
        finally:
            db.close()

    def edit(self, memory_id: int, note: str, *, revision: int, kind="note", source=""):
        self._validate_id(memory_id)
        self._validate_entry(note, kind, source)
        if type(revision) is not int or not 1 <= revision < 9223372036854775807:
            raise ValueError("Invalid memory revision")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "UPDATE memories SET note = ?, kind = ?, source = ?, "
                "provenance = 'user_edit', consented_at = COALESCE(consented_at, ?), "
                "updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                (note.strip(), kind, source.strip(), now, now, memory_id, revision),
            )
            if not cursor.rowcount:
                if db.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone():
                    raise MemoryConflict("Memory changed since it was loaded; reload before editing")
                raise MemoryNotFound("Memory no longer exists")
            row = db.execute(
                f"SELECT {MEMORY_COLUMNS} FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()
            db.commit()
            return dict(row)
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def forget(self, memory_id: int):
        self._validate_id(memory_id)
        db = self._connect()
        try:
            cursor = db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            db.commit()
            return cursor.rowcount > 0
        finally:
            db.close()
