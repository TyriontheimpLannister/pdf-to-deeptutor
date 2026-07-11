"""Review state store — Stage 6.

The store is small and side-effect free.  The on-disk layout is:

* ``review/geometry_figures.json`` — produced by Stage 5; the
  store *edits* this file in place when decisions are applied.
* ``review/review_state.json`` — the lightweight record of every
  decision, including the ``reviewer_note`` and ``applied_at``
  timestamp.

The two-file design means the user can wipe the audit log
(``review_state.json``) without losing the geometry queue, and
the renderer can load just the queue to know which relations are
includable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..geometry import (
    NON_PROMOTABLE_EVIDENCE,
    Evidence,
    GeometryFigure,
    ReviewState,
)
from ..project import ProjectWorkspace, StageStatus, record_stage


class PromotionError(ValueError):
    """Raised when a review action would violate evidence rules."""


class ReviewAction(str, Enum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"


# Map action → resulting review state.
_ACTION_TO_STATE: dict[ReviewAction, ReviewState] = {
    ReviewAction.CONFIRM: ReviewState.CONFIRMED,
    ReviewAction.CORRECT: ReviewState.CORRECTED,
    ReviewAction.REJECT: ReviewState.REJECTED,
}


@dataclass
class ReviewDecision:
    """One human review decision to apply to a relation."""

    figure_id: str
    relation_key: str
    action: ReviewAction
    corrected_entities: list[str] = field(default_factory=list)
    reviewer_note: str = ""
    applied_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "relation_key": self.relation_key,
            "action": self.action.value,
            "corrected_entities": list(self.corrected_entities),
            "reviewer_note": self.reviewer_note,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewDecision:
        return cls(
            figure_id=str(data["figure_id"]),
            relation_key=str(data["relation_key"]),
            action=ReviewAction(str(data["action"])),
            corrected_entities=[str(e) for e in data.get("corrected_entities") or []],
            reviewer_note=str(data.get("reviewer_note") or ""),
            applied_at=str(data.get("applied_at") or ""),
        )


class ReviewStateStore:
    """Loads, applies, and persists human review decisions."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        *,
        queue_path: Path | str | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        self._workspace = workspace
        self._queue_path = (
            Path(queue_path)
            if queue_path
            else (workspace.review_dir / "geometry_figures.json")
        )
        self._state_path = (
            Path(state_path)
            if state_path
            else (workspace.review_dir / "review_state.json")
        )

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    def load_queue(self) -> list[GeometryFigure]:
        if not self._queue_path.is_file():
            return []
        data = json.loads(self._queue_path.read_text(encoding="utf-8"))
        return [GeometryFigure.from_dict(f) for f in data.get("figures") or []]

    def load_state(self) -> list[ReviewDecision]:
        if not self._state_path.is_file():
            return []
        data = json.loads(self._state_path.read_text(encoding="utf-8"))
        return [ReviewDecision.from_dict(d) for d in data.get("decisions") or []]

    def queue_index(self) -> dict[str, GeometryFigure]:
        return {f.figure_id: f for f in self.load_queue()}

    # ------------------------------------------------------------------ #
    # Apply
    # ------------------------------------------------------------------ #

    def apply(
        self,
        decisions: list[ReviewDecision],
    ) -> list[ReviewDecision]:
        """Apply decisions to the on-disk queue and state files.

        Decisions are processed in order.  Later decisions targeting
        the same ``(figure_id, relation_key)`` pair override earlier
        ones.  Returns the persisted list of decisions.
        """
        if not self._queue_path.is_file():
            raise FileNotFoundError(
                f"Stage 5 output missing: {self._queue_path}. "
                "Run analyze_geometry first."
            )
        figures = self.load_queue()
        by_id = {f.figure_id: f for f in figures}

        applied: list[ReviewDecision] = []
        now = _now()
        for d in decisions:
            figure = by_id.get(d.figure_id)
            if figure is None:
                raise PromotionError(
                    f"unknown figure_id: {d.figure_id!r}"
                )
            relation = figure.relation(d.relation_key)
            if relation is None:
                raise PromotionError(
                    f"unknown relation_key {d.relation_key!r} on "
                    f"figure {d.figure_id!r}"
                )
            new_state = _ACTION_TO_STATE[d.action]
            self._enforce_promotion(relation.evidence, new_state, d.action)
            relation.review_state = new_state
            if d.corrected_entities:
                relation.entities = list(d.corrected_entities)
            if d.reviewer_note:
                relation.review_note = d.reviewer_note
            d.applied_at = now
            applied.append(d)

        # Persist the queue with updated review states.
        payload = {
            "schema_version": "geometry_figures/v1",
            "project_id": self._workspace.root.name,
            "generated_at": now,
            "figures": [f.to_dict() for f in figures],
        }
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._queue_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Append/replace decisions in the audit log.
        existing = self.load_state()
        latest = {(d.figure_id, d.relation_key): d for d in existing}
        for d in applied:
            latest[(d.figure_id, d.relation_key)] = d
        ordered = sorted(
            latest.values(), key=lambda d: (d.figure_id, d.relation_key)
        )
        state_payload = {
            "schema_version": "review_state/v1",
            "project_id": self._workspace.root.name,
            "updated_at": now,
            "decisions": [d.to_dict() for d in ordered],
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(state_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ordered

    @staticmethod
    def _enforce_promotion(
        evidence: Evidence, new_state: ReviewState, action: ReviewAction
    ) -> None:
        if new_state != ReviewState.CONFIRMED:
            return
        if evidence in NON_PROMOTABLE_EVIDENCE:
            raise PromotionError(
                f"cannot {action.value} a {evidence.value!r} relation: "
                "only problem_text, diagram_mark, and "
                "problem_text_and_diagram_mark may be auto-confirmed"
            )


# ---------------------------------------------------------------------- #
# Pipeline glue
# ---------------------------------------------------------------------- #


def apply_review(
    workspace: ProjectWorkspace,
    decisions: list[ReviewDecision] | None = None,
    *,
    state_path: Path | str | None = None,
    queue_path: Path | str | None = None,
) -> tuple[list[ReviewDecision], dict[str, int]]:
    """Apply *decisions* and record Stage 6 in the manifest.

    Returns the persisted decision list and a per-status count map.
    """
    store = ReviewStateStore(
        workspace, queue_path=queue_path, state_path=state_path
    )
    applied = store.apply(decisions or [])

    # Build a per-state count of the queue.
    counts: dict[str, int] = {}
    for f in store.load_queue():
        for rel in f.relations:
            counts[rel.review_state.value] = (
                counts.get(rel.review_state.value, 0) + 1
            )

    record_stage(
        workspace,
        "stage6_review",
        status=StageStatus.COMPLETED,
        input_fingerprint=_sha256_file(store._queue_path)
        if store._queue_path.is_file()
        else "",
        output_fingerprint=_sha256_file(store._state_path)
        if store._state_path.is_file()
        else "",
        metadata={
            "queue_path": str(store._queue_path.relative_to(workspace.root)),
            "state_path": str(store._state_path.relative_to(workspace.root)),
            "decisions_applied": len(applied),
            "relation_state_counts": counts,
        },
    )
    return applied, counts


def load_review_state(
    workspace: ProjectWorkspace,
) -> list[ReviewDecision]:
    """Read-only helper used by callers that just want the audit log."""
    return ReviewStateStore(workspace).load_state()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "PromotionError",
    "ReviewAction",
    "ReviewDecision",
    "ReviewStateStore",
    "apply_review",
    "load_review_state",
]
