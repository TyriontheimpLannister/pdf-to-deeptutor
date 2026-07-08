"""Project manifest: stage records and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .workspace import ProjectWorkspace


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


def record_stage(
    workspace: ProjectWorkspace,
    stage_name: str,
    *,
    status: StageStatus,
    input_fingerprint: str | None = None,
    output_fingerprint: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Update the project manifest with a single stage record."""
    manifest = workspace.load_manifest()
    now = _now()
    record = manifest["stages"].get(stage_name) or {
        "stage": stage_name,
        "first_started_at": now,
    }
    record.update(
        {
            "status": status.value if isinstance(status, StageStatus) else status,
            "last_updated_at": now,
            "input_fingerprint": input_fingerprint or record.get("input_fingerprint"),
            "output_fingerprint": output_fingerprint or record.get("output_fingerprint"),
            "error": error,
            "metadata": {**(record.get("metadata") or {}), **(metadata or {})},
        }
    )
    manifest["stages"][stage_name] = record
    manifest["updated_at"] = now
    workspace.write_manifest(manifest)


def save_manifest(workspace: ProjectWorkspace, manifest: dict[str, Any]) -> None:
    """Persist a full manifest dict (after custom edits)."""
    manifest["updated_at"] = _now()
    workspace.write_manifest(manifest)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Convenience type re-exports for consumers
class StageRecord(dict):
    """Marker subclass; treated as a plain dict at runtime."""

    pass