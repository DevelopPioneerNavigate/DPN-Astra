"""Explicit default-deny permissions for future repository tooling."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RepositoryPermissions:
    read: bool = False
    edit: bool = False
    execute: bool = False

    def require_read(self):
        if not self.read:
            raise PermissionError("Repository read access is not authorized")

    def require_edit(self, approved=False):
        if not self.edit and approved is not True:
            raise PermissionError("Repository edits are not authorized")

    def require_execute(self):
        if not self.execute:
            raise PermissionError("Repository command execution is not authorized")
