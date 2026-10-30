"""SQLite-backed, hash-chained metadata log for project change lifecycle events."""
from datetime import datetime, timezone
import hashlib
import json
import re
import os
from pathlib import Path
import sqlite3


ZERO_HASH = "0" * 64
SCHEMA_VERSION = 4


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
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
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
            db.execute(
                """CREATE TABLE IF NOT EXISTS drafting_task_checkpoints (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    paths_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    budget_seconds INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    worker_active INTEGER NOT NULL,
                    outcome_code TEXT,
                    proposal_ids_json TEXT NOT NULL,
                    UNIQUE (project_key, task_id)
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS drafting_task_checkpoint_order
                   ON drafting_task_checkpoints (project_key, sequence DESC)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS sandbox_run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    image_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    exit_code INTEGER,
                    duration_ms INTEGER NOT NULL,
                    output_truncated INTEGER NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE (project_key, run_id)
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS sandbox_run_event_order
                   ON sandbox_run_events (project_key, sequence DESC)"""
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

    def save_task_checkpoint(self, *, task_id, owner_id, paths, created_at,
                             budget_seconds, state, worker_active, outcome_code, proposal_ids):
        if not self.project_key:
            raise ValueError("Task checkpoints require a configured project identity")
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO drafting_task_checkpoints
                   (project_key, task_id, owner_id, paths_json, created_at, updated_at,
                    budget_seconds, state, worker_active, outcome_code, proposal_ids_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_key, task_id) DO UPDATE SET
                       updated_at=excluded.updated_at, state=excluded.state,
                       worker_active=excluded.worker_active, outcome_code=excluded.outcome_code,
                       proposal_ids_json=excluded.proposal_ids_json""",
                (
                    self.project_key, task_id, owner_id, json.dumps(paths), created_at,
                    datetime.now(timezone.utc).isoformat(), budget_seconds, state,
                    int(worker_active), outcome_code, json.dumps(proposal_ids),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def task_checkpoints(self, limit=100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Task checkpoint limit must be between 1 and 100")
        db = self._connect()
        try:
            db.execute("BEGIN")
            rows = db.execute(
                """SELECT task_id, owner_id, paths_json, created_at, updated_at,
                          budget_seconds, state, worker_active, outcome_code, proposal_ids_json
                   FROM drafting_task_checkpoints WHERE project_key = ?
                   ORDER BY sequence DESC LIMIT ?""",
                (self.project_key, limit),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM drafting_task_checkpoints WHERE project_key = ?",
                (self.project_key,),
            ).fetchone()[0]
            records = []
            for row in rows:
                record = dict(row)
                for source, target in (("paths_json", "paths"), ("proposal_ids_json", "proposal_ids")):
                    values = json.loads(record.pop(source))
                    if not isinstance(values, list) or len(values) > 5 or not all(
                        isinstance(value, str) and 1 <= len(value) <= 240 for value in values
                    ):
                        raise ValueError("Invalid stored task checkpoint metadata")
                    record[target] = values
                record["worker_active"] = bool(record["worker_active"])
                records.append(record)
            return {"checkpoints": records, "total": total, "truncated": total > len(records)}
        finally:
            db.close()

    def append_sandbox_run(self, *, run_id, profile, image_digest, state,
                           exit_code, duration_ms, output_truncated):
        if not self.project_key:
            raise ValueError("Sandbox run records require a configured project identity")
        if profile != "unit_tests" or state not in {
            "completed", "failed", "timed_out", "cancelled", "runner_error",
        }:
            raise ValueError("Invalid sandbox run metadata")
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid sandbox run ID")
        if (
            not isinstance(image_digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_digest)
        ):
            raise ValueError("Invalid sandbox image digest")
        if exit_code is not None and type(exit_code) is not int:
            raise ValueError("Sandbox run exit code must be an integer or null")
        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("Sandbox run duration must be a non-negative integer")
        if type(output_truncated) is not bool:
            raise ValueError("Sandbox output-truncated marker must be boolean")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                """SELECT event_hash FROM sandbox_run_events
                   WHERE project_key = ? ORDER BY sequence DESC LIMIT 1""",
                (self.project_key,),
            ).fetchone()
            record = {
                "project_key": self.project_key,
                "run_id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "profile": profile,
                "image_digest": image_digest,
                "state": state,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "output_truncated": int(output_truncated),
                "previous_hash": previous["event_hash"] if previous else ZERO_HASH,
            }
            record["event_hash"] = self._sandbox_run_hash(record)
            db.execute(
                """INSERT INTO sandbox_run_events
                   (project_key, run_id, timestamp, profile, image_digest, state,
                    exit_code, duration_ms, output_truncated, previous_hash, event_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(record[key] for key in (
                    "project_key", "run_id", "timestamp", "profile", "image_digest",
                    "state", "exit_code", "duration_ms", "output_truncated",
                    "previous_hash", "event_hash",
                )),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def sandbox_runs(self, limit=100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Sandbox run limit must be between 1 and 100")
        db = self._connect()
        try:
            cursor = db.execute(
                """SELECT sequence, project_key, run_id, timestamp, profile, image_digest, state,
                          exit_code, duration_ms, output_truncated, previous_hash, event_hash
                   FROM sandbox_run_events WHERE project_key = ?
                   ORDER BY sequence ASC""",
                (self.project_key,),
            )
            total = db.execute(
                "SELECT COUNT(*) FROM sandbox_run_events WHERE project_key = ?",
                (self.project_key,),
            ).fetchone()[0]
            events = []
            previous_hash = ZERO_HASH
            valid = True
            checked = 0
            first_invalid_sequence = None
            for row in cursor:
                record = dict(row)
                events.append({
                    key: value for key, value in record.items()
                    if key != "project_key"
                })
                if len(events) > limit:
                    events.pop(0)
                if not valid:
                    continue
                if (
                    record["previous_hash"] != previous_hash
                    or record["event_hash"] != self._sandbox_run_hash(record)
                ):
                    valid = False
                    first_invalid_sequence = record["sequence"]
                    continue
                previous_hash = record["event_hash"]
                checked += 1
            result = {
                "events": events,
                "total": total,
                "truncated": total > len(events),
                "integrity": {"valid": valid, "checked": checked},
            }
            if first_invalid_sequence is not None:
                result["integrity"]["first_invalid_sequence"] = first_invalid_sequence
            return result
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

    @staticmethod
    def _sandbox_run_hash(record: dict) -> str:
        payload = {
            key: record[key]
            for key in (
                "project_key", "run_id", "timestamp", "profile", "image_digest",
                "state", "exit_code", "duration_ms", "output_truncated",
                "previous_hash",
            )
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
