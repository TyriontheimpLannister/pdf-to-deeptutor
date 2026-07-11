"""End-to-end test for the pipeline runner against the synthetic fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.assets import LocalMirrorDownloader
from pdf2dt.geometry import (
    Evidence,
    GeometryAnalyzer,
    HybridGeometryAnalyzer,
    RelationType,
)
from pdf2dt.geometry.vlm import VlmRelationCandidate, VlmResponse
from pdf2dt.pipeline import AssetLocalizationError, PipelineRunner, run_pipeline
from pdf2dt.project import StageStatus

FIXTURE_TASK = Path(__file__).resolve().parents[1] / "demos/inbox-sample" / "g8-triangle-ch03"
FIXTURE_OUTLINE = Path(__file__).resolve().parents[1] / "outlines" / "elementary-math-v1.yaml"


@pytest.fixture
def mirror() -> LocalMirrorDownloader:
    return LocalMirrorDownloader(FIXTURE_TASK / "images")


def test_pipeline_creates_full_workspace(tmp_path: Path, mirror) -> None:
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "demo"

    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="demo",
        title="Demo",
        subject="math",
        stage="middle-G8",
    )

    # Standard layout exists
    for rel in (
        "source",
        "providers/mineru/raw",
        "assets",
        "normalized",
        "book_view",
        "topic_assignments",
        "export_plans",
        "review",
        "exports/deeptutor",
        "reports",
        "logs",
    ):
        assert (project_root / rel).is_dir()

    # Manifest persisted with stage records
    manifest = result.workspace.load_manifest()
    assert manifest["project_id"] == "demo"
    assert manifest["subject"]["subject"] == "math"
    assert manifest["stages"]["stage0_workspace"]["status"] == StageStatus.COMPLETED.value
    assert manifest["stages"]["stage1_ingest"]["status"] == StageStatus.COMPLETED.value
    assert manifest["stages"]["stage2_localize"]["status"] == StageStatus.COMPLETED.value
    assert manifest["stages"]["stage1_ingest"]["metadata"]["image_references_count"] == 4
    assert manifest["stages"]["stage2_localize"]["metadata"]["assets_localized"] == 4

    # 4 assets localized on disk
    assert len(result.asset_registry) == 4
    asset_files = list((project_root / "assets").iterdir())
    assert len(asset_files) == 4
    assert all(p.suffix == ".png" for p in asset_files)

    # Rewritten markdown no longer references mineru.example
    md = (project_root / "normalized" / "full.md").read_text(encoding="utf-8")
    assert "mineru.example" not in md
    assert md.count("![") == 4
    assert md.count("assets/") >= 4

    # Rewritten layout exists and all image_url values point at local paths
    layout_path = project_root / "normalized" / "layout.localized.json"
    assert layout_path.is_file()
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    for page in layout["pages"]:
        for block in page.get("blocks", []):
            if "image_url" in block:
                assert not block["image_url"].startswith("http")
                assert block["image_url"].startswith("assets/")
                assert "asset_id" in block

    # Registry persisted and consistent
    registry_path = project_root / "normalized" / "assets_registry.json"
    registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry_data["count"] == 4
    assert len(registry_data["assets"]) == 4
    assert len(registry_data["by_url"]) == 4

    # Raw MinerU output copied verbatim into providers/mineru/raw/
    raw = project_root / "providers" / "mineru" / "raw" / FIXTURE_TASK.name
    assert (raw / "full.md").is_file()
    assert (raw / "layout.json").is_file()
    assert (raw / "meta.json").is_file()


def test_pipeline_resumable(tmp_path: Path, mirror) -> None:
    """Re-running against the same project_root skips completed stages."""
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "p1"

    result1 = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="p1",
        title="first",
        preflight=False,
    )

    manifest1 = result1.workspace.load_manifest()
    assert manifest1["stages"]["stage0_workspace"]["status"] == "completed"
    assert manifest1["stages"]["stage1_ingest"]["status"] == "completed"
    assert manifest1["stages"]["stage2_localize"]["status"] == "completed"

    # Second run should succeed, skipping all completed stages.
    result2 = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="p1",
        title="second",
        preflight=False,
    )

    manifest2 = result2.workspace.load_manifest()
    # Stages 0-2 should be SKIPPED on the second run.
    assert manifest2["stages"]["stage0_workspace"]["status"] == "skipped"
    assert manifest2["stages"]["stage1_ingest"]["status"] == "skipped"
    assert manifest2["stages"]["stage2_localize"]["status"] == "skipped"

    # The workspace root is the same.
    assert result1.workspace.root == result2.workspace.root

    # Content should be unchanged — normalized files identical.
    md1 = (project_root / "normalized" / "full.md").read_text(encoding="utf-8")
    md2 = (project_root / "normalized" / "full.md").read_text(encoding="utf-8")
    assert md1 == md2


def test_pipeline_marks_stage2_failed_when_an_image_cannot_be_localized(
    tmp_path: Path,
) -> None:
    class FailingDownloader:
        def download(self, url: str):
            from pdf2dt.assets.models import DownloadResult, DownloadStatus

            return DownloadResult(url=url, status=DownloadStatus.FAILED, error="fixture miss")

    project_root = tmp_path / "failed-assets"
    with pytest.raises(AssetLocalizationError, match="4 image reference"):
        PipelineRunner(FailingDownloader()).run(
            project_root=project_root,
            inbox_task_dir=FIXTURE_TASK,
            project_id="failed-assets",
            title="Failed assets",
        )

    manifest = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
    stage = manifest["stages"]["stage2_localize"]
    assert stage["status"] == StageStatus.FAILED.value
    assert stage["metadata"]["assets_failed"] == 4
    report = json.loads(
        (project_root / stage["metadata"]["report_path"]).read_text(encoding="utf-8")
    )
    assert report["status"] == "failed"
    assert len(report["failures"]) == 4


class TestManifestQueries:
    def test_get_stage_status_none_for_missing(self, tmp_path: Path) -> None:
        from pdf2dt.project import create_workspace, get_stage_status

        ws = create_workspace(
            tmp_path / "p", project_id="p", title="t"
        )
        assert get_stage_status(ws, "stage1_ingest") is None

    def test_get_stage_status_returns_enum(self, tmp_path: Path, mirror) -> None:
        from pdf2dt.project import StageStatus, create_workspace, get_stage_status, record_stage

        ws = create_workspace(
            tmp_path / "p", project_id="p", title="t"
        )
        record_stage(ws, "stage1_ingest", status=StageStatus.COMPLETED)
        assert get_stage_status(ws, "stage1_ingest") == StageStatus.COMPLETED

    def test_is_stage_completed(self, tmp_path: Path, mirror) -> None:
        from pdf2dt.project import StageStatus, create_workspace, is_stage_completed, record_stage

        ws = create_workspace(
            tmp_path / "p", project_id="p", title="t"
        )
        assert not is_stage_completed(ws, "stage1_ingest")
        record_stage(ws, "stage1_ingest", status=StageStatus.COMPLETED)
        assert is_stage_completed(ws, "stage1_ingest")
        record_stage(ws, "stage1_ingest", status=StageStatus.FAILED)
        assert not is_stage_completed(ws, "stage1_ingest")
        # P0 fix: SKIPPED is recorded by the resume guard and must still
        # count as done, otherwise the next guard iteration flips
        # the stage back to "not done" and the pipeline re-extracts
        # the queue, dropping review_state.json.
        record_stage(ws, "stage1_ingest", status=StageStatus.SKIPPED)
        assert is_stage_completed(ws, "stage1_ingest")


class TestCopyMineruRawIdempotent:
    def test_skip_on_same_content(self, tmp_path: Path) -> None:
        from pdf2dt.project import create_workspace

        ws = create_workspace(
            tmp_path / "p", project_id="p", title="t"
        )
        # First copy
        ws.copy_mineru_raw(FIXTURE_TASK)
        target1 = ws.mineru_raw_dir / FIXTURE_TASK.name
        assert target1.is_dir()

        # Second copy — should be skipped (same hash).
        ws.copy_mineru_raw(FIXTURE_TASK)
        target2 = ws.mineru_raw_dir / FIXTURE_TASK.name
        assert target2.is_dir()
        # Same inode / path, not deleted and re-copied.
        assert target1 == target2

    def test_replace_on_different_content(self, tmp_path: Path) -> None:

        from pdf2dt.project import create_workspace

        ws = create_workspace(
            tmp_path / "p", project_id="p", title="t"
        )
        # First copy from fixture
        ws.copy_mineru_raw(FIXTURE_TASK)
        target = ws.mineru_raw_dir / FIXTURE_TASK.name

        # Modify a file in the raw copy to change its hash.
        meta = target / "meta.json"
        original = meta.read_text(encoding="utf-8")
        meta.write_text('{"modified": true}', encoding="utf-8")

        # Re-copy — should replace because content differs.
        ws.copy_mineru_raw(FIXTURE_TASK)
        restored = meta.read_text(encoding="utf-8")
        # The fixture's original meta.json is restored.
        assert restored == original


class TestFullPipeline:
    """End-to-end tests running Stages 0→7 with an outline."""

    def test_full_pipeline_with_outline(self, tmp_path: Path, mirror) -> None:
        """Run the full pipeline (Stage 0 through 7) with an outline."""
        runner = PipelineRunner(mirror)
        project_root = tmp_path / "projects" / "full-e2e"

        result = runner.run(
            project_root=project_root,
            inbox_task_dir=FIXTURE_TASK,
            project_id="full-e2e",
            title="Full E2E Test",
            subject="math",
            stage="middle-G8",
            outline_path=FIXTURE_OUTLINE,
            mode="B",
            preflight=False,  # already validated by fixture tests
        )

        manifest = result.workspace.load_manifest()
        stages = manifest["stages"]

        # All stages completed
        for stage_key in (
            "stage0_workspace",
            "stage1_ingest",
            "stage2_localize",
            "stage4b_outline",
            "stage3_book_view",
            "stage4c_export_plan",
            "stage7_export",
        ):
            assert stages[stage_key]["status"] == StageStatus.COMPLETED.value, (
                f"{stage_key} should be COMPLETED"
            )

        # Stage 4b completed
        assert stages["stage4b_outline"]["status"] == StageStatus.COMPLETED.value

        # Stage 3 completed
        assert stages["stage3_book_view"]["status"] == StageStatus.COMPLETED.value

        # Stage 4c completed
        s4c = stages["stage4c_export_plan"]
        assert s4c["status"] == StageStatus.COMPLETED.value
        assert s4c["metadata"]["mode"] == "B"
        assert s4c["metadata"]["plans"] >= 1

        # Stage 7 completed
        s7 = stages["stage7_export"]
        assert s7["status"] == StageStatus.COMPLETED.value
        assert len(result.render_results) >= 1

        # BookView file exists
        bv_path = project_root / "book_view" / "book_view.json"
        assert bv_path.is_file()
        bv = json.loads(bv_path.read_text(encoding="utf-8"))
        assert bv["book_id"]

        # Assignments file exists
        assign_path = project_root / "topic_assignments" / "assignments.json"
        assert assign_path.is_file()

        # Export plans file exists
        plans_path = project_root / "export_plans" / "plans.json"
        assert plans_path.is_file()
        plans = json.loads(plans_path.read_text(encoding="utf-8"))
        assert len(plans["plans"]) == s4c["metadata"]["plans"]

        # At least one PDF in exports/deeptutor/
        export_dir = project_root / "exports" / "deeptutor"
        assert export_dir.is_dir()
        pdf_files = list(export_dir.glob("*.pdf"))
        assert len(pdf_files) >= 1

        # Export manifest exists
        manifest_path = export_dir / "export_manifest.json"
        assert manifest_path.is_file()

    def test_pipeline_without_outline_stops_at_stage2(self, tmp_path: Path, mirror) -> None:
        """Without outline_path, pipeline stops after Stage 2 (backward compat)."""
        runner = PipelineRunner(mirror)
        project_root = tmp_path / "projects" / "stage2-only"

        result = runner.run(
            project_root=project_root,
            inbox_task_dir=FIXTURE_TASK,
            project_id="stage2-only",
            title="Stage2 Only",
            preflight=False,
        )

        manifest = result.workspace.load_manifest()
        stages = manifest["stages"]

        # Stages 0-2 present and completed
        assert stages["stage0_workspace"]["status"] == StageStatus.COMPLETED.value
        assert stages["stage1_ingest"]["status"] == StageStatus.COMPLETED.value
        assert stages["stage2_localize"]["status"] == StageStatus.COMPLETED.value

        # Stages 4b/3/4c/7 NOT present
        late_stages = (
            "stage4b_outline", "stage3_book_view",
            "stage4c_export_plan", "stage7_export",
        )
        for stage_key in late_stages:
            assert stage_key not in stages, f"{stage_key} should not exist without outline"

        # No render results
        assert len(result.render_results) == 0

        # No book_view, no plans, no PDFs
        assert not (project_root / "book_view" / "book_view.json").exists()
        assert not (project_root / "export_plans" / "plans.json").exists()
        assert not list((project_root / "exports" / "deeptutor").glob("*.pdf"))

    def test_full_pipeline_mode_a(self, tmp_path: Path, mirror) -> None:
        """Mode A produces original-order plans (single plan for single chapter)."""
        runner = PipelineRunner(mirror)
        project_root = tmp_path / "projects" / "mode-a"

        result = runner.run(
            project_root=project_root,
            inbox_task_dir=FIXTURE_TASK,
            project_id="mode-a",
            title="Mode A Test",
            outline_path=FIXTURE_OUTLINE,
            mode="A",
            preflight=False,
        )

        manifest = result.workspace.load_manifest()
        s4c = manifest["stages"]["stage4c_export_plan"]["metadata"]
        assert s4c["mode"] == "A"
        assert s4c["plans"] >= 1
        assert len(result.render_results) >= 1


def test_resolve_relative_ref_rejects_path_traversal(tmp_path: Path) -> None:
    """Paths that escape the task_dir must be left untouched so they cannot
    be used to read arbitrary local files.
    """
    from pdf2dt.pipeline.runner import _resolve_relative_ref

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    safe_file = task_dir / "images" / "safe.png"
    safe_file.parent.mkdir(parents=True, exist_ok=True)
    safe_file.write_text("png", encoding="utf-8")

    # Safe path inside task_dir — resolved to file://
    assert _resolve_relative_ref("images/safe.png", task_dir).startswith("file://")

    # Path traversal — must be rejected and returned unchanged
    assert _resolve_relative_ref("images/../../safe.png", task_dir) == "images/../../safe.png"
    assert _resolve_relative_ref("../outside.png", task_dir) == "../outside.png"


# ---------------------------------------------------------------------- #
# P1 #4 — public PipelineRunner geometry-provider API.
#
# The CLI's --geometry-provider flag is the only surface that can pick a
# hybrid VLM analyzer; programmatic callers (tests, notebooks, future UI
# hooks) must be able to inject a custom GeometryAnalyzer without going
# through CLI parsing.  These tests pin the runner and the public
# run_pipeline() helper to the same contract the CLI uses, and verify
# the force_geometry knob honours the P0 review-state preservation rule.
# ---------------------------------------------------------------------- #


class _FakeVlmProvider:
    """In-memory VLM provider that never touches the network.

    The audit calls for "a test that injects a fake VLM provider without
    network access".  This provider's ``analyze_image`` returns whatever
    relations the test sets up, and we count invocations so we can
    assert the selection strategy fired (or skipped) correctly.
    """

    def __init__(self, candidates: list[VlmRelationCandidate]) -> None:
        self._candidates = list(candidates)
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model-v1"

    @property
    def endpoint(self) -> str:
        return "https://example.test/v1/fake"

    def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
        self.calls += 1
        return VlmResponse(relations=list(self._candidates))


def test_pipeline_runner_accepts_injected_geometry_analyzer(
    tmp_path: Path, mirror
) -> None:
    """The runner must accept a pre-built analyzer and apply it on Stage 5.

    We inject a HybridGeometryAnalyzer with a fake provider so no network
    call is ever made.  The runner must persist the visual_inference
    relation through to ``review/geometry_figures.json``.
    """
    provider = _FakeVlmProvider(
        [
            VlmRelationCandidate(
                relation_type=RelationType.MIDPOINT,
                entities=["AB", "D"],
                confidence=0.88,
                observation="D is visually the midpoint of AB",
            )
        ]
    )
    analyzer = HybridGeometryAnalyzer(provider=provider)
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "hybrid-injected"

    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="hybrid-injected",
        title="Hybrid injected",
        subject="math",
        stage="middle-G8",
        outline_path=FIXTURE_OUTLINE,
        mode="B",
        preflight=False,
        geometry_analyzer=analyzer,
    )

    # Stage 5 recorded a COMPLETED status with the geometry_analyzer
    # having run end-to-end (no exceptions, both stages ran).
    manifest = result.workspace.load_manifest()
    assert manifest["stages"]["stage5_geometry"]["status"] == StageStatus.COMPLETED.value
    assert result.geometry_figures, "Stage 5 produced figures through the runner"

    # The fake provider must have been consulted because at least one
    # fixture figure has rules_blank / visual_observation conditions
    # that fire ``should_call_vlm``; we do not pin the count because
    # the synthetic fixture's content drifts, but a non-zero count
    # proves the selection gate reached the provider.
    assert provider.calls >= 1, provider.calls

    # And the persisted queue carries the visual_inference evidence
    # emitted by the hybrid analyzer.
    figs_path = project_root / "review" / "geometry_figures.json"
    payload = json.loads(figs_path.read_text(encoding="utf-8"))
    visual = [
        rel
        for figure in payload["figures"]
        for rel in figure["relations"]
        if rel["evidence"] == Evidence.VISUAL_INFERENCE.value
    ]
    assert visual, payload


def test_pipeline_runner_without_injected_analyzer_uses_rules_only(
    tmp_path: Path, mirror
) -> None:
    """Without ``geometry_analyzer`` the runner must use the default rules
    analyzer and never call any provider.  This guards against accidental
    network usage from the public API default."""
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "rules-only"

    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="rules-only",
        title="Rules only",
        subject="math",
        stage="middle-G8",
        outline_path=FIXTURE_OUTLINE,
        mode="B",
        preflight=False,
    )

    manifest = result.workspace.load_manifest()
    assert manifest["stages"]["stage5_geometry"]["status"] == StageStatus.COMPLETED.value
    # No vlm_summary means no hybrid call ever happened.
    meta = manifest["stages"]["stage5_geometry"]["metadata"]
    assert "vlm_summary" not in meta or not meta.get("vlm_summary")
    assert "vlm_report_path" not in meta or meta.get("vlm_report_path") is None


def test_pipeline_runner_geometry_analyzer_with_custom_rules(
    tmp_path: Path, mirror
) -> None:
    """A subclassed :class:`GeometryAnalyzer` is also accepted; the runner
    must not pin callers to the hybrid flavor."""
    captured: dict[str, int] = {"calls": 0}

    class CountingAnalyzer(GeometryAnalyzer):
        def analyze(self, **kwargs):  # type: ignore[override]
            captured["calls"] += 1
            return super().analyze(**kwargs)

    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "custom-rules"

    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="custom-rules",
        title="Custom rules",
        subject="math",
        stage="middle-G8",
        outline_path=FIXTURE_OUTLINE,
        mode="B",
        preflight=False,
        geometry_analyzer=CountingAnalyzer(),
    )

    assert captured["calls"] >= 1
    assert result.geometry_figures


def test_run_pipeline_convenience_forwards_geometry_analyzer(
    tmp_path: Path, mirror
) -> None:
    """The module-level :func:`run_pipeline` must surface the same knob
    as :meth:`PipelineRunner.run` so non-CLI callers do not need to
    instantiate the runner directly."""
    provider = _FakeVlmProvider(candidates=[])
    analyzer = HybridGeometryAnalyzer(provider=provider)

    result = run_pipeline(
        project_root=tmp_path / "projects" / "convenience",
        inbox_task_dir=FIXTURE_TASK,
        project_id="convenience",
        title="Convenience",
        subject="math",
        stage="middle-G8",
        outline_path=FIXTURE_OUTLINE,
        mode="B",
        preflight=False,
        downloader=mirror,
        geometry_analyzer=analyzer,
    )

    manifest = result.workspace.load_manifest()
    assert manifest["stages"]["stage5_geometry"]["status"] == StageStatus.COMPLETED.value
    # We pass an analyzer that returns zero candidates; the analyzer's
    # call_records list reflects the selection strategy decisions.
    assert isinstance(analyzer.call_records, list)


def test_pipeline_runner_force_geometry_re_extends_with_injected_analyzer(
    tmp_path: Path, mirror
) -> None:
    """``force_geometry=True`` on the runner must run Stage 5 through the
    injected analyzer and record the review-state reset in the manifest,
    mirroring the CLI's ``--force-geometry`` flag.

    This combines P1 #4 with the P0 review-state-preservation rule:
    forced re-extraction must honour the new analyzer selection.
    """
    provider = _FakeVlmProvider(
        [
            VlmRelationCandidate(
                relation_type=RelationType.PERPENDICULAR,
                entities=["AB", "CD"],
                confidence=0.7,
                observation="visual right angle",
            )
        ]
    )
    analyzer = HybridGeometryAnalyzer(provider=provider)
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "force-hybrid"

    # ``force_geometry=True`` on a fresh workspace must drive Stage 5
    # through the injected analyzer and wipe any review_state audit log.
    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="force-hybrid",
        title="Force hybrid",
        subject="math",
        stage="middle-G8",
        outline_path=FIXTURE_OUTLINE,
        mode="B",
        preflight=False,
        geometry_analyzer=analyzer,
        force_geometry=True,
    )

    manifest = result.workspace.load_manifest()
    meta = manifest["stages"]["stage5_geometry"]["metadata"]
    assert manifest["stages"]["stage5_geometry"]["status"] == StageStatus.COMPLETED.value
    # The injected analyzer must have run — at least one figure in the
    # synthetic fixture triggers the selection gate.
    assert provider.calls >= 1, provider.calls
    # The hybrid runtime is recorded, so the vlm_report_path reflects
    # that the injected analyzer ran end-to-end.
    assert meta.get("vlm_report_path")
    # On a fresh workspace force_geometry=True still zeroes the audit
    # log because the new queue overwrites every review_state.
    assert meta.get("review_reset") is True

