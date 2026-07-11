"""Tests for the deterministic figure description generator."""
from __future__ import annotations

import json
from pathlib import Path

from pdf2dt.export.renderer import (
    PdfRenderer,
)
from pdf2dt.geometry import (
    Evidence,
    GeometryFigure,
    GeometryRelation,
    RelationType,
    ReviewState,
    describe_figure,
    describe_figure_block,
    detect_locale,
    format_relation_bullets,
)

# ---------------------------------------------------------------------- #
# Locale detection
# ---------------------------------------------------------------------- #


def test_detect_locale_cjk() -> None:
    assert detect_locale("在 triangle ABC 中") == "zh"
    assert detect_locale("AB 平行于 DE") == "zh"


def test_detect_locale_ascii() -> None:
    assert detect_locale("AB is parallel to DE") == "en"
    assert detect_locale("triangle ABC") == "en"


def test_detect_locale_empty() -> None:
    assert detect_locale("") == "en"


# ---------------------------------------------------------------------- #
# describe_figure
# ---------------------------------------------------------------------- #


def _figure(*relations: GeometryRelation) -> GeometryFigure:
    return GeometryFigure(
        figure_id="fig-1",
        asset_id="asset-1",
        associated_item_id="i-1",
        points=["A", "B", "C", "D", "E"],
        segments=["AB", "DE"],
        relations=list(relations),
    )


def test_describe_parallel_english() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    sentences = describe_figure(fig, locale="en")
    assert sentences == ["AB is parallel to DE."]


def test_describe_parallel_chinese_auto_detected() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
        GeometryRelation(
            type=RelationType.MIDPOINT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
    )
    # No CJK in entities — auto detection picks English.
    sentences = describe_figure(fig)
    assert any("parallel" in s for s in sentences)
    assert any("midpoint" in s for s in sentences)

    # With explicit Chinese locale, output uses Chinese templates.
    sentences_zh = describe_figure(fig, locale="zh")
    assert any("平行于" in s for s in sentences_zh)
    assert any("中点" in s for s in sentences_zh)


def test_describe_equal_angle() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.EQUAL_ANGLE,
            entities=["A", "B", "C", "D", "E", "F"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    sentences = describe_figure(fig, locale="en")
    assert sentences == ["∠ABC and ∠DEF are equal."]


def test_describe_midpoint() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.MIDPOINT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    assert describe_figure(fig, locale="en") == ["D is the midpoint of AB."]
    assert describe_figure(fig, locale="zh") == ["D 是 AB 的中点。"]


def test_describe_collinear() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.COLLINEAR,
            entities=["A", "B", "C"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    assert describe_figure(fig, locale="en") == [
        "A, B, and C are collinear."
    ]


def test_describe_point_on_segment() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.POINT_ON_SEGMENT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    assert describe_figure(fig, locale="en") == [
        "D lies on segment AB."
    ]
    assert describe_figure(fig, locale="zh") == ["D 在线段 AB 上。"]


def test_describe_perpendicular() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PERPENDICULAR,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    assert describe_figure(fig, locale="en") == [
        "AB is perpendicular to DE."
    ]


def test_describe_equal_length() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.EQUAL_LENGTH,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        )
    )
    assert describe_figure(fig, locale="en") == [
        "AB and DE have equal length."
    ]


# ---------------------------------------------------------------------- #
# Review gating
# ---------------------------------------------------------------------- #


def test_describe_drops_unreviewed_relations() -> None:
    """Unreviewed relations are silently dropped.

    The describer honours ``INCLUDABLE_REVIEW_STATES``; review
    state enforcement (which forbids ``visual_inference`` to
    reach ``confirmed``) is the store's job, not the
    describer's.
    """
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.UNREVIEWED,
        ),
    )
    sentences = describe_figure(fig, locale="en")
    assert sentences == []


def test_describe_keeps_corrected() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.VISUAL_INFERENCE,
            review_state=ReviewState.CORRECTED,
        )
    )
    sentences = describe_figure(fig, locale="en")
    assert sentences == ["AB is parallel to DE."]


def test_describe_drops_rejected() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.REJECTED,
        )
    )
    assert describe_figure(fig) == []


# ---------------------------------------------------------------------- #
# describe_figure_block
# ---------------------------------------------------------------------- #


def test_block_joins_with_separator_zh() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
        GeometryRelation(
            type=RelationType.MIDPOINT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
    )
    block = describe_figure_block(fig, locale="zh")
    # Chinese sentences already end in "。" so the joiner is empty.
    assert "平行于" in block
    assert "中点" in block
    # No double punctuation.
    assert "。。" not in block


def test_block_joins_with_space_en() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
        GeometryRelation(
            type=RelationType.MIDPOINT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
    )
    block = describe_figure_block(fig, locale="en")
    assert block == "AB is parallel to DE. D is the midpoint of AB."


def test_block_empty_when_no_includable() -> None:
    fig = _figure()  # no relations
    assert describe_figure_block(fig, locale="en") == ""


# ---------------------------------------------------------------------- #
# format_relation_bullets
# ---------------------------------------------------------------------- #


def test_bullets_skip_unreviewed() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.CONFIRMED,
        ),
        GeometryRelation(
            type=RelationType.MIDPOINT,
            entities=["D", "AB"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.UNREVIEWED,
        ),
    )
    bullets = list(format_relation_bullets(fig, include_reviewed_only=True))
    assert len(bullets) == 1
    assert "parallel" in bullets[0]
    assert "AB" in bullets[0]


def test_bullets_include_all_when_requested() -> None:
    fig = _figure(
        GeometryRelation(
            type=RelationType.PARALLEL,
            entities=["AB", "DE"],
            evidence=Evidence.PROBLEM_TEXT,
            review_state=ReviewState.UNREVIEWED,
        )
    )
    bullets = list(format_relation_bullets(fig, include_reviewed_only=False))
    assert len(bullets) == 1


# ---------------------------------------------------------------------- #
# Renderer integration
# ---------------------------------------------------------------------- #


def _stub_workspace(tmp_path: Path):
    """Build a ProjectWorkspace on tmp_path without a manifest."""
    from pdf2dt.project import ProjectWorkspace
    return ProjectWorkspace(tmp_path)


def test_renderer_writes_description_paragraph(tmp_path: Path) -> None:
    """The renderer must write a description paragraph when at
    least one confirmed relation exists for the embedded figure."""
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(
            {
                "schema_version": "geometry_figures/v1",
                "figures": [
                    {
                        "figure_id": "fig-1",
                        "asset_id": "asset-1",
                        "associated_item_id": "i-1",
                        "points": ["A", "B", "D", "E"],
                        "segments": ["AB", "DE"],
                        "relations": [
                            {
                                "type": "parallel",
                                "entities": ["AB", "DE"],
                                "evidence": "problem_text",
                                "review_state": "confirmed",
                            }
                        ],
                        "visual_observations": [],
                        "review_state": "confirmed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    renderer = PdfRenderer(_stub_workspace(tmp_path))

    # Spy on write_caption to capture the text without actually
    # producing a PDF.
    captured: list[str] = []

    class _SpyPdf:
        def write_caption(self, text: str) -> None:
            captured.append(text)

        def get_y(self) -> float:
            return 0.0

        def set_y(self, y: float) -> None:  # noqa: ARG002
            pass

        def add_page(self) -> None:
            pass

        def image(self, *args, **kwargs) -> None:  # noqa: ARG002
            pass

    renderer._render_geometry_description(
        _SpyPdf(),  # type: ignore[arg-type]
        {
            "figure_id": "fig-1",
            "asset_id": "asset-1",
            "associated_item_id": "i-1",
            "points": ["A", "B", "D", "E"],
            "segments": ["AB", "DE"],
            "relations": [
                {
                    "type": "parallel",
                    "entities": ["AB", "DE"],
                    "evidence": "problem_text",
                    "review_state": "confirmed",
                }
            ],
        },
    )
    assert any("parallel" in s for s in captured), captured
    # And the bullet list still works (sanity).
    renderer._render_geometry_relations(
        _SpyPdf(),  # type: ignore[arg-type]
        {
            "relations": [
                {
                    "type": "parallel",
                    "entities": ["AB", "DE"],
                    "evidence": "problem_text",
                    "review_state": "confirmed",
                }
            ]
        },
    )
    assert any("•" in s for s in captured)


def test_renderer_skips_description_when_no_includable(
    tmp_path: Path,
) -> None:
    """When every relation is unreviewed, no description is
    written.  This preserves the existing caption-only behaviour
    for figures whose review state is still pending."""
    renderer = PdfRenderer(_stub_workspace(tmp_path))

    captured: list[str] = []

    class _SpyPdf:
        def write_caption(self, text: str) -> None:
            captured.append(text)

    renderer._render_geometry_description(
        _SpyPdf(),  # type: ignore[arg-type]
        {
            "figure_id": "fig-1",
            "asset_id": "asset-1",
            "associated_item_id": "i-1",
            "points": ["A", "B", "D", "E"],
            "segments": ["AB", "DE"],
            "relations": [
                {
                    "type": "parallel",
                    "entities": ["AB", "DE"],
                    "evidence": "visual_inference",
                    "review_state": "unreviewed",
                }
            ],
        },
    )
    assert captured == []


def test_renderer_skips_description_on_malformed_queue(
    tmp_path: Path,
) -> None:
    """A malformed geometry entry must not crash the renderer."""
    renderer = PdfRenderer(_stub_workspace(tmp_path))

    captured: list[str] = []

    class _SpyPdf:
        def write_caption(self, text: str) -> None:
            captured.append(text)

    renderer._render_geometry_description(
        _SpyPdf(),  # type: ignore[arg-type]
        {"not": "a figure"},
    )
    assert captured == []
