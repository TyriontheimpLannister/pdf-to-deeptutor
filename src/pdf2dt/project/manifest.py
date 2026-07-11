"""Project manifest: stage records and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
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


def get_stage_status(
    workspace: ProjectWorkspace, stage_name: str
) -> StageStatus | None:
    """Return the recorded status for *stage_name*, or ``None`` if the stage
    has never been recorded."""
    if not workspace.exists():
        return None
    manifest = workspace.load_manifest()
    record = manifest.get("stages", {}).get(stage_name)
    if record is None:
        return None
    return StageStatus(record["status"])


def is_stage_completed(workspace: ProjectWorkspace, stage_name: str) -> bool:
    """Return ``True`` if the stage has reached a terminal ``done`` state.

    A stage is considered done when its last recorded status is either
    ``COMPLETED`` or ``SKIPPED``. ``SKIPPED`` is recorded when the
    pipeline's resume guard declines to re-run an already-finished
    stage; treating it as done prevents a re-run guard from flipping
    the status to ``SKIPPED`` (no-op) and then back to ``not done``
    (forced re-run) on the next invocation, which used to drop
    review_state.json and other stage 5/6 audit logs.
    """
    status = get_stage_status(workspace, stage_name)
    return status in (StageStatus.COMPLETED, StageStatus.SKIPPED)


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
