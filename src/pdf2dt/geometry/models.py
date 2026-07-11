"""Geometry data classes — wire format for Stages 5/6 and the
exporter.

The dataclasses in this module match
``schemas/geometry-item.schema.json``.  The renderer and review
state both load JSON produced by the analyzer, so the
``to_dict`` / ``from_dict`` round-trip is the public contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .evidence import Evidence, ReviewState


class RelationType(str, Enum):
    """Closed vocabulary of supported geometry relations.

    A new relation type is added by appending to this enum and
    updating the analyzer's rule table.  The renderer is type
    agnostic — it only cares about evidence and review state — so
    new types do not require renderer changes.
    """

    POINT_ON_SEGMENT = "point_on_segment"
    MIDPOINT = "midpoint"
    COLLINEAR = "collinear"
    PARALLEL = "parallel"
    PERPENDICULAR = "perpendicular"
    EQUAL_LENGTH = "equal_length"
    EQUAL_ANGLE = "equal_angle"


def relation_key(relation_type: RelationType | str, entities: list[str]) -> str:
    """Stable string key for a relation.

    The key is type + case-folded, sorted entity tokens joined by
    ``+``.  The case-fold makes ``AB`` and ``ab`` map to the same
    key; the sort is stable so the analyzer's canonical ordering
    is preserved when two entities compare equal.
    """
    rtype = (
        relation_type.value
        if isinstance(relation_type, RelationType)
        else str(relation_type)
    )
    parts = [str(e) for e in entities if e]
    folded = [(s.casefold(), s) for s in parts]
    folded_sorted = sorted(folded, key=lambda t: t[0])
    parts_sorted = [original for _, original in folded_sorted]
    return f"{rtype}::" + "+".join(p.casefold() for p in parts_sorted)


@dataclass
class GeometryRelation:
    """One typed relation between one or more entities."""

    type: RelationType
    entities: list[str]
    evidence: Evidence
    source_reference: str = ""
    confidence: float = 0.0
    review_state: ReviewState = ReviewState.UNREVIEWED
    review_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "entities": list(self.entities),
            "evidence": self.evidence.value,
            "source_reference": self.source_reference,
            "confidence": round(float(self.confidence), 4),
            "review_state": self.review_state.value,
            "review_note": self.review_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeometryRelation:
        return cls(
            type=RelationType(str(data["type"])),
            entities=[str(e) for e in data.get("entities") or []],
            evidence=Evidence(str(data.get("evidence") or "unknown")),
            source_reference=str(data.get("source_reference") or ""),
            confidence=float(data.get("confidence") or 0.0),
            review_state=ReviewState(
                str(data.get("review_state") or "unreviewed")
            ),
            review_note=str(data.get("review_note") or ""),
        )

    @property
    def key(self) -> str:
        return relation_key(self.type, self.entities)


@dataclass
class GeometryFigure:
    """One figure and its structured interpretation."""

    figure_id: str
    asset_id: str
    associated_item_id: str = ""
    points: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    relations: list[GeometryRelation] = field(default_factory=list)
    visual_observations: list[str] = field(default_factory=list)
    review_state: ReviewState = ReviewState.UNREVIEWED

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "asset_id": self.asset_id,
            "associated_item_id": self.associated_item_id,
            "points": list(self.points),
            "segments": list(self.segments),
            "relations": [r.to_dict() for r in self.relations],
            "visual_observations": list(self.visual_observations),
            "review_state": self.review_state.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeometryFigure:
        return cls(
            figure_id=str(data["figure_id"]),
            asset_id=str(data["asset_id"]),
            associated_item_id=str(data.get("associated_item_id") or ""),
            points=[str(p) for p in data.get("points") or []],
            segments=[str(s) for s in data.get("segments") or []],
            relations=[
                GeometryRelation.from_dict(r)
                for r in data.get("relations") or []
            ],
            visual_observations=[
                str(v) for v in data.get("visual_observations") or []
            ],
            review_state=ReviewState(
                str(data.get("review_state") or "unreviewed")
            ),
        )

    def relation(self, key: str) -> GeometryRelation | None:
        for r in self.relations:
            if r.key == key:
                return r
        return None

    def relations_by_review(
        self, *states: ReviewState
    ) -> list[GeometryRelation]:
        wanted = set(states)
        return [r for r in self.relations if r.review_state in wanted]


__all__ = [
    "GeometryFigure",
    "GeometryRelation",
    "RelationType",
    "relation_key",
]
