"""Temporary workspace lifecycle management for per-reconcile work."""

import shutil
import tempfile
from pathlib import Path


class ReconcileWorkspace:
    """Async context manager that creates and cleans up a temp directory."""

    def __init__(self) -> None:
        self.workspace_dir: Path = Path()

    async def __aenter__(self) -> "ReconcileWorkspace":
        self.workspace_dir = Path(tempfile.mkdtemp(prefix="no8s-postgres-"))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.workspace_dir.exists():
            shutil.rmtree(self.workspace_dir, ignore_errors=True)
