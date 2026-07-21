"""Workspace root confinement for agent tools."""

from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a path escapes the allowed workspace root."""


class Workspace:
    """Resolve and validate paths under a single allowed root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {self.root}")

    def resolve(self, relative: str) -> Path:
        """Map a relative (or absolute-under-root) path to an absolute path.

        Rejects ``..`` escapes and absolute paths outside the root.
        """
        raw = (relative or "").strip() or "."
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(
                f"path escapes workspace root ({self.root}): {relative}"
            ) from exc
        return resolved
