"""Project workspace: directory layout and lifecycle."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Standard subdirectory layout for every project. Mirrors ARCHITECTURE.md.
STANDARD_DIRS: tuple[str, ...] = (
    "source",
    "providers/mineru/raw",
    "assets",
    "normalized",
    "book_view",
    "topic_assignments",
    "export_plans",
    "review",
    "exports/deeptutor",
    "reports",
    "logs",
)


class ProjectWorkspace:
    """A single project's on-disk workspace."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ #
    # Path accessors
    # ------------------------------------------------------------------ #

    def dir(self, relative: str) -> Path:
        """Resolve a subdirectory under the project root, ensuring it exists."""
        p = self.root / relative
        p.mkdir(parents=True, exist_ok=True)
        return p

    def file(self, relative: str) -> Path:
        """Resolve a file path under the project root (parent dirs auto-created)."""
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def source_dir(self) -> Path:
        return self.dir("source")

    @property
    def mineru_raw_dir(self) -> Path:
        return self.dir("providers/mineru/raw")

    @property
    def assets_dir(self) -> Path:
        return self.dir("assets")

    @property
    def normalized_dir(self) -> Path:
        return self.dir("normalized")

    @property
    def book_view_dir(self) -> Path:
        return self.dir("book_view")

    @property
    def topic_assignments_dir(self) -> Path:
        return self.dir("topic_assignments")

    @property
    def export_plans_dir(self) -> Path:
        return self.dir("export_plans")

    @property
    def review_dir(self) -> Path:
        return self.dir("review")

    @property
    def exports_dir(self) -> Path:
        return self.dir("exports/deeptutor")

    @property
    def reports_dir(self) -> Path:
        return self.dir("reports")

    @property
    def logs_dir(self) -> Path:
        return self.dir("logs")

    @property
    def manifest_path(self) -> Path:
        return self.root / "project.json"

    # ------------------------------------------------------------------ #
    # Manifest helpers
    # ------------------------------------------------------------------ #

    def exists(self) -> bool:
        return self.manifest_path.is_file()

    def load_manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # Convenience: copy raw artifacts
    # ------------------------------------------------------------------ #

    def copy_source(self, source_pdf: Path | str | None) -> Path | None:
        """Copy the original PDF into source/ for immutable preservation."""
        if source_pdf is None:
            return None
        src = Path(source_pdf)
        if not src.is_file():
            return None
        dest = self.source_dir / src.name
        shutil.copy2(src, dest)
        return dest

    def copy_mineru_raw(self, source_dir: Path | str) -> Path:
        """Copy a MinerU task directory's contents into providers/mineru/raw/."""
        src = Path(source_dir)
        if not src.is_dir():
            raise FileNotFoundError(src)
        dest = self.mineru_raw_dir
        # Preserve the source directory tree under raw/<basename>.
        target = dest / src.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        return target


# ---------------------------------------------------------------------- #
# Factory helpers
# ---------------------------------------------------------------------- #


def create_workspace(
    root: Path | str,
    *,
    project_id: str,
    title: str,
    subject: str | None = None,
    stage: str | None = None,
    source_path: Path | str | None = None,
    source_sha256: str | None = None,
) -> ProjectWorkspace:
    """Create a new project workspace and seed its manifest."""
    ws = ProjectWorkspace(root)
    if ws.exists():
        raise FileExistsError(f"Project already exists at {ws.root}")

    # Materialize standard dirs.
    for rel in STANDARD_DIRS:
        ws.dir(rel)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project_id": project_id,
        "title": title,
        "created_at": _now(),
        "updated_at": _now(),
        "subject": {"subject": subject, "stage": stage},
        "source": {
            "path": str(source_path) if source_path else None,
            "sha256": source_sha256,
        },
        "stages": {},
        "outline_used": None,
        "exports": [],
    }

    if source_path is not None:
        copied = ws.copy_source(source_path)
        if copied is not None:
            manifest["source"]["copied_to"] = str(copied.relative_to(ws.root))

    ws.write_manifest(manifest)
    return ws


def load_workspace(root: Path | str) -> ProjectWorkspace:
    """Open an existing project workspace. Raises if no manifest is found."""
    ws = ProjectWorkspace(root)
    if not ws.exists():
        raise FileNotFoundError(f"No project manifest at {ws.manifest_path}")
    return ws


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")