"""Project workspace — Stage 0.

A project is a directory on disk that owns the full lifecycle of one
source document, from raw MinerU output through final exports.
"""

from .manifest import (
    StageRecord,
    StageStatus,
    get_stage_status,
    is_stage_completed,
    record_stage,
    save_manifest,
)
from .workspace import ProjectWorkspace, create_workspace, load_workspace

__all__ = [
    "ProjectWorkspace",
    "StageRecord",
    "StageStatus",
    "create_workspace",
    "get_stage_status",
    "is_stage_completed",
    "load_workspace",
    "record_stage",
    "save_manifest",
]
