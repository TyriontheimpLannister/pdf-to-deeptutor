"""Mode C bridge generation.

A *bridge* is one transition paragraph that the planner inserts
between two adjacent export plans in Mode C, so a reader moving
from one topic's PDF to the next gets a short connective sentence
instead of an abrupt topic jump.

This module isolates the bridge-generation policy behind a small
:mod:`BridgeProvider` Protocol so:

* tests and the default project-wide UX run on
  :class:`MockBridgeProvider` (deterministic, no external deps),
* topic- and geometry-aware implementations like
  :class:`OutlineBridgeProvider` and
  :class:`GeometryBridgeProvider` can be registered without
  touching the planner or renderer,
* a future v2 may register a real LLM-backed implementation in
  this same package without touching the planner or renderer.

Bridges are deliberately typed as plain text + provenance: the
planner does not care which provider was used, only that the
output is a non-empty string and which preceding plan it
``follows``. The renderer typesets them as Markdown-flavoured
paragraphs (italicised, one line of vertical breathing room).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..geometry import (
    INCLUDABLE_REVIEW_STATES,
    GeometryFigure,
)


@dataclass(frozen=True)
class Bridge:
    """One transition paragraph between two adjacent plans.

    A bridge *belongs* to the plan it introduces: ``plans[i].bridges``
    contains the bridge(s) to render at the top of plan ``i``.
    Concretely, a bridge is positioned in the PDF stream **before**
    the first item of its owning plan, after a small spacer.
    """

    text: str
    """The transition text exactly as the renderer should typeset."""

    follows_plan_id: str | None = None
    """The plan_id of the topic this bridge comes *from*. ``None``
    for the very first plan in the collection (it has no
    predecessor)."""

    follows_topic_id: str | None = None
    """The topic_id of the preceding plan, when available. Used
    by MockBridgeProvider to produce a deterministic placeholder
    and by the renderer / review tools to surface provenance."""

    provider: str = "mock"
    """Identifier of the provider that produced this bridge. Lets
    reviews distinguish ``mock`` placeholder bridges from real
    LLM-generated ones."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Free-form provider-specific metadata (model name, prompt id,
    seed, …). Kept on the dataclass for round-trip safety; the
    planner persists it in the bridge dict but does not depend on
    its keys."""


@dataclass(frozen=True)
class BridgeContext:
    """Caller-supplied information for a single bridge generation
    call.

    The planner passes only the **preceding** plan here; the
    provider is expected to know which plan is being opened
    because :meth:`ExportPlanner.plan` invokes it in plan order
    and the bridge lives on the *next* plan.
    """

    follows_plan_id: str | None
    follows_topic_id: str | None
    follows_title: str | None
    follows_item_count: int
    next_plan_id: str
    next_topic_id: str | None
    next_title: str
    next_item_count: int


@runtime_checkable
class BridgeProvider(Protocol):
    """Pluggable bridge generator.

    Implementations must be deterministic given the same inputs
    and free of side effects (or fully reproduce them in the
    persisted :attr:`Bridge.metadata`). The planner calls
    :meth:`generate_bridge` exactly once per (previous, next)
    pair. Returning ``None`` is permitted and means "no bridge";
    the planner will skip insertion.

    Providers that need per-project resources (a geometry index,
    outline metadata, a workspace path) may optionally implement
    :meth:`attach_context`. The planner calls it exactly once,
    after construction and before the first :meth:`generate_bridge`
    call, so the provider can pre-load file-based resources in a
    single pass instead of re-reading them per bridge. Providers
    that need nothing may omit the method; the default
    implementation is a no-op.
    """

    name: str

    def generate_bridge(self, context: BridgeContext) -> Bridge | None: ...

    def attach_context(self, context: BridgeProviderContext) -> None:
        """Receive a one-time context for the current plan run.

        Default implementation is a no-op so existing providers
        (Mock, NoOp) and simple custom providers do not have to
        override it.
        """
        return None


@dataclass(frozen=True)
class BridgeProviderContext:
    """Caller-supplied per-run context passed to :meth:`BridgeProvider.attach_context`.

    This is the smallest payload needed by a topic- or
    geometry-aware provider. Everything is optional so providers
    that only need a subset can simply ignore the unused fields.

    * ``plans_by_id`` lets a provider look up the items / asset
      references of either side of the transition.
    * ``review_dir`` lets the provider read
      ``geometry_figures.json`` itself when it wants the full
      structured geometry data (rather than just the items
      registered on each plan).
    * ``outline`` carries the parsed :class:`Outline` model so the
      provider can pull vocabulary / labels / strategy overrides
      without re-parsing the YAML.
    """

    plans_by_id: dict[str, PlanAccessor] = field(default_factory=dict)
    review_dir: Path | None = None
    outline: Any = None


@dataclass(frozen=True)
class PlanAccessor:
    """Read-only view of one plan that a provider can inspect.

    Kept separate from :class:`ExportPlan` so providers do not
    take a hard dependency on the planner module and so the
    surface stays stable if ExportPlan grows new fields in the
    future.
    """

    plan_id: str
    topic_id: str | None
    title: str
    item_count: int
    asset_ids: tuple[str, ...]
    items: tuple[dict[str, Any], ...]


# ---------------------------------------------------------------------- #
# Built-in providers
# ---------------------------------------------------------------------- #


class MockBridgeProvider:
    """Deterministic placeholder bridge.

    Renders a clearly-marked placeholder so human review can
    immediately tell, in the PDF, which transitions are awaiting a
    real provider. Never raises; never returns ``None``. This is
    the default provider so the test suite and the default CLI
    UX never require external LLM access.
    """

    name = "mock"

    TEMPLATE = (
        "[Mock bridge] 本节承接自《{prev_title}》，进入《{next_title}》。"
        "（占位提示——后续可接入真实生成式过渡；Mode B 不会写入此句。）"
    )

    def attach_context(self, context: BridgeProviderContext) -> None:  # noqa: ARG002 - mock needs nothing
        return None

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:
        prev_title = context.follows_title or "上文"
        next_title = context.next_title or "下一节"
        text = self.TEMPLATE.format(prev_title=prev_title, next_title=next_title)
        return Bridge(
            text=text,
            follows_plan_id=context.follows_plan_id,
            follows_topic_id=context.follows_topic_id,
            provider=self.name,
            metadata={"template": "placeholder"},
        )


class NoOpBridgeProvider:
    """Provider that never inserts anything.

    Useful for tests that want to assert "with bridges disabled,
    the rendered PDF contains zero transition paragraphs", and as
    a documented escape hatch for projects that opt out of Mode C
    while keeping the same code path.
    """

    name = "noop"

    def attach_context(self, context: BridgeProviderContext) -> None:  # noqa: ARG002 - parity with Protocol
        return None

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:  # noqa: ARG002 - parity with Protocol
        return None


# ---------------------------------------------------------------------- #
# Topic-aware providers
# ---------------------------------------------------------------------- #


class OutlineBridgeProvider:
    """Topic-aware bridge generator driven by the outline vocabulary.

    Mode C places plans in outline-leaf order.  Two adjacent plans
    therefore represent two sibling leaves under the same parent.
    This provider consults the outline's vocabulary (keywords +
    patterns) and parent / leaf labels to write a short
    deterministic connective that names the parent context and
    explains *why* the two siblings belong together.

    Example output (CN locale):

        "本节承接《三角形》相关概念，进入《勾股定理》。"
        "两者同属「直角三角形」主题，相关关键词：直角边、斜边、平方。"

    The provider is **deterministic**: same outline + same plan
    pair ⇒ same text.  No model call, no network.  When the
    outline is missing or the relevant leaves cannot be located
    the provider degrades to a clearly-marked placeholder
    identical to :class:`MockBridgeProvider` so reviewers can
    always tell when an outline-derived preamble was unavailable.
    """

    name = "outline"

    TEMPLATE_WITH_PARENT = (
        "本节承接《{prev_title}》，进入《{next_title}》。"
        "两者同属「{parent_label}」主题，相关关键词：{keywords}。"
    )
    TEMPLATE_FALLBACK = MockBridgeProvider.TEMPLATE

    def __init__(
        self,
        *,
        max_keywords: int = 5,
        locale: str | None = None,
    ) -> None:
        self._max_keywords = max(0, int(max_keywords))
        self._locale = locale  # auto-detect if None
        self._outline: Any = None

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:
        outline = self._outline
        if outline is None:
            return self._fallback(context, reason="no-outline")

        prev_leaf = self._leaf_for(context.follows_topic_id)
        next_leaf = self._leaf_for(context.next_topic_id)
        if prev_leaf is None or next_leaf is None:
            return self._fallback(context, reason="leaf-missing")

        parent_label = self._shared_parent_label(prev_leaf, next_leaf)
        keywords = self._merged_keywords(prev_leaf, next_leaf)
        if parent_label is None and not keywords:
            return self._fallback(context, reason="no-shared-context")

        prev_title = context.follows_title or prev_leaf.label
        next_title = context.next_title or next_leaf.label

        if parent_label:
            text = self.TEMPLATE_WITH_PARENT.format(
                prev_title=prev_title,
                next_title=next_title,
                parent_label=parent_label,
                keywords=keywords or "（无可用关键词）",
            )
        else:
            # Shared keywords but no common parent — still useful,
            # fall back to a flatter variant.
            kw_display = keywords or "（无可用关键词）"
            text = (
                f"本节承接《{prev_title}》，进入《{next_title}》。"
                f"两者共享关键词：{kw_display}。"
            )

        return Bridge(
            text=text,
            follows_plan_id=context.follows_plan_id,
            follows_topic_id=context.follows_topic_id,
            provider=self.name,
            metadata={
                "parent_label": parent_label,
                "keywords": keywords.split("、") if keywords else [],
                "prev_leaf_id": prev_leaf.id,
                "next_leaf_id": next_leaf.id,
            },
        )

    def attach_context(self, ctx: BridgeProviderContext) -> None:
        self._outline = ctx.outline

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _leaf_for(self, topic_id: str | None):
        if topic_id is None or self._outline is None:
            return None
        return self._outline.leaf_by_id(topic_id)

    def _shared_parent_label(self, prev_leaf: Any, next_leaf: Any) -> str | None:
        """Find the lowest common ancestor's label, if any.

        Returns ``None`` when the two leaves belong to different
        top-level chapters — in which case the provider still
        surfaces a transition, but without the "common theme"
        framing.
        """
        outline = self._outline
        if outline is None:
            return None
        prev_anc = outline._ancestors_of(prev_leaf.id)
        next_anc = outline._ancestors_of(next_leaf.id)
        # Walk from the top down; the last node at which the two
        # ancestors still agree is the LCA.
        common: Any = None
        for a, b in zip(prev_anc, next_anc, strict=False):
            if a.id != b.id:
                break
            common = a
        if common is None or common.id in (prev_leaf.id, next_leaf.id):
            return None
        return common.label or common.id

    def _merged_keywords(self, prev_leaf: Any, next_leaf: Any) -> str:
        """Merge and de-duplicate vocabulary keywords for both leaves.

        Uses the outline's :class:`VocabularyEntry` for each leaf,
        preferring ``keywords`` over ``patterns`` (patterns are
        noisier as user-facing hints).  The result is rendered in
        CJK-friendly comma-separated form so it can be pasted
        straight into the bridge text.
        """
        outline = self._outline
        if outline is None:
            return ""
        merged: list[str] = []
        seen: set[str] = set()
        for leaf in (prev_leaf, next_leaf):
            vocab = outline.vocabulary_for(leaf.id)
            for kw in vocab.keywords:
                norm = kw.strip()
                if not norm or norm.casefold() in seen:
                    continue
                seen.add(norm.casefold())
                merged.append(norm)
                if 0 < self._max_keywords <= len(merged):
                    break
            if 0 < self._max_keywords <= len(merged):
                break
        return "、".join(merged)

    def _fallback(self, context: BridgeContext, *, reason: str) -> Bridge:
        """Degrade to a placeholder when outline-derived data is unavailable."""
        bridge = MockBridgeProvider().generate_bridge(context)
        assert bridge is not None  # MockBridgeProvider never returns None.
        return Bridge(
            text=bridge.text,
            follows_plan_id=bridge.follows_plan_id,
            follows_topic_id=bridge.follows_topic_id,
            provider=f"{self.name}-fallback",
            metadata={**bridge.metadata, "fallback_reason": reason},
        )


class GeometryBridgeProvider:
    """Geometry-aware bridge generator.

    Reads ``review/geometry_figures.json`` (when present) and
    writes a bridge that:

    1. Names the strongest **confirmed/corrected** relation from
       the *preceding* plan as a "given" so the reader carries it
       forward.  This honours the project's evidence rules — only
       relations whose ``review_state`` is in
       :data:`INCLUDABLE_REVIEW_STATES` are quoted.
    2. Names the strongest **confirmed/corrected** relation from
       the *next* plan as a "to be applied" so the reader knows
       what the new section is going to demonstrate.

    When the file is missing, no figure in either plan has
    confirmed relations, or the JSON is malformed, the provider
    returns ``None`` (not a placeholder) so the planner simply
    skips insertion.  This is intentional: a geometry-aware
    provider has nothing useful to say when there is no geometry
    data, and a fake placeholder would pollute the export.
    """

    name = "geometry"

    TEMPLATE = (
        "承接上节确认的「{prev_rel}」，"
        "本节将通过「{next_rel}」进入《{next_title}》。"
    )
    FALLBACK_TEXT = "（无可用的几何关系可作衔接。）"

    def __init__(self, *, max_entities: int = 3) -> None:
        self._max_entities = max(0, int(max_entities))
        self._review_dir: Path | None = None
        self._figures_by_id: dict[str, GeometryFigure] = {}
        self._plans_by_id: dict[str, PlanAccessor] = {}

    def attach_context(self, ctx: BridgeProviderContext) -> None:
        self._review_dir = ctx.review_dir
        self._figures_by_id = self._load_figures(ctx.review_dir)
        self._plans_by_id = dict(ctx.plans_by_id)

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:
        prev_plan = self._plans_by_id.get(context.follows_plan_id or "")
        next_plan = self._plans_by_id.get(context.next_plan_id or "")
        if prev_plan is None or next_plan is None:
            return None

        prev_rel = self._pick_relation_text(prev_plan.asset_ids)
        next_rel = self._pick_relation_text(next_plan.asset_ids)
        if prev_rel is None or next_rel is None:
            return None

        next_title = context.next_title or next_plan.title
        text = self.TEMPLATE.format(
            prev_rel=prev_rel,
            next_rel=next_rel,
            next_title=next_title,
        )
        return Bridge(
            text=text,
            follows_plan_id=context.follows_plan_id,
            follows_topic_id=context.follows_topic_id,
            provider=self.name,
            metadata={
                "prev_relation": prev_rel,
                "next_relation": next_rel,
                "prev_asset_id": self._pick_asset_id(prev_plan.asset_ids),
                "next_asset_id": self._pick_asset_id(next_plan.asset_ids),
            },
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_figures(self, review_dir: Path | None) -> dict[str, GeometryFigure]:
        if review_dir is None:
            return {}
        path = Path(review_dir) / "geometry_figures.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        out: dict[str, GeometryFigure] = {}
        for fig in data.get("figures") or []:
            aid = fig.get("asset_id")
            if not aid:
                continue
            try:
                out[str(aid)] = GeometryFigure.from_dict(fig)
            except (KeyError, ValueError, TypeError):
                # Skip malformed entries; the provider must never
                # fail the whole export because of one bad row.
                continue
        return out

    def _pick_relation_text(self, asset_ids: tuple[str, ...]) -> str | None:
        """Return the highest-priority confirmed/corrected relation text.

        Priority order:

        1. ``equal_length`` — most directly portable across topics.
        2. ``parallel`` / ``perpendicular`` — direction facts carry
           forward nicely.
        3. Everything else (midpoint, collinear, point_on_segment,
           equal_angle).

        Within the same priority bucket the relation with the
        lexicographically smallest ``key`` wins so the output is
        deterministic.
        """
        priority = [
            "equal_length",
            "parallel",
            "perpendicular",
            "midpoint",
            "collinear",
            "point_on_segment",
            "equal_angle",
        ]
        best: tuple[int, str, str] | None = None  # (prio, key, text)
        for aid in asset_ids:
            fig = self._figures_by_id.get(aid)
            if fig is None:
                continue
            for rel in fig.relations:
                if rel.review_state not in INCLUDABLE_REVIEW_STATES:
                    continue
                rtype = rel.type.value if hasattr(rel.type, "value") else str(rel.type)
                # Promote "PROMOTABLE_EVIDENCE" over visual_inference
                # / unknown — but since visual_inference cannot be
                # INCLUDABLE here, this is automatic in practice.
                try:
                    prio = priority.index(rtype)
                except ValueError:
                    prio = len(priority)
                entities = rel.entities[: self._max_entities or None]
                entities_text = "、".join(str(e) for e in entities) if entities else ""
                text = f"{rtype}({entities_text})"
                candidate = (prio, rel.key, text)
                if best is None or candidate < best:
                    best = candidate
        return best[2] if best else None

    def _pick_asset_id(self, asset_ids: tuple[str, ...]) -> str | None:
        for aid in asset_ids:
            if aid in self._figures_by_id:
                return aid
        return None


# ---------------------------------------------------------------------- #
# Registry
# ---------------------------------------------------------------------- #


_PROVIDERS: dict[str, BridgeProvider] = {
    MockBridgeProvider.name: MockBridgeProvider(),
    NoOpBridgeProvider.name: NoOpBridgeProvider(),
    OutlineBridgeProvider.name: OutlineBridgeProvider(),
    GeometryBridgeProvider.name: GeometryBridgeProvider(),
}

DEFAULT_BRIDGE_PROVIDER: BridgeProvider = _PROVIDERS[MockBridgeProvider.name]


def resolve_bridge_provider(name: str | BridgeProvider | None) -> BridgeProvider:
    """Look up a provider by name, pass through instances, or
    fall back to the default.

    Raises :class:`KeyError` (via dict lookup) if the name is
    unknown so the CLI can surface a clean error to the user.
    """
    if name is None:
        return DEFAULT_BRIDGE_PROVIDER
    if isinstance(name, BridgeProvider):
        return name
    if name in _PROVIDERS:
        return _PROVIDERS[name]
    raise KeyError(
        f"Unknown bridge provider {name!r}. "
        f"Known providers: {sorted(_PROVIDERS)}"
    )


def register_bridge_provider(provider: Any) -> None:
    """Register a custom provider.

    Used by future LLM-backed implementations to inject themselves
    into the planner. Tests can also use this to swap in a
    deterministic stub without monkey-patching.

    A provider is considered valid when it (1) has a non-empty
    ``name`` attribute and (2) exposes a callable
    ``generate_bridge``. ``attach_context`` is optional so legacy
    and minimal stub providers remain compatible.
    """
    name = getattr(provider, "name", None)
    if not name or not isinstance(name, str):
        raise TypeError(
            f"provider must have a non-empty string 'name', got {type(provider).__name__}"
        )
    if not callable(getattr(provider, "generate_bridge", None)):
        raise TypeError(
            f"provider must implement generate_bridge(), got {type(provider).__name__}"
        )
    _PROVIDERS[name] = provider


def known_bridge_providers() -> list[str]:
    """Snapshot of currently-registered provider names."""
    return sorted(_PROVIDERS)
