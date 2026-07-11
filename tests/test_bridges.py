"""Unit tests for the Mode C BridgeProvider abstraction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.export.bridges import (
    BridgeContext,
    BridgeProviderContext,
    GeometryBridgeProvider,
    MockBridgeProvider,
    NoOpBridgeProvider,
    OutlineBridgeProvider,
    PlanAccessor,
    known_bridge_providers,
    register_bridge_provider,
    resolve_bridge_provider,
)
from pdf2dt.geometry import (
    Evidence,
    GeometryFigure,
    GeometryRelation,
    RelationType,
    ReviewState,
)
from pdf2dt.outlining import Outline, Topic, VocabularyEntry


def test_mock_provider_returns_named_bridge() -> None:
    """MockBridgeProvider must always return a non-empty Bridge
    with ``name='mock'`` and the previous / next titles baked
    into the text."""
    ctx = BridgeContext(
        follows_plan_id="plan-a-001",
        follows_topic_id="topic-a",
        follows_title="三角形",
        follows_item_count=4,
        next_plan_id="plan-b-002",
        next_topic_id="topic-b",
        next_title="勾股定理",
        next_item_count=3,
    )
    bridge = MockBridgeProvider().generate_bridge(ctx)
    assert bridge is not None
    assert bridge.provider == "mock"
    assert "三角形" in bridge.text
    assert "勾股定理" in bridge.text
    # Bridge remembers the previous plan / topic for provenance.
    assert bridge.follows_plan_id == "plan-a-001"
    assert bridge.follows_topic_id == "topic-a"


def test_noop_provider_returns_none() -> None:
    """NoOpBridgeProvider must always return ``None`` so the
    planner can short-circuit without inserting anything."""
    ctx = BridgeContext(
        follows_plan_id="x",
        follows_topic_id="y",
        follows_title="A",
        follows_item_count=0,
        next_plan_id="z",
        next_topic_id="w",
        next_title="B",
        next_item_count=0,
    )
    assert NoOpBridgeProvider().generate_bridge(ctx) is None


def test_resolve_bridge_provider_default_is_mock() -> None:
    """``None`` must resolve to the default mock provider so the
    default CLI UX needs no extra flag."""
    p = resolve_bridge_provider(None)
    assert p.name == "mock"


def test_resolve_bridge_provider_by_name() -> None:
    assert resolve_bridge_provider("mock").name == "mock"
    assert resolve_bridge_provider("noop").name == "noop"


def test_resolve_bridge_provider_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown bridge provider"):
        resolve_bridge_provider("does-not-exist")


def test_resolve_bridge_provider_passes_instances_through() -> None:
    """Passing a BridgeProvider instance directly is supported
    so test doubles don't have to register themselves."""
    sentinel = NoOpBridgeProvider()
    assert resolve_bridge_provider(sentinel) is sentinel


def test_register_bridge_provider_appends() -> None:
    """A registered provider becomes the resolution target by name."""

    class OneOffProvider:
        name = "unit-test-oneoff"

        def generate_bridge(self, context: BridgeContext) -> str:  # noqa: ARG002 - sanity
            return "always-this-string"

    try:
        register_bridge_provider(OneOffProvider())
        assert "unit-test-oneoff" in known_bridge_providers()
        resolved = resolve_bridge_provider("unit-test-oneoff")
        assert resolved.name == "unit-test-oneoff"
    finally:
        # Clean up so the global registry does not leak across tests.
        from pdf2dt.export.bridges import _PROVIDERS
        _PROVIDERS.pop("unit-test-oneoff", None)


def test_register_rejects_non_provider() -> None:
    class NotAProvider:
        name = "fake"

    with pytest.raises(TypeError, match="generate_bridge"):
        register_bridge_provider(NotAProvider())  # type: ignore[arg-type]


def test_register_rejects_missing_name() -> None:
    class NoNameProvider:
        name = ""

        def generate_bridge(self, context: BridgeContext) -> str:  # noqa: ARG002 - sanity
            return ""

    with pytest.raises(TypeError, match="name"):
        register_bridge_provider(NoNameProvider())  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# OutlineBridgeProvider
# ---------------------------------------------------------------------- #


def _outline_with_siblings() -> Outline:
    """Outline with one chapter, two siblings sharing a parent."""
    right = Topic(
        id="right-triangle",
        label="直角三角形",
        children=(
            Topic(id="pythagoras", label="勾股定理"),
            Topic(id="isosceles", label="等腰三角形"),
        ),
    )
    other = Topic(id="circles", label="圆")
    return Outline(
        outline_id="g8-geom",
        name="G8 Geometry",
        version="1.0.0",
        applies_to={},
        topics=(right, other),
        vocabulary={
            "pythagoras": VocabularyEntry(keywords=("直角边", "斜边", "平方")),
            "isosceles": VocabularyEntry(keywords=("等腰", "顶角", "底边")),
            "circles": VocabularyEntry(keywords=("圆心", "半径")),
        },
    )


def test_outline_provider_uses_shared_parent_and_keywords() -> None:
    """When two leaves share a parent the bridge names the parent
    and lists merged keywords in CJK-friendly form."""
    outline = _outline_with_siblings()
    provider = OutlineBridgeProvider()
    provider.attach_context(BridgeProviderContext(outline=outline))

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="plan-pythagoras",
            follows_topic_id="pythagoras",
            follows_title="勾股定理",
            follows_item_count=3,
            next_plan_id="plan-isosceles",
            next_topic_id="isosceles",
            next_title="等腰三角形",
            next_item_count=2,
        )
    )
    assert bridge is not None
    assert bridge.provider == "outline"
    assert "直角三角形" in bridge.text
    assert "勾股定理" in bridge.text
    assert "等腰三角形" in bridge.text
    assert "直角边" in bridge.text
    assert bridge.metadata["parent_label"] == "直角三角形"


def test_outline_provider_falls_back_when_no_shared_parent() -> None:
    """When two leaves come from different top-level chapters the
    provider still emits a bridge but with a flatter wording and
    no parent label."""
    outline = _outline_with_siblings()
    provider = OutlineBridgeProvider()
    provider.attach_context(BridgeProviderContext(outline=outline))

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="plan-pythagoras",
            follows_topic_id="pythagoras",
            follows_title="勾股定理",
            follows_item_count=3,
            next_plan_id="plan-circles",
            next_topic_id="circles",
            next_title="圆",
            next_item_count=2,
        )
    )
    assert bridge is not None
    assert bridge.provider == "outline"
    # No parent_label → metadata reflects this.
    assert bridge.metadata["parent_label"] is None
    assert "直角边" in bridge.text or "圆心" in bridge.text


def test_outline_provider_falls_back_to_mock_when_outline_missing() -> None:
    """Without an outline attached, the provider degrades to a
    clearly-marked mock-style placeholder so reviewers can tell."""
    provider = OutlineBridgeProvider()  # never received attach_context
    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="pythagoras",
            follows_title="A",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="circles",
            next_title="B",
            next_item_count=1,
        )
    )
    assert bridge is not None
    assert bridge.provider == "outline-fallback"
    assert bridge.metadata.get("fallback_reason") == "no-outline"


def test_outline_provider_respects_max_keywords() -> None:
    """``max_keywords`` caps how many keywords are merged into the
    bridge text."""
    outline = _outline_with_siblings()
    provider = OutlineBridgeProvider(max_keywords=2)
    provider.attach_context(BridgeProviderContext(outline=outline))

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="pythagoras",
            follows_title="勾股定理",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="isosceles",
            next_title="等腰三角形",
            next_item_count=1,
        )
    )
    assert bridge is not None
    kws = bridge.metadata["keywords"]
    assert len(kws) <= 2


def test_outline_provider_deduplicates_keywords_case_insensitively() -> None:
    """Repeated keywords across the two vocabularies collapse into
    a single entry so the bridge stays short."""
    outline = Outline(
        outline_id="dup-test",
        name="Dup",
        version="1.0.0",
        applies_to={},
        topics=(
            Topic(
                id="chap",
                label="章",
                children=(
                    Topic(id="a", label="A"),
                    Topic(id="b", label="B"),
                ),
            ),
        ),
        vocabulary={
            "a": VocabularyEntry(keywords=("三角", "angle")),
            "b": VocabularyEntry(keywords=("三角", "Triangle")),
        },
    )
    provider = OutlineBridgeProvider()
    provider.attach_context(BridgeProviderContext(outline=outline))

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="a",
            follows_title="A",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="b",
            next_title="B",
            next_item_count=1,
        )
    )
    assert bridge is not None
    # Only one occurrence of "三角" should appear even though the
    # keyword is in both vocabularies.
    assert bridge.text.count("三角") == 1
    # English variants are kept as separate keys after de-dup.
    assert len(bridge.metadata["keywords"]) == 3


# ---------------------------------------------------------------------- #
# GeometryBridgeProvider
# ---------------------------------------------------------------------- #


def _geometry_figure(
    asset_id: str,
    relations: list[GeometryRelation],
) -> GeometryFigure:
    return GeometryFigure(
        figure_id=f"fig-{asset_id}",
        asset_id=asset_id,
        relations=relations,
    )


def _plan_with_assets(plan_id: str, asset_ids: tuple[str, ...]) -> PlanAccessor:
    items = [{"item_id": f"{plan_id}-item", "asset_refs": [{"asset_id": a} for a in asset_ids]}]
    return PlanAccessor(
        plan_id=plan_id,
        topic_id="topic",
        title=f"Plan {plan_id}",
        item_count=1,
        asset_ids=asset_ids,
        items=tuple(items),
    )


def test_geometry_provider_emits_bridge_with_confirmed_relations(tmp_path: Path) -> None:
    """When both plans carry confirmed relations the bridge quotes
    one relation from each side."""
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    figures = [
        _geometry_figure(
            "asset-prev",
            [
                GeometryRelation(
                    type=RelationType.EQUAL_LENGTH,
                    entities=["AB", "CD"],
                    evidence=Evidence.PROBLEM_TEXT,
                    review_state=ReviewState.CONFIRMED,
                ),
            ],
        ),
        _geometry_figure(
            "asset-next",
            [
                GeometryRelation(
                    type=RelationType.PERPENDICULAR,
                    entities=["EF", "GH"],
                    evidence=Evidence.DIAGRAM_MARK,
                    review_state=ReviewState.CORRECTED,
                ),
            ],
        ),
    ]
    payload = {
        "schema_version": "geometry_figures/v1",
        "figures": [f.to_dict() for f in figures],
    }
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=review_dir,
            plans_by_id={
                "p1": _plan_with_assets("p1", ("asset-prev",)),
                "p2": _plan_with_assets("p2", ("asset-next",)),
            },
        )
    )

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="topic",
            follows_title="上节",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="topic",
            next_title="本节",
            next_item_count=1,
        )
    )
    assert bridge is not None
    assert bridge.provider == "geometry"
    assert "equal_length" in bridge.text
    assert "perpendicular" in bridge.text
    assert "AB" in bridge.text or "CD" in bridge.text


def test_geometry_provider_skips_unreviewed_relations(tmp_path: Path) -> None:
    """Unreviewed ``visual_inference`` relations must never be
    quoted in a bridge — they fail the project evidence rules."""
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    figure = _geometry_figure(
        "asset-prev",
        [
            GeometryRelation(
                type=RelationType.EQUAL_LENGTH,
                entities=["AB", "CD"],
                evidence=Evidence.VISUAL_INFERENCE,
                review_state=ReviewState.UNREVIEWED,
            ),
        ],
    )
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(
            {
                "schema_version": "geometry_figures/v1",
                "figures": [figure.to_dict()],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=review_dir,
            plans_by_id={
                "p1": _plan_with_assets("p1", ("asset-prev",)),
                "p2": _plan_with_assets("p2", ("asset-prev",)),
            },
        )
    )

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="t",
            follows_title="上节",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="t",
            next_title="本节",
            next_item_count=1,
        )
    )
    assert bridge is None


def test_geometry_provider_returns_none_when_file_missing(tmp_path: Path) -> None:
    """No geometry file → nothing to say → no bridge."""
    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=tmp_path,  # exists but no geometry_figures.json
            plans_by_id={
                "p1": _plan_with_assets("p1", ("a",)),
                "p2": _plan_with_assets("p2", ("b",)),
            },
        )
    )
    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="t",
            follows_title="上节",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="t",
            next_title="本节",
            next_item_count=1,
        )
    )
    assert bridge is None


def test_geometry_provider_handles_malformed_json(tmp_path: Path) -> None:
    """A malformed geometry file must not raise — the provider
    simply degrades to ``None`` so the planner skips insertion."""
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "geometry_figures.json").write_text("{not-json", encoding="utf-8")

    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=review_dir,
            plans_by_id={
                "p1": _plan_with_assets("p1", ("a",)),
                "p2": _plan_with_assets("p2", ("b",)),
            },
        )
    )
    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="t",
            follows_title="上节",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="t",
            next_title="本节",
            next_item_count=1,
        )
    )
    assert bridge is None


def test_geometry_provider_priority_prefers_equal_length(tmp_path: Path) -> None:
    """When a figure has both a low- and high-priority relation,
    the high-priority one is chosen."""
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    figure = _geometry_figure(
        "asset",
        [
            GeometryRelation(
                type=RelationType.PERPENDICULAR,
                entities=["EF", "GH"],
                evidence=Evidence.DIAGRAM_MARK,
                review_state=ReviewState.CONFIRMED,
            ),
            GeometryRelation(
                type=RelationType.EQUAL_LENGTH,
                entities=["AB", "CD"],
                evidence=Evidence.PROBLEM_TEXT,
                review_state=ReviewState.CONFIRMED,
            ),
        ],
    )
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(
            {"schema_version": "geometry_figures/v1", "figures": [figure.to_dict()]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=review_dir,
            plans_by_id={
                "p1": _plan_with_assets("p1", ("asset",)),
                "p2": _plan_with_assets("p2", ("asset",)),
            },
        )
    )

    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="t",
            follows_title="上节",
            follows_item_count=1,
            next_plan_id="p2",
            next_topic_id="t",
            next_title="本节",
            next_item_count=1,
        )
    )
    assert bridge is not None
    assert bridge.metadata["prev_relation"].startswith("equal_length")
    assert bridge.metadata["next_relation"].startswith("equal_length")


def test_geometry_provider_requires_both_sides() -> None:
    """If only one side has confirmed relations the bridge is
    skipped — quoting a single side would mislead the reader."""
    provider = GeometryBridgeProvider()
    provider.attach_context(
        BridgeProviderContext(
            review_dir=None,
            plans_by_id={
                "p1": _plan_with_assets("p1", ()),
                "p2": _plan_with_assets("p2", ()),
            },
        )
    )
    bridge = provider.generate_bridge(
        BridgeContext(
            follows_plan_id="p1",
            follows_topic_id="t",
            follows_title="上节",
            follows_item_count=0,
            next_plan_id="p2",
            next_topic_id="t",
            next_title="本节",
            next_item_count=0,
        )
    )
    assert bridge is None


# ---------------------------------------------------------------------- #
# Registry / contract integration
# ---------------------------------------------------------------------- #


def test_new_providers_are_registered_by_default() -> None:
    """``outline`` and ``geometry`` should resolve without manual
    registration so the CLI can opt in by name alone."""
    assert "outline" in known_bridge_providers()
    assert "geometry" in known_bridge_providers()
    assert resolve_bridge_provider("outline").name == "outline"
    assert resolve_bridge_provider("geometry").name == "geometry"


def test_protocol_satisfied_by_all_built_in_providers() -> None:
    """Every built-in provider must satisfy the BridgeProvider
    protocol so the planner can call ``attach_context`` uniformly."""
    from pdf2dt.export.bridges import _PROVIDERS, BridgeProvider

    for name, provider in _PROVIDERS.items():
        assert isinstance(provider, BridgeProvider), name
        assert callable(getattr(provider, "attach_context", None)), name
        assert callable(getattr(provider, "generate_bridge", None)), name
