"""Stage 4c export planner.

Inputs:
* ``book_view/book_view.json`` — the canonical BookView produced by
  Stage 3.
* ``topic_assignments/assignments.json`` — optional but already merged
  into the BookView by Stage 3.

Outputs:
* ``export_plans/plans.json`` — list of :class:`ExportPlan` records.
* Project manifest entry ``stage4c_export_plan``.

Behavior:

* One plan per outline leaf topic (including ``_misc`` as a fallback).
* Items that match multiple topics appear in multiple plans; their
  assets are deduplicated by ``asset_id`` within each export.
* Plan ordering follows the requested reorganization mode:

  * Mode A — original source order.
  * Mode B — topic cluster order: definitions → theorems → methods →
    worked examples → exercises → solutions → summaries → other.
  * Mode C — same deterministic item ordering as Mode B; in
    addition, the planner asks a :class:`BridgeProvider` to write
    one transition paragraph between every pair of adjacent
    plans. Default provider is :class:`MockBridgeProvider` (no
    external LLM required). Real LLM provider slots are open
    for v2 and live in :mod:`pdf2dt.export.bridges`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..outlining import OutlineLoader
from ..project import ProjectWorkspace, StageStatus, record_stage
from .bridges import (
    Bridge,
    BridgeContext,
    BridgeProvider,
    DEFAULT_BRIDGE_PROVIDER,
    resolve_bridge_provider,
)


class PlanError(ValueError):
    """Raised when export planning fails due to malformed input."""


class ReorgMode(str, Enum):
    A = "A"
    B = "B"
    C = "C"


# Mode-B priority: lower numbers appear first in the export.
_MODE_B_PRIORITY = {
    "chapter": 0,
    "section": 1,
    "definition": 10,
    "theorem": 11,
    "method": 20,
    "knowledge_point": 25,
    "example": 30,
    "worked_example": 30,
    "exercise": 40,
    "solution": 50,
    "summary": 60,
    "chapter_summary": 60,
    "other": 70,
}


def _sort_mode_a(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Source order is implicit: use item_id numeric suffix.
    def _idx(item: dict[str, Any]) -> int:
        sid = item.get("item_id", "")
        m = re.search(r"\d+", sid)
        return int(m.group()) if m else 10**9

    return sorted(items, key=_idx)


def _sort_mode_b(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(item: dict[str, Any]) -> tuple[int, int]:
        itype = item.get("item_type") or "other"
        priority = _MODE_B_PRIORITY.get(itype, 70)
        sid = item.get("item_id", "")
        m = re.search(r"\d+", sid)
        order = int(m.group()) if m else 10**9
        return (priority, order)

    return sorted(items, key=_key)


def _sort_items(items: list[dict[str, Any]], mode: ReorgMode) -> list[dict[str, Any]]:
    if mode == ReorgMode.A:
        return _sort_mode_a(items)
    # Mode B and C share the same deterministic heuristic here.
    return _sort_mode_b(items)


@dataclass
class ExportPlan:
    """One planned PDF export document."""

    plan_id: str
    topic_id: str | None
    title: str
    mode: ReorgMode
    items: list[dict[str, Any]]
    figure_ids: list[str]
    output_filename: str
    outline_used: dict[str, Any] | None
    is_misc_fallback: bool
    unclassified_count: int = 0
    bridges: list[Bridge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "topic_id": self.topic_id,
            "title": self.title,
            "mode": self.mode.value,
            "items": list(self.items),
            "figure_ids": list(self.figure_ids),
            "output_filename": self.output_filename,
            "outline_used": self.outline_used,
            "is_misc_fallback": self.is_misc_fallback,
            "unclassified_count": self.unclassified_count,
            "bridges": [
                {
                    "text": b.text,
                    "follows_plan_id": b.follows_plan_id,
                    "follows_topic_id": b.follows_topic_id,
                    "provider": b.provider,
                    "metadata": b.metadata,
                }
                for b in self.bridges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExportPlan":
        bridges_raw = data.get("bridges") or []
        bridges = [
            Bridge(
                text=str(b["text"]),
                follows_plan_id=b.get("follows_plan_id"),
                follows_topic_id=b.get("follows_topic_id"),
                provider=str(b.get("provider") or "mock"),
                metadata=dict(b.get("metadata") or {}),
            )
            for b in bridges_raw
        ]
        return cls(
            plan_id=str(data["plan_id"]),
            topic_id=data.get("topic_id"),
            title=str(data["title"]),
            mode=ReorgMode(str(data.get("mode") or "B")),
            items=list(data.get("items") or []),
            figure_ids=list(data.get("figure_ids") or []),
            output_filename=str(data["output_filename"]),
            outline_used=data.get("outline_used"),
            is_misc_fallback=bool(data.get("is_misc_fallback")),
            unclassified_count=int(data.get("unclassified_count") or 0),
            bridges=bridges,
        )


@dataclass
class ExportPlanCollection:
    """Container for all plans plus provenance."""

    project_id: str
    generated_at: str
    mode: ReorgMode
    outline_used: dict[str, Any] | None
    plans: list[ExportPlan]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "export_plan/v1",
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "mode": self.mode.value,
            "outline_used": self.outline_used,
            "plans": [p.to_dict() for p in self.plans],
        }


class ExportPlanner:
    """Plan topic-oriented exports from a BookView JSON document."""

    MISC_TOPIC = "_misc"

    def __init__(
        self,
        book_view: dict[str, Any],
        *,
        mode: ReorgMode = ReorgMode.B,
        project_id: str = "book",
        outline_provenance: dict[str, Any] | None = None,
        bridge_provider: BridgeProvider | str | None = None,
    ) -> None:
        self._book = book_view
        self._mode = mode
        self._project_id = project_id
        self._outline = outline_provenance
        # Resolve the provider eagerly so a misconfigured name
        # fails loudly at construction time rather than mid-plan().
        if bridge_provider is None or isinstance(bridge_provider, BridgeProvider):
            self._bridge_provider: BridgeProvider = (
                bridge_provider if isinstance(bridge_provider, BridgeProvider)
                else DEFAULT_BRIDGE_PROVIDER
            )
        else:
            self._bridge_provider = resolve_bridge_provider(bridge_provider)

    @property
    def bridge_provider(self) -> BridgeProvider:
        return self._bridge_provider

    def plan(self) -> ExportPlanCollection:
        items = self._collect_items()
        by_topic: dict[str, list[dict[str, Any]]] = {}
        misc_items: list[dict[str, Any]] = []

        for item in items:
            topic_ids = list(item.get("topic_ids") or [])
            if not topic_ids or topic_ids == [self.MISC_TOPIC]:
                misc_items.append(item)
                continue
            for tid in topic_ids:
                if tid == self.MISC_TOPIC:
                    continue
                by_topic.setdefault(tid, []).append(item)

        plans: list[ExportPlan] = []
        topic_order = sorted(by_topic.keys())
        for seq, tid in enumerate(topic_order, start=1):
            plan_items = _sort_items(by_topic[tid], self._mode)
            plan = self._build_plan(
                topic_id=tid,
                items=plan_items,
                sequence=seq,
                is_misc=False,
            )
            plans.append(plan)

        if misc_items:
            plans.append(
                self._build_plan(
                    topic_id=self.MISC_TOPIC,
                    items=_sort_items(misc_items, self._mode),
                    sequence=len(plans) + 1,
                    is_misc=True,
                )
            )

        # When no outline is supplied and no topic grouping exists,
        # plan a single chapter-style export covering all items.
        if not plans:
            plans.append(
                self._build_plan(
                    topic_id=None,
                    items=_sort_items(items, ReorgMode.A),
                    sequence=1,
                    is_misc=False,
                )
            )

        # Mode C: ask the bridge provider to write a transition
        # between every adjacent pair of plans. Failures or `None`
        # returns are non-fatal; we just skip insertion for that
        # pair so Mode C keeps degrading gracefully into Mode B.
        if self._mode == ReorgMode.C and len(plans) > 1:
            self._attach_bridges(plans)

        return ExportPlanCollection(
            project_id=self._project_id,
            generated_at=_now(),
            mode=self._mode,
            outline_used=self._outline,
            plans=plans,
        )

    def _attach_bridges(self, plans: list["ExportPlan"]) -> None:
        """For every (i, i+1) pair, ask the bridge provider to
        produce a transition paragraph and append it to ``plans[i+1]``.

        ``plans[0]`` is intentionally *not* bridged — it is the
        opening of the collection and has no predecessor.
        """
        for prev, curr in zip(plans, plans[1:]):
            context = BridgeContext(
                follows_plan_id=prev.plan_id,
                follows_topic_id=prev.topic_id,
                follows_title=prev.title,
                follows_item_count=len(prev.items),
                next_plan_id=curr.plan_id,
                next_topic_id=curr.topic_id,
                next_title=curr.title,
                next_item_count=len(curr.items),
            )
            try:
                bridge = self._bridge_provider.generate_bridge(context)
            except Exception as exc:  # noqa: BLE001 - policy: bridges fail-soft
                # Persist the failure in metadata, not as a hard
                # error, so a flaky provider can never block export.
                curr.bridges.append(
                    Bridge(
                        text=(
                            f"[bridge-error] bridge provider "
                            f"{self._bridge_provider.name!r} raised: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        follows_plan_id=prev.plan_id,
                        follows_topic_id=prev.topic_id,
                        provider=f"{self._bridge_provider.name}-error",
                        metadata={"error_type": type(exc).__name__},
                    )
                )
                continue
            if bridge is not None:
                curr.bridges.append(bridge)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _collect_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for chapter in self._book.get("chapters") or []:
            for section in chapter.get("sections") or []:
                for item in section.get("items") or []:
                    items.append(item)
        return items

    def _build_plan(
        self,
        *,
        topic_id: str | None,
        items: list[dict[str, Any]],
        sequence: int,
        is_misc: bool,
    ) -> ExportPlan:
        figure_ids: list[str] = []
        seen: set[str] = set()
        for item in items:
            for asset in item.get("asset_refs") or []:
                aid = asset.get("asset_id")
                if aid and aid not in seen:
                    seen.add(aid)
                    figure_ids.append(aid)

        title = self._plan_title(topic_id, items)
        filename = self._plan_filename(topic_id, sequence)
        plan_id = self._plan_id(topic_id, sequence)
        return ExportPlan(
            plan_id=plan_id,
            topic_id=topic_id,
            title=title,
            mode=self._mode,
            items=items,
            figure_ids=figure_ids,
            output_filename=filename,
            outline_used=self._outline,
            is_misc_fallback=is_misc,
            unclassified_count=len(items) if is_misc else 0,
        )

    def _plan_title(self, topic_id: str | None, items: list[dict[str, Any]]) -> str:
        if topic_id is None:
            return f"{self._project_id} — full export"
        if topic_id == self.MISC_TOPIC:
            return f"{self._project_id} — 未分类内容 ({len(items)} items)"
        return f"{self._project_id} — {topic_id}"

    def _plan_filename(self, topic_id: str | None, sequence: int) -> str:
        safe_topic = "full" if topic_id is None else topic_id
        safe_topic = re.sub(r"[^a-zA-Z0-9_\-]+", "-", safe_topic).strip("-")
        return f"{self._project_id}-{safe_topic}-{sequence:03d}.pdf"

    def _plan_id(self, topic_id: str | None, sequence: int) -> str:
        base = topic_id if topic_id is not None else "full"
        return f"plan-{base}-{sequence:03d}"


# ---------------------------------------------------------------------- #
# Pipeline glue
# ---------------------------------------------------------------------- #


def plan_exports(
    workspace: ProjectWorkspace,
    *,
    mode: ReorgMode | str = ReorgMode.B,
    book_view_path: Path | str | None = None,
    outline_path: Path | str | None = None,
    bridge_provider: BridgeProvider | str | None = None,
) -> ExportPlanCollection:
    """Run Stage 4c and persist the export plan collection."""
    mode_enum = ReorgMode(str(mode)) if not isinstance(mode, ReorgMode) else mode

    bv_path = Path(book_view_path) if book_view_path else (workspace.book_view_dir / "book_view.json")
    if not bv_path.is_file():
        raise PlanError(f"BookView not found: {bv_path}")
    book = json.loads(bv_path.read_text(encoding="utf-8"))

    outline_provenance: dict[str, Any] | None = None
    # When an outline path is supplied, load it through the canonical
    # OutlineLoader so we get the real outline_id, version, and sha256
    # rather than guessing or hard-coding a default. This keeps the
    # PDF footer in sync with whatever the on-disk outline actually is.
    if outline_path is not None and Path(outline_path).is_file():
        outline = OutlineLoader().load(Path(outline_path))
        outline_provenance = {
            "outline_id": outline.outline_id,
            "version": outline.version,
            "sha256": outline.sha256,
        }
    # Prefer outline provenance stored inside the BookView fingerprints.
    if book.get("fingerprints", {}).get("assignments"):
        # The assignments file contains the outline_id and version.
        assignments_path = workspace.topic_assignments_dir / "assignments.json"
        if assignments_path.is_file():
            try:
                a_data = json.loads(assignments_path.read_text(encoding="utf-8"))
                # assignments.json keys are ``outline_id`` /
                # ``outline_version`` / ``outline_sha256``. (Earlier
                # code read ``version`` and silently fell back to
                # ``"1.0.0"``, which is why the PDF footer stayed
                # stuck on v1.0.0 even when the outline on disk was
                # bumped.)
                outline_provenance = {
                    "outline_id": a_data.get("outline_id", "unknown"),
                    "version": a_data.get("outline_version", "0.0.0"),
                    "sha256": a_data.get("outline_sha256", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass

    planner = ExportPlanner(
        book,
        mode=mode_enum,
        project_id=workspace.root.name,
        outline_provenance=outline_provenance,
        bridge_provider=bridge_provider,
    )
    collection = planner.plan()

    out_dir = workspace.export_plans_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "plans.json"
    out_path.write_text(
        json.dumps(collection.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    record_stage(
        workspace,
        "stage4c_export_plan",
        status=StageStatus.COMPLETED,
        input_fingerprint=hashlib.sha256(bv_path.read_bytes()).hexdigest(),
        output_fingerprint=hashlib.sha256(out_path.read_bytes()).hexdigest(),
        metadata={
            "mode": collection.mode.value,
            "plans": len(collection.plans),
            "total_items": sum(len(p.items) for p in collection.plans),
            "plans_path": str(out_path.relative_to(workspace.root)),
            "outline_used": collection.outline_used,
        },
    )
    return collection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
