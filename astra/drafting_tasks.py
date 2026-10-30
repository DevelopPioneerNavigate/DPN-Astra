"""Process-local, cooperatively cancellable review-only drafting tasks."""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import secrets
import sqlite3
from threading import RLock, Thread
import time

from .project_changes import MAX_CHANGE_SET_FILES, ProjectDraftConflict
from .project_drafting import ProjectDrafting
from .inference import ModelError


MAX_TASKS = 20


class DraftStopped(RuntimeError):
    """Cancellation or budget exhaustion prevents accepting draft output."""


@dataclass
class DraftTask:
    id: str
    paths: list[str]
    created_at: str
    started: float
    budget_seconds: int
    state: str = "queued"
    worker_active: bool = True
    error: str | None = None
    result: dict | None = None
    outcome_code: str | None = None
    checkpoint_error: str | None = None


class DraftingTasks:
    def __init__(self, drafting: ProjectDrafting):
        self.drafting = drafting
        self._lock = RLock()
        self._tasks: dict[str, DraftTask] = {}
        self._store = drafting.repository.changes.audit_store
        self._owner_id = secrets.token_urlsafe(24)

    def start(self, paths, instruction, *, confirmed=False, budget_seconds=120) -> dict:
        self.drafting._check_request(instruction, confirmed)
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_CHANGE_SET_FILES or not all(
            isinstance(path, str) and 1 <= len(path) <= 240 for path in paths
        ):
            raise ValueError("Select 1-5 existing relative file paths of 1-240 characters")
        if type(budget_seconds) is not int or not 10 <= budget_seconds <= 120:
            raise ValueError("Draft budget must be an integer from 10 to 120 seconds")
        with self._lock:
            if any(task.worker_active for task in self._tasks.values()):
                raise ProjectDraftConflict("A drafting task is still running or draining; wait for its worker to exit")
            while len(self._tasks) >= MAX_TASKS:
                del self._tasks[next(iter(self._tasks))]
            task = DraftTask(
                secrets.token_urlsafe(24), list(paths), datetime.now(timezone.utc).isoformat(),
                time.monotonic(), budget_seconds,
            )
            self._persist(task, required=True)
            self._tasks[task.id] = task
            try:
                Thread(target=self._run, args=(task, instruction), daemon=True).start()
            except RuntimeError as exc:
                task.state = "failed"
                task.worker_active = False
                task.error = f"Could not start drafting worker: {exc}"
                task.outcome_code = "worker_start_failed"
                self._persist(task)
                raise ModelError(task.error) from exc
            return self._describe(task)

    def status(self, task_id) -> dict:
        with self._lock:
            task = self._get(task_id)
            self._expire(task)
            return self._describe(task)

    def list_tasks(self) -> dict:
        with self._lock:
            tasks = []
            for task in reversed(list(self._tasks.values())):
                self._expire(task)
                tasks.append({key: value for key, value in self._describe(task).items() if key != "result"})
            return {"tasks": tasks, "durable": False}

    def history(self) -> dict:
        with self._lock:
            report = self._store.task_checkpoints() if self._store is not None else {
                "checkpoints": [], "total": 0, "truncated": False,
            }
            for record in report["checkpoints"]:
                owned = record.pop("owner_id") == self._owner_id
                record["task_available"] = owned and record["task_id"] in self._tasks
                record["continuity"] = (
                    "current_process" if record["task_available"]
                    else "historical_unverified" if record["worker_active"]
                    else "historical_finished"
                )
                record["recorded_worker_active"] = record.pop("worker_active")
                record["metadata_only"] = True
            return {**report, "durable": self._store is not None}

    def cancel(self, task_id, *, confirmed=False) -> dict:
        if confirmed is not True:
            raise PermissionError("Cancelling a draft task requires explicit confirmation")
        with self._lock:
            task = self._get(task_id)
            self._expire(task)
            if task.worker_active and task.state not in {"cancel_requested", "timed_out", "completed", "failed"}:
                task.state = "cancel_requested"
                task.error = "Cancellation requested; late output will not be staged. Inference may still be running."
                task.outcome_code = "cancel_requested"
                self._persist(task)
            return self._describe(task)

    def _get(self, task_id) -> DraftTask:
        if not isinstance(task_id, str) or task_id not in self._tasks:
            raise LookupError("Draft task was not found (possibly restarted or evicted)")
        return self._tasks[task_id]

    def _expire(self, task: DraftTask):
        if task.worker_active and task.state not in {"cancel_requested", "timed_out", "completed", "failed"} and (
            time.monotonic() - task.started >= task.budget_seconds
        ):
            task.state = "timed_out"
            task.error = "Draft budget exhausted; output will not be staged. Inference may still be running."
            task.outcome_code = "budget_exhausted"
            self._persist(task)

    def _check(self, task: DraftTask):
        self._expire(task)
        if task.state in {"cancel_requested", "timed_out"}:
            raise DraftStopped(task.error)

    def _progress(self, task: DraftTask, state: str):
        with self._lock:
            self._check(task)
            task.state = state
            self._persist(task, required=True)
            self._check(task)

    def _stage(self, task: DraftTask, entries, baselines):
        # Serialize cancellation against accepting the complete validated set.
        with self._lock:
            self._check(task)
            task.state = "staging"
            self._persist(task, required=True)
            result = self.drafting.repository.validation.propose_many(
                entries, expected_sha256=baselines, before_stage=lambda: self._check(task),
            )
            task.state = "completed"
            task.outcome_code = "review_staged"
            return result

    def _run(self, task: DraftTask, instruction: str):
        try:
            result = self.drafting.draft_many(
                task.paths, instruction, confirmed=True,
                progress=lambda state: self._progress(task, state),
                stage=lambda entries, baselines: self._stage(task, entries, baselines),
            )
            with self._lock:
                task.result = result
        except DraftStopped:
            pass
        except Exception as exc:
            logging.getLogger(__name__).exception("Draft task %s failed", task.id)
            with self._lock:
                self._expire(task)
                if task.state not in {"cancel_requested", "timed_out"}:
                    task.state = "failed"
                    task.error = f"{type(exc).__name__}: {exc}"
                    task.outcome_code = "draft_failed"
        finally:
            with self._lock:
                if task.state == "cancel_requested":
                    task.state = "cancelled"
                    task.error = "Draft cancelled; no output was staged. Worker has exited."
                    task.outcome_code = "cancelled"
                elif task.state == "timed_out":
                    task.error = "Draft budget exhausted; no output was staged. Worker has exited."
                task.worker_active = False
                self._persist(task)

    def _persist(self, task: DraftTask, *, required=False):
        if self._store is None:
            return
        try:
            self._store.save_task_checkpoint(
                task_id=task.id, owner_id=self._owner_id, paths=task.paths,
                created_at=task.created_at, budget_seconds=task.budget_seconds,
                state=task.state, worker_active=task.worker_active, outcome_code=task.outcome_code,
                proposal_ids=[entry["proposal"]["id"] for entry in task.result["changes"]] if task.result else [],
            )
            task.checkpoint_error = None
        except (sqlite3.Error, OSError):
            task.checkpoint_error = "Task checkpoint storage unavailable; recorded history may be stale."
            logging.getLogger(__name__).exception("Could not persist draft task %s", task.id)
            if required:
                raise

    def _describe(self, task: DraftTask) -> dict:
        return {
            "id": task.id, "paths": list(task.paths), "created_at": task.created_at,
            "budget_seconds": task.budget_seconds, "state": task.state,
            "worker_active": task.worker_active, "error": task.error,
            "result": task.result if not task.worker_active else None,
            "tests_executed": False, "durable": False,
            "transport_interrupt_supported": False,
            "checkpoint_enabled": self._store is not None,
            "checkpoint_error": task.checkpoint_error,
        }
