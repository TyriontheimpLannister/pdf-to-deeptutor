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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..outlining import Outline, OutlineLoader
from ..project import ProjectWorkspace, StageStatus, record_stage
from ..review import FigureRoleOverrideStore
from ..review.figure_roles import FigureRole, FigureRoleStore
from .bridges import (
    DEFAULT_BRIDGE_PROVIDER,
    Bridge,
    BridgeContext,
    BridgeProvider,
    BridgeProviderContext,
    PlanAccessor,
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


# Match ``![<alt>](<path-or-url>)`` markers as emitted by MinerU's
# markdown export (and any markdown we normalize to inline).
_INLINE_IMAGE_MARKER_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# Per-process cache of basename → asset_id indices, keyed by the
# absolute registry path. The Stage 4c back-fill is called once per
# plan run; caching avoids re-parsing the same JSON repeatedly when
# the planner is reinstantiated (eg. in a test loop).
_REGISTRY_BASENAME_INDEX: dict[str, dict[str, str]] = {}


def _basename(path_or_url: str) -> str:
    """Return the final path segment with any URL query/fragment stripped.

    Markdown image targets can be either local paths
    (``assets/<hash>.jpg``) or remote URLs (``https://.../<hash>.jpg``).
    For the Stage 4c inline back-fill we key purely by the filename so
    inline markers resolve against the asset registry regardless of
    whether the path is relative, absolute, or a CDN URL.

    Supports both ``/`` and Windows-style ``\\\\`` separators in
    ``local_path`` registry entries: the registry normalises to
    forward slashes on POSIX but keeps backslashes on Windows, and a
    hashed filename is the only useful join key across both.
    """
    p = path_or_url.split("?", 1)[0].split("#", 1)[0].strip()
    if not p:
        return ""
    # Normalise backslashes to forward slashes before splitting so
    # Windows-style registry paths are handled identically to POSIX ones.
    p = p.replace("\\", "/")
    return p.rsplit("/", 1)[-1]  # type: ignore[no-any-return]


def _load_assets_basename_index(registry_path: Path) -> dict[str, str]:
    """Build ``<basename> -> asset_id`` for an assets registry JSON file.

    The registry schema is ``registry/v1`` with an ``assets`` list whose
    records carry ``asset_id`` and ``local_path`` (workspace-relative,
    backslash-separated on Windows). We index by the final path segment
    so an inline ``![image](assets/<hash>.jpg)`` marker can be turned
    into the right ``asset_id`` even when MinerU recorded the figure's
    source under a different URL.
    """
    cache_key = str(registry_path)
    cached = _REGISTRY_BASENAME_INDEX.get(cache_key)
    if cached is not None:
        return cached
    if not registry_path.is_file():
        index: dict[str, str] = {}
        _REGISTRY_BASENAME_INDEX[cache_key] = index
        return index
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        index = {}
        _REGISTRY_BASENAME_INDEX[cache_key] = index
        return index
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        index = {}
        _REGISTRY_BASENAME_INDEX[cache_key] = index
        return index
    index = {}
    for a in assets:
        if not isinstance(a, dict):
            continue
        aid = a.get("asset_id")
        local = a.get("local_path")
        if not aid or not local:
            continue
        bname = _basename(str(local))
        if bname and bname not in index:
            index[bname] = str(aid)
    _REGISTRY_BASENAME_INDEX[cache_key] = index
    return index


def backfill_inline_asset_refs(
    book: dict[str, Any],
    basename_index: dict[str, str],
) -> dict[str, int]:
    """Back-fill ``items[].asset_refs`` from inline image markers.

    Stage 3's bookview builder gathers ``asset_refs`` by walking the
    MinerU layout blocks and joining figure blocks to each item. Items
    whose only figures sit *inline* in their Markdown body — common for
    worked examples and exercises — never receive a layout block-level
    hit, so ``asset_refs`` arrives empty and the export planner /
    Stage 7 renderer never see those figures.

    This pass scans every item's ``text`` for ``![image](<local>)``
    markers, resolves the basename through the assets registry, and
    appends any not-already-present asset to ``asset_refs``. Existing
    layout-derived refs are preserved; back-filled records are tagged
    ``origin: "inline_marker"`` so future consumers can distinguish the
    two provenance paths.

    Returns a small audit dict (``items_touched``, ``refs_added``,
    ``markers_missing``, ``missing_examples``).
    """
    if not basename_index:
        # No registry means we cannot resolve any inline target — leave
        # book untouched and let downstream layers decide.
        return {
            "items_touched": 0,
            "refs_added": 0,
            "markers_missing": 0,
            "missing_examples": [],
        }
    items_touched = 0
    refs_added = 0
    markers_missing = 0
    missing_examples: list[str] = []

    def _walk(items: Any) -> None:
        nonlocal items_touched, refs_added, markers_missing
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            text = it.get("text") or ""
            if "!" not in text or "(" not in text:
                continue
            existing_ids = {
                str(r.get("asset_id"))
                for r in (it.get("asset_refs") or [])
                if isinstance(r, dict) and r.get("asset_id")
            }
            added_for_item = 0
            for m in _INLINE_IMAGE_MARKER_RE.finditer(text):
                target = (m.group(1) or "").strip()
                if not target:
                    continue
                # Skip remote URLs — the local registry cannot resolve
                # them and the renderer will not be able to embed them
                # offline either. Surface them as missing so the
                # planner audit log flags the gap.
                if target.startswith(("http://", "https://")):
                    markers_missing += 1
                    if len(missing_examples) < 10:
                        missing_examples.append(f"{it.get('item_id')} -> {target}")
                    continue
                bname = _basename(target)
                if not bname:
                    continue
                aid = basename_index.get(bname)
                if not aid:
                    markers_missing += 1
                    if len(missing_examples) < 10:
                        missing_examples.append(f"{it.get('item_id')} -> {bname}")
                    continue
                if aid in existing_ids:
                    continue
                refs = it.get("asset_refs")
                if refs is None:
                    refs = []
                    it["asset_refs"] = refs
                elif not isinstance(refs, list):
                    refs = []
                    it["asset_refs"] = refs
                refs.append(
                    {
                        "asset_id": aid,
                        "figure_id": aid,
                        "origin": "inline_marker",
                    }
                )
                existing_ids.add(aid)
                refs_added += 1
                added_for_item += 1
            if added_for_item:
                items_touched += 1

    chapters = book.get("chapters") or []
    if isinstance(chapters, list):
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            sections = chapter.get("sections") or []
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                _walk(section.get("items") or [])
    # Some BookView shapes also carry a top-level ``items`` list.
    _walk(book.get("items") or [])

    return {
        "items_touched": items_touched,
        "refs_added": refs_added,
        "markers_missing": markers_missing,
        "missing_examples": missing_examples,
    }


# Defence-in-depth: drop chapter-type items from plans.
#
# Chapter items carry the chapter-opening narration ("chapter 17:
# 立体图形认知 — watch the magic trick…") plus a banner figure that
# has nothing to do with most of the topics the outline matcher
# also tags the item with. Without this filter the same opening
# paragraph and banner image end up on the first page of every
# plan whose outline keyword matches even a single word in that
# paragraph (e.g. "正方体" matches both ``geometry-plane-quads``
# and ``geometry-solid-cube``), producing visibly unrelated
# content in the export PDF.
#
# Section-level items are kept — they carry the actual worked
# examples, exercises, and review content.
_INTRO_ITEM_TYPES: frozenset[str] = frozenset({"chapter"})


def is_intro_item(item: Any) -> bool:
    """Return True for items the planner should exclude from a plan.

    Matches ``item_type == 'chapter'``. See the comment above
    :data:`_INTRO_ITEM_TYPES` for the rationale.
    """
    if not isinstance(item, dict):
        return False
    return str(item.get("item_type") or "").strip().lower() in _INTRO_ITEM_TYPES


def _load_content_figure_ids(workspace: ProjectWorkspace) -> set[str]:
    """Return locally reviewed figure/asset IDs that must survive filtering."""
    records = FigureRoleStore(workspace).load()
    ids: set[str] = set()
    ids_by_figure: dict[str, set[str]] = {}
    for record in records:
        related = {value for value in (record.figure_id, record.asset_id) if value}
        ids_by_figure.setdefault(record.figure_id, set()).update(related)
        if record.role == FigureRole.CONTENT:
            ids.update(related)

    # Human overrides remain authoritative over persisted classifications.
    for decision in FigureRoleOverrideStore(workspace).load():
        related = ids_by_figure.get(decision.figure_id, {decision.figure_id})
        if decision.role == FigureRole.CONTENT.value:
            ids.update(related)
        elif decision.role == FigureRole.DECOR.value:
            ids.difference_update(related)
    return ids


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
    def from_dict(cls, data: dict[str, Any]) -> ExportPlan:
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
    dropped_plans: list[dict[str, Any]] = field(default_factory=list)
    topic_pruning: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "export_plan/v1",
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "mode": self.mode.value,
            "outline_used": self.outline_used,
            "plans": [p.to_dict() for p in self.plans],
            "dropped_plans": list(self.dropped_plans),
            "topic_pruning": list(self.topic_pruning),
        }


class ExportPlanner:
    """Plan topic-oriented exports from a BookView JSON document."""

    MISC_TOPIC = "_misc"

    def __init__(
        self,
        book_view: dict[str, Any],
        *,
        mode: ReorgMode = ReorgMode.B,
        force_mode: bool = False,
        project_id: str = "book",
        outline: Outline | None = None,
        outline_provenance: dict[str, Any] | None = None,
        bridge_provider: BridgeProvider | str | None = None,
        review_dir: Path | None = None,
        content_figure_ids: set[str] | None = None,
    ) -> None:
        self._book = book_view
        self._mode = mode
        self._force_mode = force_mode
        self._project_id = project_id
        self._outline_model = outline
        self._outline = outline_provenance
        self._review_dir = Path(review_dir) if review_dir is not None else None
        self._content_figure_ids = frozenset(content_figure_ids or ())
        self._topic_pruning: list[dict[str, Any]] = []
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
            topic_ids = self._topic_ids_for_export(item)
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
            plan_mode = self._effective_mode(tid)
            plan_items = _sort_items(by_topic[tid], plan_mode)
            plan = self._build_plan(
                topic_id=tid,
                items=plan_items,
                sequence=seq,
                is_misc=False,
                mode=plan_mode,
            )
            plans.append(plan)

        if misc_items:
            plans.append(
                self._build_plan(
                    topic_id=self.MISC_TOPIC,
                    items=_sort_items(misc_items, self._effective_mode(self.MISC_TOPIC)),
                    sequence=len(plans) + 1,
                    is_misc=True,
                    mode=self._effective_mode(self.MISC_TOPIC),
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
                    mode=self._mode,
                )
            )

        # Deduplicate: remove plans whose item sets are a proper subset
        # of another plan.  This happens when an item carries multiple
        # topic_ids and a finer-grained topic ends up with a strictly
        # smaller item set than a broader topic.
        plans, dropped = self._deduplicate_plans(plans)

        # A Mode-C plan asks for a transition from its immediate
        # predecessor. Failures or `None` returns are non-fatal.
        if len(plans) > 1:
            self._attach_bridges(plans)

        return ExportPlanCollection(
            project_id=self._project_id,
            generated_at=_now(),
            mode=self._mode,
            outline_used=self._outline,
            plans=plans,
            dropped_plans=dropped,
            topic_pruning=self._topic_pruning,
        )

    def _topic_ids_for_export(self, item: dict[str, Any]) -> list[str]:
        """Keep duplicate topic placements unless Stage 4b has one clear winner."""
        topic_ids = [
            topic_id
            for topic_id in item.get("topic_ids") or []
            if isinstance(topic_id, str)
        ]
        if len(topic_ids) < 2 or self.MISC_TOPIC in topic_ids:
            return topic_ids

        raw_scores = item.get("topic_match_scores")
        if not isinstance(raw_scores, dict):
            return topic_ids
        scores = {
            topic_id: raw_scores.get(topic_id)
            for topic_id in topic_ids
        }
        if any(not isinstance(score, int) or isinstance(score, bool) for score in scores.values()):
            return topic_ids

        highest_score = max(scores.values())
        winners = [topic_id for topic_id, score in scores.items() if score == highest_score]
        if len(winners) != 1:
            return topic_ids

        kept_topic_id = winners[0]
        removed_topic_ids = [topic_id for topic_id in topic_ids if topic_id != kept_topic_id]
        self._topic_pruning.append(
            {
                "item_id": str(item.get("item_id") or ""),
                "kept_topic_id": kept_topic_id,
                "removed_topic_ids": removed_topic_ids,
                "scores": scores,
                "reason": "unique_highest_match_score",
            }
        )
        return [kept_topic_id]

    def _deduplicate_plans(
        self, plans: list[ExportPlan]
    ) -> tuple[list[ExportPlan], list[dict[str, Any]]]:
        """Remove single-item plans whose item set is a proper subset
        of another plan.

        When an item carries multiple topic_ids, a fine-grained topic
        may end up with a single item that is also present in a broader
        topic.  The single-item plan is usually an introductory heading
        or overview and adds no value as a standalone export.

        Plans with more than one item are kept even if they are a
        proper subset, because a multi-item plan usually represents a
        coherent sub-topic that is worth preserving.

        Only *proper* subsets are dropped (``A < B``, not ``A == B``).
        When multiple superseding plans exist, the smallest one (by
        item count) is chosen so the user gets the tightest match.
        """
        plan_item_sets: dict[str, frozenset[str]] = {}
        for plan in plans:
            iids = frozenset(
                item.get("item_id") for item in plan.items if item.get("item_id")
            )
            plan_item_sets[plan.plan_id] = iids

        dropped: list[dict[str, Any]] = []
        keep: list[ExportPlan] = []

        for plan in plans:
            iids = plan_item_sets[plan.plan_id]
            # Only consider dropping single-item plans.
            if len(plan.items) > 1:
                keep.append(plan)
                continue

            superseded_by: ExportPlan | None = None
            for other in plans:
                if other.plan_id == plan.plan_id:
                    continue
                other_iids = plan_item_sets[other.plan_id]
                if iids < other_iids:
                    if superseded_by is None or len(other.items) < len(
                        superseded_by.items
                    ):
                        superseded_by = other

            if superseded_by is not None:
                dropped.append(
                    {
                        "plan_id": plan.plan_id,
                        "topic_id": plan.topic_id,
                        "item_count": len(plan.items),
                        "superseded_by_plan_id": superseded_by.plan_id,
                        "superseded_by_topic_id": superseded_by.topic_id,
                        "superseded_by_item_count": len(superseded_by.items),
                        "reason": "single_item_proper_subset",
                    }
                )
            else:
                keep.append(plan)

        return keep, dropped

    def _attach_bridges(self, plans: list[ExportPlan]) -> None:
        """For every (i, i+1) pair, ask the bridge provider to
        produce a transition paragraph and append it to ``plans[i+1]``.

        ``plans[0]`` is intentionally *not* bridged — it is the
        opening of the collection and has no predecessor.
        """
        # Build a one-time accessor view of every plan so providers
        # that need item-level data (asset_ids, items) can look it up
        # without re-walking the whole plan list. The accessors are
        # plain dicts / tuples so a provider never imports the
        # planner module.
        plans_by_id: dict[str, PlanAccessor] = {
            plan.plan_id: self._plan_accessor(plan) for plan in plans
        }
        self._invoke_attach(plans_by_id)

        for prev, curr in zip(plans, plans[1:], strict=False):
            if curr.mode != ReorgMode.C:
                continue
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

    def _invoke_attach(self, plans_by_id: dict[str, PlanAccessor]) -> None:
        """Call the provider's :meth:`attach_context` once per plan run.

        Failures here are non-fatal: a provider that fails to load
        its context falls back to the ``mock`` provider for the
        remaining bridges. This keeps export planning available
        even when geometry / outline files are temporarily
        unreadable.
        """
        try:
            self._bridge_provider.attach_context(
                BridgeProviderContext(
                    plans_by_id=plans_by_id,
                    review_dir=self._review_dir,
                    outline=self._outline_model,
                )
            )
        except Exception as exc:  # noqa: BLE001 - policy: bridges fail-soft
            self._bridge_provider = DEFAULT_BRIDGE_PROVIDER
            self._bridge_provider.attach_context(
                BridgeProviderContext(
                    plans_by_id=plans_by_id,
                    review_dir=self._review_dir,
                    outline=self._outline_model,
                )
            )
            self._attach_error = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]

    @staticmethod
    def _plan_accessor(plan: ExportPlan) -> PlanAccessor:
        asset_ids: list[str] = []
        for item in plan.items:
            for asset in item.get("asset_refs") or []:
                aid = asset.get("asset_id")
                if aid and aid not in asset_ids:
                    asset_ids.append(aid)
        return PlanAccessor(
            plan_id=plan.plan_id,
            topic_id=plan.topic_id,
            title=plan.title,
            item_count=len(plan.items),
            asset_ids=tuple(asset_ids),
            items=tuple(plan.items),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _collect_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for chapter in self._book.get("chapters") or []:
            for section in chapter.get("sections") or []:
                for item in section.get("items") or []:
                    items.append(item)
        # Defence-in-depth: drop text-noise items here too, so the
        # Stage 4b matcher-side filter and Stage 4c planner stay in
        # sync regardless of the order they run. The renderer's
        # _render_item also re-checks, so three independent layers
        # have to fail for a noise item to slip into a PDF.
        from pdf2dt.outlining import classify_noise  # noqa: PLC0415

        kept: list[dict[str, Any]] = []
        for item in items:
            noise = classify_noise(item)
            intro = is_intro_item(item)
            if not noise.is_noise and not intro:
                kept.append(item)
                continue

            asset_ids = {
                str(asset.get("figure_id") or asset.get("asset_id") or "")
                for asset in item.get("asset_refs") or []
                if isinstance(asset, dict)
            }
            asset_ids.update(
                str(asset.get("asset_id") or "")
                for asset in item.get("asset_refs") or []
                if isinstance(asset, dict)
            )
            if not (asset_ids & self._content_figure_ids):
                continue

            # A confirmed content figure is stronger evidence than the generic
            # text-noise/chapter-intro filters. Keep it exactly once in the
            # misc fallback instead of duplicating a chapter banner across all
            # topic plans. Noise titles are replaced so the renderer's
            # defence-in-depth filter does not discard the rescued item again.
            rescued = dict(item)
            rescued["topic_ids"] = [self.MISC_TOPIC]
            rescued["content_rescue_reason"] = (
                "content_figure_on_noise_item"
                if noise.is_noise
                else "content_figure_on_intro_item"
            )
            if noise.is_noise:
                original_title = str(item.get("title") or "").strip()
                body = str(item.get("text") or "")
                if original_title and body.startswith(original_title):
                    body = body[len(original_title) :].lstrip()
                page_refs = item.get("page_refs") or []
                label = (
                    f"内容图（源页 {page_refs[0]}）"
                    if page_refs
                    else f"内容图（{item.get('item_id') or '未编号'}）"
                )
                rescued["title"] = label
                rescued["text"] = body
            kept.append(rescued)
        return kept

    def _build_plan(
        self,
        *,
        topic_id: str | None,
        items: list[dict[str, Any]],
        sequence: int,
        is_misc: bool,
        mode: ReorgMode,
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
            mode=mode,
            items=items,
            figure_ids=figure_ids,
            output_filename=filename,
            outline_used=self._outline,
            is_misc_fallback=is_misc,
            unclassified_count=len(items) if is_misc else 0,
        )

    def _effective_mode(self, topic_id: str | None) -> ReorgMode:
        # An explicit CLI A/C request remains a collection-wide override
        # for compatibility. The default CLI B delegates to the outline's
        # per-topic strategy, including its default and overrides.
        # When force_mode is set (e.g. user explicitly passed --mode B),
        # honour the requested mode for every topic.
        if (
            self._force_mode
            or self._mode != ReorgMode.B
            or self._outline_model is None
            or topic_id is None
        ):
            return self._mode
        return ReorgMode(self._outline_model.strategy_for(topic_id))

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
    force_mode: bool = False,
    book_view_path: Path | str | None = None,
    outline_path: Path | str | None = None,
    bridge_provider: BridgeProvider | str | None = None,
) -> ExportPlanCollection:
    """Run Stage 4c and persist the export plan collection."""
    mode_enum = ReorgMode(str(mode)) if not isinstance(mode, ReorgMode) else mode

    bv_path = (
        Path(book_view_path) if book_view_path else (workspace.book_view_dir / "book_view.json")
    )
    if not bv_path.is_file():
        raise PlanError(f"BookView not found: {bv_path}")
    book = json.loads(bv_path.read_text(encoding="utf-8"))

    # Back-fill ``items[].asset_refs`` from inline Markdown image
    # markers before the planner collects items. Stage 3's bookview
    # builder only collects layout block-derived refs, so any figure
    # whose only presence is an inline ``![image](assets/<hash>)``
    # marker in ``item.text`` would never reach the planner or
    # renderer. The registry basename index comes from
    # ``normalized/assets_registry.json`` — the same source the
    # renderer uses — so the two layers stay in sync.
    registry_path = workspace.normalized_dir / "assets_registry.json"
    basename_index = _load_assets_basename_index(registry_path)
    inline_backfill = backfill_inline_asset_refs(book, basename_index)

    outline_provenance: dict[str, Any] | None = None
    outline: Outline | None = None
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
        force_mode=force_mode,
        project_id=workspace.root.name,
        outline=outline,
        outline_provenance=outline_provenance,
        bridge_provider=bridge_provider,
        review_dir=workspace.review_dir,
        content_figure_ids=_load_content_figure_ids(workspace),
    )
    collection = planner.plan()

    out_dir = workspace.export_plans_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "plans.json"
    out_path.write_text(
        json.dumps(collection.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    reports_dir = workspace.root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    topic_pruning_report = {
        "generated_at": _now(),
        "pruned_count": len(collection.topic_pruning),
        "pruned_items": collection.topic_pruning,
    }
    (reports_dir / "export_topic_pruning.json").write_text(
        json.dumps(topic_pruning_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Persist deduplication audit report when plans were dropped.
    if collection.dropped_plans:
        dedup_report = {
            "generated_at": _now(),
            "dropped_count": len(collection.dropped_plans),
            "dropped_plans": collection.dropped_plans,
        }
        (reports_dir / "plan_deduplication.json").write_text(
            json.dumps(dedup_report, ensure_ascii=False, indent=2),
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
            "dropped_plans": len(collection.dropped_plans),
            "topic_pruning": len(collection.topic_pruning),
            "plans_path": str(out_path.relative_to(workspace.root)),
            "outline_used": collection.outline_used,
            "inline_backfill": inline_backfill,
        },
    )
    return collection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
