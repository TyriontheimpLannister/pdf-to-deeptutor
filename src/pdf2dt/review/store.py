"""Review state store — Stage 6.

The store is small and side-effect free.  The on-disk layout is:

* ``review/geometry_figures.json`` — produced by Stage 5; the
  store *edits* this file in place when decisions are applied.
* ``review/review_state.json`` — the lightweight record of every
  decision, including the ``reviewer_note`` and ``applied_at``
  timestamp.
* ``review/figure_role_overrides.json`` — explicit overrides for
  figure role classifications.  This is a separate file so that
  geometry review state and figure role overrides can be reset
  independently.

The two-file (now three-file) design means the user can wipe the
audit log (``review_state.json``) without losing the geometry
queue, and reset role overrides (``figure_role_overrides.json``)
without touching the geometry decisions.
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


# ---------------------------------------------------------------------- #
# Figure role overrides (independent of geometry relations)
# ---------------------------------------------------------------------- #


_OVERRIDES_FILENAME = "figure_role_overrides.json"
_OVERRIDES_SCHEMA = "figure_role_overrides/v1"
_VALID_ROLES = {"content", "decor", "ambiguous"}


@dataclass
class FigureRoleDecision:
    """One explicit human override for a figure's role.

    A role decision does not need a corresponding relation key —
    it is a property of the figure itself.  Decisions are written
    to ``review/figure_role_overrides.json`` so they can be reset
    without touching geometry decisions.
    """

    figure_id: str
    role: str
    reviewer_note: str = ""
    applied_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "role": self.role,
            "reviewer_note": self.reviewer_note,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FigureRoleDecision:
        role = str(data.get("role") or "ambiguous")
        if role not in _VALID_ROLES:
            role = "ambiguous"
        return cls(
            figure_id=str(data.get("figure_id") or ""),
            role=role,
            reviewer_note=str(data.get("reviewer_note") or ""),
            applied_at=str(data.get("applied_at") or ""),
        )


class FigureRoleOverrideStore:
    """Persist figure role overrides."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        *,
        path: Path | str | None = None,
    ) -> None:
        self._workspace = workspace
        self._path = (
            Path(path)
            if path
            else (workspace.review_dir / _OVERRIDES_FILENAME)
        )

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[FigureRoleDecision]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [
            FigureRoleDecision.from_dict(d) for d in data.get("decisions") or []
        ]

    def index(self) -> dict[str, FigureRoleDecision]:
        return {d.figure_id: d for d in self.load()}

    def save(self, decisions: list[FigureRoleDecision]) -> list[FigureRoleDecision]:
        ordered = sorted(decisions, key=lambda d: d.figure_id)
        now = _now()
        for d in ordered:
            if not d.applied_at:
                d.applied_at = now
        payload = {
            "schema_version": _OVERRIDES_SCHEMA,
            "project_id": self._workspace.root.name,
            "updated_at": now,
            "decisions": [d.to_dict() for d in ordered],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ordered


def apply_figure_role_overrides(
    workspace: ProjectWorkspace,
    decisions: list[FigureRoleDecision] | None = None,
    *,
    path: Path | str | None = None,
) -> list[FigureRoleDecision]:
    """Apply *decisions* and persist them to the overrides file.

    Returns the persisted list of decisions, sorted by ``figure_id``.
    Raises :class:`PromotionError` if any decision targets an unknown
    role value.  Resets the figure role store only if the user
    explicitly supplies a non-empty list — calling with no decisions
    is a no-op.
    """
    store = FigureRoleOverrideStore(workspace, path=path)
    existing = store.load()
    if not decisions:
        return existing
    by_id = {d.figure_id: d for d in existing}
    now = _now()
    for d in decisions:
        if d.role not in _VALID_ROLES:
            raise PromotionError(
                f"unknown figure role override: {d.role!r}"
            )
        if not d.figure_id:
            raise PromotionError("figure_id is required for a role override")
        d.applied_at = now
        by_id[d.figure_id] = d
    return store.save(list(by_id.values()))


__all__ = [
    "PromotionError",
    "ReviewAction",
    "ReviewDecision",
    "ReviewStateStore",
    "apply_review",
    "load_review_state",
    "FigureRoleDecision",
    "FigureRoleOverrideStore",
    "apply_figure_role_overrides",
]
