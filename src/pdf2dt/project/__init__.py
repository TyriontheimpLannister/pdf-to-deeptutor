"""Project workspace — Stage 0.

A project is a directory on disk that owns the full lifecycle of one
source document, from raw MinerU output through final exports.
"""

from .workspace import ProjectWorkspace, create_workspace, load_workspace
from .manifest import (
    StageRecord,
    StageStatus,
    record_stage,
    save_manifest,
)

__all__ = [
    "ProjectWorkspace",
    "create_workspace",
    "load_workspace",
    "StageRecord",
    "StageStatus",
    "record_stage",
    "save_manifest",
]