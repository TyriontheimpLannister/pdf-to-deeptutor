"""Workspace/report contract tests for the Phase 1.5 dry-run adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from pdf2dt.figure_roles.pre_filter_runtime import (
    REPORT_FILENAME,
    build_prefilter_candidates,
    build_template_decor_audit,
    run_prefilter_dry_run,
)
from pdf2dt.project import ProjectWorkspace


def _workspace(
    tmp_path: Path,
    *,
    with_registry: bool = True,
    text: str = "微信公众号 教辅资料站\n![image](assets/icon.png)",
    image_size: tuple[int, int] = (90, 90),
    registry_path: str = "assets/icon.png",
) -> ProjectWorkspace:
    ws = ProjectWorkspace(tmp_path)
    (ws.book_view_dir / "book_view.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "item_id": "item-1",
                        "title": "例题",
                        "text": text,
                        "item_type": "section",
                        "asset_refs": [
                            {"asset_id": "asset-1", "local_path": registry_path}
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    asset = ws.assets_dir / "icon.png"
    Image.new("RGB", image_size, color="white").save(asset)
    if with_registry:
        (ws.normalized_dir / "assets_registry.json").write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "asset_id": "asset-1",
                            "local_path": registry_path,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    return ws


def test_dry_run_report_is_provider_free_and_candidate_granular(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    report = run_prefilter_dry_run(ws)

    assert report["provider_calls"] == 0
    assert report["candidates_total"] == 1
    assert report["total_unique_decor"] == 1
    assert report["projected_vlm_calls_after"] == 0
    assert report["unique_decor_tuples"][0]["item_id"] == "item-1"
    assert report["config_snapshot"]["rules_enabled"] == [
        "decor_phrase_in_context",
        "tiny_icon_size",
        "extreme_aspect_ratio",
    ]
    assert report["notes"]["approved_rules"] == []
    assert report["notes"]["real_corpus"] == ws.root.name
    assert all(
        entry["ground_truth_comparison"]["status"] == "unverified"
        for entry in report["rule_stats"].values()
    )
    assert report["template_decor_audit"]["status"] == "computed"
    assert report["template_decor_audit"]["matched_asset_count"] == 0
    assert report["template_decor_audit"]["affects_projected_savings"] is False


def test_phrase_outside_local_image_window_does_not_flag(tmp_path: Path) -> None:
    ws = _workspace(
        tmp_path,
        text="微信公众号 教辅资料站\n" + "unrelated text " * 30
        + "\n![image](assets/icon.png)",
        image_size=(200, 200),
    )

    report = run_prefilter_dry_run(ws)

    assert report["total_unique_decor"] == 0
    assert report["projected_vlm_calls_after"] == 1


def test_windows_registry_path_matches_markdown_image_marker(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, registry_path="assets\\icon.png")

    report = run_prefilter_dry_run(ws)

    assert report["candidates_total"] == 1
    assert report["rule_stats"]["tiny_icon_size"]["hits"] == 1


def test_missing_registry_defers_and_does_not_flag(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, with_registry=False)
    report = run_prefilter_dry_run(ws)

    assert report["candidates_total"] == 1
    assert report["total_unique_decor"] == 0
    assert report["projected_vlm_calls_after"] == 1
    assert all(stats["hits"] == 0 for stats in report["rule_stats"].values())
    assert report["template_decor_audit"]["status"] == "unavailable"


def test_template_decor_audit_is_separate_from_prefilter_savings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(
        tmp_path,
        text="ordinary math text\n![image](assets/icon.png)",
        image_size=(200, 200),
    )
    monkeypatch.setattr(
        "pdf2dt.figure_roles.pre_filter_runtime.find_template_decor_assets",
        lambda _asset_paths: {"asset-1"},
    )

    report = run_prefilter_dry_run(ws)

    audit = report["template_decor_audit"]
    assert audit["matched_asset_count"] == 1
    assert audit["candidate_hits"] == 1
    assert audit["projected_saved_calls"] == 1
    assert audit["approved_for_provider_skip"] is False
    assert audit["affects_projected_savings"] is False
    assert report["total_unique_decor"] == 0
    assert report["projected_vlm_calls_after"] == 1


def test_template_decor_audit_uses_candidate_granularity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, image_size=(200, 200))
    candidates, _ = build_prefilter_candidates(ws)
    monkeypatch.setattr(
        "pdf2dt.figure_roles.pre_filter_runtime.find_template_decor_assets",
        lambda _asset_paths: {"asset-1"},
    )

    audit = build_template_decor_audit(ws, candidates)

    assert audit["candidate_hits"] == 1
    assert audit["matched_asset_ids"] == ["asset-1"]


def test_existing_report_is_not_overwritten(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    report_path = ws.reports_dir / REPORT_FILENAME
    report_path.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_prefilter_dry_run(ws)
    assert report_path.read_text(encoding="utf-8") == "keep me\n"


def test_cluster_audit_is_separate_from_savings(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    report = run_prefilter_dry_run(
        ws,
        cluster_audit={"status": "review-only", "unique_cluster_decor": 999},
    )

    assert report["cluster_audit"]["unique_cluster_decor"] == 999
    assert report["cluster_audit"]["affects_projected_savings"] is False
    assert report["total_unique_decor"] == 1
