"""SQLite-backed, hash-chained metadata log for project change lifecycle events."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


ZERO_HASH = "0" * 64
SCHEMA_VERSION = 2


class ProjectAuditLog:
    def __init__(self, path: Path, *, project_key: str = ""):
        self.path = Path(path)
        self.project_key = project_key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if os.name != "nt":
            self.path.chmod(0o600)

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
                raise RuntimeError(f"Unsupported project audit schema version: {version}")
            db.execute(
                """CREATE TABLE IF NOT EXISTS project_change_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    proposed_sha256 TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS project_review_checkpoints (
                    project_key TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    proposed_sha256 TEXT NOT NULL,
                    PRIMARY KEY (project_key, proposal_id)
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS project_review_checkpoint_order
                   ON project_review_checkpoints (project_key, sequence DESC)"""
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @property
    def durable(self):
        return True

    def append(self, event: str, proposal_id: str, proposal):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT sequence, event_hash FROM project_change_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = previous["sequence"] + 1 if previous else 1
            previous_hash = previous["event_hash"] if previous else ZERO_HASH
            record = {
                "sequence": sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "proposal_id": proposal_id,
                "path": proposal.path,
                "state": proposal.state,
                "original_sha256": proposal.original_hash,
                "proposed_sha256": proposal.replacement_hash,
                "previous_hash": previous_hash,
            }
            event_hash = self._event_hash(record)
            db.execute(
                """INSERT INTO project_change_events
                (sequence, timestamp, event, proposal_id, path, state,
                 original_sha256, proposed_sha256, previous_hash, event_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sequence, record["timestamp"], event, proposal_id, proposal.path,
                    proposal.state, proposal.original_hash, proposal.replacement_hash,
                    previous_hash, event_hash,
                ),
            )
            if self.project_key:
                db.execute(
                    """INSERT INTO project_review_checkpoints
                       (project_key, proposal_id, sequence, timestamp, event, path,
                        state, original_sha256, proposed_sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(project_key, proposal_id) DO UPDATE SET
                           sequence=excluded.sequence, timestamp=excluded.timestamp,
                           event=excluded.event, path=excluded.path, state=excluded.state,
                           original_sha256=excluded.original_sha256,
                           proposed_sha256=excluded.proposed_sha256""",
                    (
                        self.project_key, proposal_id, sequence, record["timestamp"],
                        event, proposal.path, proposal.state, proposal.original_hash,
                        proposal.replacement_hash,
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Audit event limit must be between 1 and 100")
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT sequence, timestamp, event, proposal_id, path, state,
                          original_sha256, proposed_sha256, previous_hash, event_hash
                   FROM project_change_events ORDER BY sequence DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            total = db.execute("SELECT COUNT(*) FROM project_change_events").fetchone()[0]
            events = [dict(row) for row in reversed(rows)]
            return {"events": events, "total": total}
        finally:
            db.close()

    def checkpoints(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Checkpoint limit must be between 1 and 100")
        db = self._connect()
        try:
            db.execute("BEGIN")
            rows = db.execute(
                """SELECT proposal_id, sequence, timestamp, event, path, state,
                          original_sha256, proposed_sha256
                   FROM project_review_checkpoints WHERE project_key = ?
                   ORDER BY sequence DESC LIMIT ?""",
                (self.project_key, limit),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM project_review_checkpoints WHERE project_key = ?",
                (self.project_key,),
            ).fetchone()[0]
            return {"checkpoints": [dict(row) for row in rows], "total": total, "truncated": total > len(rows)}
        finally:
            db.close()

    def checkpoint(self, proposal_id: str) -> dict:
        db = self._connect()
        try:
            row = db.execute(
                """SELECT proposal_id, sequence, timestamp, event, path, state,
                          original_sha256, proposed_sha256
                   FROM project_review_checkpoints
                   WHERE project_key = ? AND proposal_id = ?""",
                (self.project_key, proposal_id),
            ).fetchone()
            if row is None:
                raise LookupError("Project review checkpoint was not found")
            return dict(row)
        finally:
            db.close()

    def verify(self):
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT sequence, timestamp, event, proposal_id, path, state,
                          original_sha256, proposed_sha256, previous_hash, event_hash
                   FROM project_change_events ORDER BY sequence"""
            )
            expected_sequence = 1
            previous_hash = ZERO_HASH
            for row in rows:
                record = dict(row)
                if record["sequence"] != expected_sequence:
                    return {
                        "valid": False,
                        "checked": expected_sequence - 1,
                        "first_invalid_sequence": record["sequence"],
                    }
                if record["previous_hash"] != previous_hash or record["event_hash"] != self._event_hash(record):
                    return {
                        "valid": False,
                        "checked": expected_sequence - 1,
                        "first_invalid_sequence": expected_sequence,
                    }
                previous_hash = record["event_hash"]
                expected_sequence += 1
            return {"valid": True, "checked": expected_sequence - 1, "first_invalid_sequence": None}
        finally:
            db.close()

    @staticmethod
    def _event_hash(record: dict) -> str:
        payload = {
            key: record[key]
            for key in (
                "sequence", "timestamp", "event", "proposal_id", "path", "state",
                "original_sha256", "proposed_sha256", "previous_hash",
            )
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
