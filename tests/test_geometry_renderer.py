"""Tests for the renderer's geometry review gating."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf2dt.export.renderer import (
    ExportValidationStatus,
    PdfRenderer,
    RenderResult,
)
from pdf2dt.project import ProjectWorkspace


def _stub_workspace(root: Path) -> ProjectWorkspace:
    """Build a ProjectWorkspace over *root* without going through
    :func:`load_workspace`.  The renderer only resolves path
    accessors; it does not require a manifest."""
    return ProjectWorkspace(root)


def _plan_with_assets(item_asset_ids: list[str]) -> Any:
    from pdf2dt.export.planner import ExportPlan, ReorgMode

    items = [
        {
            "item_id": f"i-{i}",
            "item_type": "definition",
            "title": f"item {i}",
            "text": "x",
            "page_refs": [1],
            "asset_refs": [{"asset_id": aid, "caption": "cap"}],
        }
        for i, aid in enumerate(item_asset_ids)
    ]
    return ExportPlan(
        plan_id="plan-1",
        topic_id="geometry-plane-triangles",
        output_filename="plan-1.pdf",
        title="Plan 1",
        items=items,
        figure_ids=list(item_asset_ids),
        bridges=[],
        outline_used=None,
        is_misc_fallback=False,
        unclassified_count=0,
        mode=ReorgMode.B,
    )


def test_render_result_blocks_on_unreviewed_visual_inference() -> None:
    r = RenderResult(
        output_path=Path("dummy.pdf"),
        plan_id="p",
        item_count=1,
        figure_count=1,
        missing_figures=[],
        geometry_blocked_figures=["asset-1"],
    )
    r.finalise()
    assert r.validation_status == ExportValidationStatus.BLOCKED


def test_render_result_does_not_block_after_review() -> None:
    r = RenderResult(
        output_path=Path("dummy.pdf"),
        plan_id="p",
        item_count=1,
        figure_count=1,
        missing_figures=[],
        geometry_blocked_figures=[],
    )
    r.finalise()
    assert r.validation_status == ExportValidationStatus.READY


def test_geometry_blocked_for_plan_marks_unreviewed_visual_inference(
    tmp_path: Path,
) -> None:
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
                        "associated_item_id": "i-0",
                        "points": ["A", "B"],
                        "segments": ["AB"],
                        "relations": [
                            {
                                "type": "parallel",
                                "entities": ["AB"],
                                "evidence": "visual_inference",
                                "review_state": "unreviewed",
                            }
                        ],
                        "visual_observations": [],
                        "review_state": "unreviewed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    blocked = renderer._geometry_blocked_for_plan(plan)
    assert blocked == ["asset-1"]


def test_geometry_blocked_for_plan_ignores_confirmed(
    tmp_path: Path,
) -> None:
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
                        "associated_item_id": "i-0",
                        "points": ["A", "B"],
                        "segments": ["AB"],
                        "relations": [
                            {
                                "type": "parallel",
                                "entities": ["AB"],
                                "evidence": "visual_inference",
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
    plan = _plan_with_assets(["asset-1"])
    assert renderer._geometry_blocked_for_plan(plan) == []


def test_geometry_blocked_for_plan_ignores_promotable_unreviewed(
    tmp_path: Path,
) -> None:
    """Unreviewed problem_text is *not* a hard block — only
    visual_inference and unknown are blocked.  Promotable evidence
    is allowed to remain unreviewed because the user can still
    review it later without invalidating the export."""
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
                        "associated_item_id": "i-0",
                        "points": ["A", "B"],
                        "segments": ["AB"],
                        "relations": [
                            {
                                "type": "parallel",
                                "entities": ["AB"],
                                "evidence": "problem_text",
                                "review_state": "unreviewed",
                            }
                        ],
                        "visual_observations": [],
                        "review_state": "unreviewed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    assert renderer._geometry_blocked_for_plan(plan) == []


def test_geometry_blocked_for_plan_skips_unknown_asset(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(
            {
                "schema_version": "geometry_figures/v1",
                "figures": [],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    assert renderer._geometry_blocked_for_plan(plan) == []


def test_geometry_blocked_for_plan_marks_unknown_evidence(tmp_path: Path) -> None:
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
                        "associated_item_id": "i-0",
                        "points": ["A", "B"],
                        "segments": ["AB"],
                        "relations": [
                            {
                                "type": "parallel",
                                "entities": ["AB"],
                                "evidence": "unknown",
                                "review_state": "unreviewed",
                            }
                        ],
                        "visual_observations": [],
                        "review_state": "unreviewed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    assert renderer._geometry_blocked_for_plan(plan) == ["asset-1"]
