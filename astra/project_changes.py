"""Reviewable, per-operation approved changes for selected project text files."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import tempfile
from threading import RLock

from .permissions import RepositoryPermissions
from .repository import SENSITIVE_NAME_PARTS, SUPPORTED_SUFFIXES, SPECIAL_FILES


MAX_CHANGE_BYTES = 24 * 1024
MAX_PENDING_PROPOSALS = 20
PROPOSAL_LIFETIME = timedelta(minutes=15)


@dataclass
class Proposal:
    path: str
    target: Path
    original: bytes
    replacement: bytes
    original_hash: str
    replacement_hash: str
    diff: str
    created_at: datetime
    state: str = "pending"


class ProjectChanges:
    """Stage a diff, then apply or roll it back only after explicit approval."""

    def __init__(self, root: Path, permissions: RepositoryPermissions | None = None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Project root must be a directory")
        self.permissions = permissions or RepositoryPermissions(read=True)
        self.permissions.require_read()
        self._proposals: dict[str, Proposal] = {}
        self._lock = RLock()

    def read_file(self, relative_path: str) -> dict:
        self.permissions.require_read()
        target, display_path = self._resolve_target(relative_path)
        content = self._read_text(target)
        return {"path": display_path, "content": content, "bytes": target.stat().st_size}

    def propose(self, relative_path: str, content: str) -> dict:
        target, display_path = self._resolve_target(relative_path)
        if not isinstance(content, str):
            raise ValueError("Proposed file content must be text")
        original = self._read_bytes(target)
        try:
            original_text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files can be changed") from exc

        newline = "\r\n" if b"\r\n" in original else "\n"
        replacement_text = content.replace("\r\n", "\n").replace("\r", "\n")
        if newline == "\r\n":
            replacement_text = replacement_text.replace("\n", "\r\n")
        replacement = replacement_text.encode("utf-8")
        if len(replacement) > MAX_CHANGE_BYTES:
            raise ValueError(f"Proposed file content exceeds {MAX_CHANGE_BYTES} bytes")
        if replacement == original:
            raise ValueError("Proposed content is identical to the current file")

        diff = "".join(difflib.unified_diff(
            original_text.splitlines(keepends=True),
            replacement_text.splitlines(keepends=True),
            fromfile=f"a/{display_path}",
            tofile=f"b/{display_path}",
        ))
        if len(diff) > MAX_CHANGE_BYTES * 3:
            raise ValueError("Generated review diff is too large")
        original_hash = self._hash(original)
        proposal = Proposal(
            path=display_path,
            target=target,
            original=original,
            replacement=replacement,
            original_hash=original_hash,
            replacement_hash=self._hash(replacement),
            diff=diff,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._expire_proposals()
            if len(self._proposals) >= MAX_PENDING_PROPOSALS:
                raise ValueError("Too many pending project changes; restart ASTRA to clear them")
            proposal_id = secrets.token_urlsafe(24)
            self._proposals[proposal_id] = proposal
        return self._describe(proposal_id, proposal)

    def apply(self, proposal_id: str, confirmed=False) -> dict:
        if confirmed is not True:
            raise PermissionError("Applying project changes requires explicit confirmation")
        self.permissions.require_edit(approved=True)
        with self._lock:
            proposal = self._get_proposal(proposal_id, expected_state="pending")
            self._assert_unchanged(proposal, proposal.original_hash)
            self._atomic_replace(proposal.target, proposal.replacement)
            proposal.state = "applied"
            return self._describe(proposal_id, proposal)

    def rollback(self, proposal_id: str, confirmed=False) -> dict:
        if confirmed is not True:
            raise PermissionError("Rolling back project changes requires explicit confirmation")
        self.permissions.require_edit(approved=True)
        with self._lock:
            proposal = self._get_proposal(proposal_id, expected_state="applied")
            self._assert_unchanged(proposal, proposal.replacement_hash)
            self._atomic_replace(proposal.target, proposal.original)
            proposal.state = "rolled_back"
            return self._describe(proposal_id, proposal)

    def _resolve_target(self, relative_path: str):
        if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 240:
            raise ValueError("Project file path must contain 1–240 characters")
        if "\\" in relative_path:
            raise ValueError("Project file paths must use relative forward-slash syntax")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("Project file path must stay within the configured root")
        if any(part.startswith(".") for part in relative.parts):
            raise ValueError("Hidden project paths cannot be edited")
        filename = relative.name.lower()
        if (
            any(part in filename for part in SENSITIVE_NAME_PARTS)
            or filename.startswith(".env")
            or Path(filename).suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
            or (Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES and filename not in SPECIAL_FILES)
        ):
            raise ValueError("This project file type or name is not eligible for editing")

        target = self.root.joinpath(*relative.parts)
        if target.is_symlink():
            raise ValueError("Symlinked project paths cannot be edited")
        for parent in (self.root, *target.parents):
            if parent == self.root.parent:
                break
            if parent.is_symlink():
                raise ValueError("Symlinked project paths cannot be edited")
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ValueError("Project path must identify an existing file inside the configured root")
        if resolved.stat().st_size > MAX_CHANGE_BYTES:
            raise ValueError(f"Project files larger than {MAX_CHANGE_BYTES} bytes cannot be edited")
        return resolved, relative.as_posix()

    @staticmethod
    def _read_bytes(target: Path) -> bytes:
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ValueError(f"Could not read project file: {exc.strerror}") from exc
        if len(data) > MAX_CHANGE_BYTES:
            raise ValueError(f"Project files larger than {MAX_CHANGE_BYTES} bytes cannot be edited")
        return data

    def _read_text(self, target: Path) -> str:
        try:
            return self._read_bytes(target).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files can be changed") from exc

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _assert_unchanged(self, proposal: Proposal, expected_hash: str):
        target, _ = self._resolve_target(proposal.path)
        if target != proposal.target or self._hash(self._read_bytes(target)) != expected_hash:
            raise ValueError("Project file changed since review; create a new proposal")

    def _atomic_replace(self, target: Path, content: bytes):
        current_target, _ = self._resolve_target(target.relative_to(self.root).as_posix())
        if current_target != target:
            raise ValueError("Project file target changed since review")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".astra-change-", dir=target.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                os.chmod(temporary_path, stat.S_IMODE(target.stat().st_mode))
            os.replace(temporary_path, target)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _get_proposal(self, proposal_id: str, expected_state: str) -> Proposal:
        self._expire_proposals()
        if not isinstance(proposal_id, str) or proposal_id not in self._proposals:
            raise ValueError("Project change proposal was not found or has expired")
        proposal = self._proposals[proposal_id]
        if proposal.state != expected_state:
            raise ValueError(f"Project change is already {proposal.state.replace('_', ' ')}")
        return proposal

    def _expire_proposals(self):
        cutoff = datetime.now(timezone.utc) - PROPOSAL_LIFETIME
        expired = [
            proposal_id for proposal_id, proposal in self._proposals.items()
            if proposal.created_at < cutoff
        ]
        for proposal_id in expired:
            del self._proposals[proposal_id]

    @staticmethod
    def _describe(proposal_id: str, proposal: Proposal) -> dict:
        return {
            "id": proposal_id,
            "path": proposal.path,
            "state": proposal.state,
            "diff": proposal.diff,
            "original_sha256": proposal.original_hash,
            "proposed_sha256": proposal.replacement_hash,
            "rollback_available": proposal.state == "applied",
        }
