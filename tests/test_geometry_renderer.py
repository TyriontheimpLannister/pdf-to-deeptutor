"""Tests for the renderer's geometry review gating."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf2dt.export.renderer import (
    ExportValidationStatus,
    PdfRenderer,
    RenderResult,
    _without_inline_image_markers,
)
from pdf2dt.project import ProjectWorkspace


def _stub_workspace(root: Path) -> ProjectWorkspace:
    """Build a ProjectWorkspace over *root* without going through
    :func:`load_workspace`.  The renderer only resolves path
    accessors; it does not require a manifest."""
    return ProjectWorkspace(root)


def plan_id_dict(plan: Any) -> dict[str, Any]:
    """Serialize a plan into the manifest input shape expected by
    renderer's _record_stage fingerprint.
    """
    return {
        "plan_id": plan.plan_id,
        "topic_id": plan.topic_id,
        "output_filename": plan.output_filename,
        "title": plan.title,
        "figure_ids": list(plan.figure_ids or []),
        "is_misc_fallback": bool(plan.is_misc_fallback),
        "unclassified_count": int(plan.unclassified_count),
    }


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


# ---------------------------------------------------------------------- #
# Figure role filtering — skip embedding when role == decor
# ---------------------------------------------------------------------- #


def _seed_assets_registry(workspace_root: Path, entries: list[dict[str, Any]]) -> None:
    """Drop a minimal normalized/assets_registry.json."""
    from PIL import Image

    assets_dir = workspace_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        rel = entry["local_path"]
        img_path = workspace_root / rel
        img_path.parent.mkdir(parents=True, exist_ok=True)
        if not img_path.is_file():
            # 64x64 RGB so fpdf2 has enough pixel data to embed.
            Image.new("RGB", (64, 64), color="white").save(img_path, "PNG")
    (workspace_root / "normalized").mkdir(parents=True, exist_ok=True)
    (workspace_root / "normalized" / "assets_registry.json").write_text(
        json.dumps({"assets": entries}), encoding="utf-8"
    )


def _plan_with_items(plan_id: str, items: list[dict[str, Any]]) -> Any:
    from pdf2dt.export.planner import ExportPlan, ReorgMode

    return ExportPlan(
        plan_id=plan_id,
        topic_id="geometry-plane-triangles",
        output_filename=f"{plan_id}.pdf",
        title=f"Plan {plan_id}",
        items=items,
        figure_ids=[],
        bridges=[],
        outline_used=None,
        is_misc_fallback=False,
        mode=ReorgMode.A,
        unclassified_count=0,
    )


def test_renderer_skips_figure_marked_as_decor(tmp_path: Path) -> None:
    _seed_assets_registry(
        tmp_path,
        [
            {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
        ],
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "figure_roles.json").write_text(
        json.dumps(
            {
                "schema_version": "figure_roles/v1",
                "figures": [
                    {
                        "figure_id": "asset-1",
                        "asset_id": "asset-1",
                        "asset_sha256": "",
                        "role": "decor",
                        "confidence": 0.9,
                        "reason": "cartoon panda",
                        "model_id": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    result = renderer.render_plan(plan)

    assert result.figure_count == 0
    assert len(result.dropped_figures) == 1
    assert result.dropped_figures[0]["asset_id"] == "asset-1"
    assert result.dropped_figures[0]["role"] == "decor"
    assert result.validation_status == ExportValidationStatus.READY


def test_renderer_removes_inline_image_markers_from_body() -> None:
    body = "题目说明\n\n![image](assets/asset-1.jpg)\n\n继续说明"
    assert _without_inline_image_markers(body) == "题目说明\n\n继续说明"


def test_renderer_embeds_figure_when_role_is_content(tmp_path: Path) -> None:
    _seed_assets_registry(
        tmp_path,
        [
            {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
        ],
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "figure_roles.json").write_text(
        json.dumps(
            {
                "schema_version": "figure_roles/v1",
                "figures": [
                    {
                        "figure_id": "asset-1",
                        "asset_id": "asset-1",
                        "asset_sha256": "",
                        "role": "content",
                        "confidence": 0.95,
                        "reason": "labeled triangle",
                        "model_id": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    result = renderer.render_plan(plan)

    assert result.figure_count == 1
    assert result.dropped_figures == []


def test_renderer_embeds_figure_when_role_is_ambiguous(tmp_path: Path) -> None:
    """Ambiguous must render — it is the safe default that prevents
    accidental drops of figures that may have been needed.
    """
    _seed_assets_registry(
        tmp_path,
        [
            {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
        ],
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "figure_roles.json").write_text(
        json.dumps(
            {
                "schema_version": "figure_roles/v1",
                "figures": [
                    {
                        "figure_id": "asset-1",
                        "asset_id": "asset-1",
                        "asset_sha256": "",
                        "role": "ambiguous",
                        "confidence": 0.5,
                        "reason": "couldn't tell",
                        "model_id": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    result = renderer.render_plan(plan)

    assert result.figure_count == 1
    assert result.dropped_figures == []


def test_renderer_user_override_promotes_decor_to_content(tmp_path: Path) -> None:
    """User override flips a previously-decor figure back to content
    so it ends up in the export.
    """
    _seed_assets_registry(
        tmp_path,
        [
            {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
        ],
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "figure_roles.json").write_text(
        json.dumps(
            {
                "schema_version": "figure_roles/v1",
                "figures": [
                    {
                        "figure_id": "asset-1",
                        "asset_id": "asset-1",
                        "asset_sha256": "",
                        "role": "decor",
                        "confidence": 0.9,
                        "reason": "auto",
                        "model_id": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    from pdf2dt.review import apply_figure_role_overrides
    from pdf2dt.review.store import FigureRoleDecision

    apply_figure_role_overrides(
        _stub_workspace(tmp_path),
        [FigureRoleDecision(figure_id="asset-1", role="content", reviewer_note="user")],
    )

    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_assets(["asset-1"])
    result = renderer.render_plan(plan)

    assert result.figure_count == 1
    assert result.dropped_figures == []


def test_renderer_writes_dropped_figures_report(tmp_path: Path) -> None:
    _seed_assets_registry(
        tmp_path,
        [
            {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
            {"asset_id": "asset-2", "local_path": "assets/asset-2.png"},
        ],
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "figure_roles.json").write_text(
        json.dumps(
            {
                "schema_version": "figure_roles/v1",
                "figures": [
                    {
                        "figure_id": "asset-1",
                        "asset_id": "asset-1",
                        "asset_sha256": "",
                        "role": "decor",
                        "confidence": 0.9,
                        "reason": "auto",
                        "model_id": "test",
                    },
                    {
                        "figure_id": "asset-2",
                        "asset_id": "asset-2",
                        "asset_sha256": "",
                        "role": "content",
                        "confidence": 0.95,
                        "reason": "auto",
                        "model_id": "test",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    from pdf2dt.export import ExportPlanCollection, plan_exports  # noqa: F401
    from pdf2dt.export.planner import ExportPlan, ReorgMode

    items = [
        {
            "item_id": "i-0",
            "item_type": "definition",
            "title": "item 0",
            "text": "Item 0 body content for the dropped figure test.",
            "page_refs": [1],
            "asset_refs": [
                {"asset_id": "asset-1", "caption": "cap1"},
                {"asset_id": "asset-2", "caption": "cap2"},
            ],
        }
    ]
    plan = ExportPlan(
        plan_id="plan-1",
        topic_id="geometry-plane-triangles",
        output_filename="plan-1.pdf",
        title="Plan 1",
        items=items,
        figure_ids=["asset-1", "asset-2"],
        bridges=[],
        outline_used=None,
        is_misc_fallback=False,
        unclassified_count=0,
        mode=ReorgMode.B,
    )
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    collection = ExportPlanCollection(
        project_id="demo",
        generated_at="2026-07-13T00:00:00Z",
        mode=ReorgMode.B,
        outline_used=None,
        plans=[plan],
    )
    # render_collection reads export_plans/plans.json as a manifest input
    # fingerprint; the renderer itself doesn't need it to find plans
    # (the collection carries them), but it must exist for _record_stage
    # to fingerprint. A minimal stub is enough.
    plans_dir = tmp_path / "export_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "plans.json").write_text(
        json.dumps({"plans": [plan_id_dict(plan)]}), encoding="utf-8"
    )
    # _record_stage calls load_manifest which requires project.json;
    # seed a minimal stub so the fingerprint step succeeds.
    (tmp_path / "project.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "schema_version": "project/v1",
                "stages": {},
            }
        ),
        encoding="utf-8",
    )
    results = renderer.render_collection(collection)

    assert len(results) == 1
    r = results[0]
    assert r.figure_count == 1
    assert len(r.dropped_figures) == 1
    assert r.dropped_figures[0]["asset_id"] == "asset-1"

    report_path = tmp_path / "reports" / "dropped_figures.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total_dropped"] == 1
    assert report["drops"][0]["plan_id"] == "plan-1"
    assert report["drops"][0]["role"] == "decor"


def test_renderer_drops_text_noise_items(tmp_path: Path) -> None:
    """Renderer is a defence-in-depth layer: even if a noise item
    somehow ends up in a plan (because the matcher was extended
    after a run was staged, for instance), the renderer must still
    drop it from the PDF and record it in dropped_figures.json.
    """
    ws_root = tmp_path
    _seed_assets_registry(ws_root, [])
    items = [
        {
            "item_id": "item-keep",
            "item_type": "example",
            "title": "例题 1",
            "text": "求证: 三角形 ABC 的内角和为 180 度.",
            "page_refs": [1],
            "asset_refs": [],
        },
        {
            "item_id": "item-watermark",
            "item_type": "section",
            "title": "微信公众号 教辅资料站",
            "text": "微信公众号 教辅资料站",
            "page_refs": [1],
            "asset_refs": [],
        },
        {
            "item_id": "item-page-num",
            "item_type": "section",
            "title": "118",
            "text": "",
            "page_refs": [1],
            "asset_refs": [],
        },
        {
            "item_id": "item-symbol",
            "item_type": "section",
            "title": "#",
            "text": "#",
            "page_refs": [1],
            "asset_refs": [],
        },
    ]
    renderer = PdfRenderer(_stub_workspace(ws_root))
    plan = _plan_with_items("plan-noise", items)
    result = renderer.render_plan(plan)

    # Three noise items recorded with role=text-noise; the math item
    # is kept.
    dropped_by_id = {d["item_id"]: d for d in result.dropped_figures}
    assert {"item-watermark", "item-page-num", "item-symbol"} <= set(dropped_by_id)
    for entry in dropped_by_id.values():
        assert entry["role"] == "text-noise"
        assert any(
            marker in entry["reason"]
            for marker in ("watermark", "page number", "single ASCII")
        )
        assert entry["asset_id"] is None
    assert "item-keep" not in dropped_by_id


def test_renderer_keeps_short_titled_math_items(tmp_path: Path) -> None:
    """Regression: a single-character title whose body is real math
    must NOT be filtered. ``title='习'`` with a long body containing
    a real problem should land in the PDF.
    """
    _seed_assets_registry(tmp_path, [])
    items = [
        {
            "item_id": "item-xi",
            "item_type": "example",
            "title": "习",
            "text": "5. 观察下图中的规律, 请按照这种规律, 填出空格中的图形.",
            "page_refs": [1],
            "asset_refs": [],
        },
    ]
    renderer = PdfRenderer(_stub_workspace(tmp_path))
    plan = _plan_with_items("plan-keep", items)
    result = renderer.render_plan(plan)
    assert result.dropped_figures == []
    assert result.item_count == 1
