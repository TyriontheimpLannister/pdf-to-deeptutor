"""Smoke tests for Stage 4c export planner and Stage 7 PDF renderer."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pdf2dt.export import (
    ExportPlan,
    ExportPlanCollection,
    PdfRenderer,
    ReorgMode,
    plan_exports,
    render_exports,
)
from pdf2dt.project import load_workspace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INBOX = PROJECT_ROOT / "demos/inbox-sample" / "g8-triangle-ch03"
OUTLINE = PROJECT_ROOT / "outlines" / "elementary-math-v1.yaml"


def _build_demo_workspace(tmp_path: Path) -> Path:
    """Run Stages 0-2-4b-3 on the synthetic fixture and return workspace path."""
    ws_root = tmp_path / "demo-export"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
            "--project-root",
            str(ws_root),
            "--inbox",
            str(FIXTURE_INBOX),
            "--project-id",
            "demo-export",
            "--title",
            "Demo Export",
            "--downloader",
            "local",
            "--mirror",
            str(FIXTURE_INBOX / "images"),
            "--outline",
            str(OUTLINE),
            "--book-view",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "[stage3]" in result.stdout, result.stdout
    return ws_root


def test_cli_runs_geometry_before_export(tmp_path: Path) -> None:
    """Geometry output must exist before Stage 7 renders the PDFs."""
    ws_root = tmp_path / "geometry-before-export"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
            "--project-root",
            str(ws_root),
            "--inbox",
            str(FIXTURE_INBOX),
            "--project-id",
            "geometry-before-export",
            "--title",
            "Geometry Before Export",
            "--downloader",
            "local",
            "--mirror",
            str(FIXTURE_INBOX / "images"),
            "--outline",
            str(OUTLINE),
            "--book-view",
            "--geometry",
            "--export",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.index("[stage5]") < result.stdout.index("[stage4c]")
    assert result.stdout.index("[stage5]") < result.stdout.index("[stage7]")
    assert (ws_root / "review" / "geometry_figures.json").is_file()
    manifest = json.loads(
        (ws_root / "exports" / "deeptutor" / "export_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["plans_rendered"] > 0


def test_plan_exports_groups_by_topic(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)

    assert isinstance(collection, ExportPlanCollection)
    assert collection.mode.value == "B"
    topic_ids = [p.topic_id for p in collection.plans]
    assert "geometry-plane-triangles" in topic_ids
    assert "_misc" in topic_ids
    misc_plan = next(p for p in collection.plans if p.topic_id == "_misc")
    assert misc_plan.is_misc_fallback
    assert misc_plan.unclassified_count == len(misc_plan.items)
    # Every non-misc plan must contain at least one item.
    for plan in collection.plans:
        if not plan.is_misc_fallback:
            assert len(plan.items) >= 1


def test_plan_exports_persists_plans_json(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)

    plans_path = ws_root / "export_plans" / "plans.json"
    assert plans_path.is_file()
    data = json.loads(plans_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "export_plan/v1"
    assert data["project_id"] == "demo-export"
    assert data["mode"] == "B"
    assert len(data["plans"]) >= 2
    for plan in data["plans"]:
        assert "plan_id" in plan
        assert "topic_id" in plan
        assert "output_filename" in plan
        assert plan["output_filename"].endswith(".pdf")


def test_render_exports_produces_pdfs(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)
    results = render_exports(ws)

    assert len(results) >= 2
    for r in results:
        assert r.output_path.is_file()


def test_outline_provenance_tracks_real_version(tmp_path: Path) -> None:
    """Regression: previously, plan_exports() would persist the
    outline version as the literal string ``"1.0.0"`` whenever
    ``--outline`` was supplied, ignoring the outline on disk. After
    bumping the elementary-math outline to v1.0.1 the PDF footer
    kept reading v1.0.0. This test pins the contract: when the
    outline file is at v1.0.1, every plan and the collection must
    report ``version == "1.0.1"``.
    """
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)

    # Sanity: the on-disk outline really is v1.0.1 (otherwise the
    # test would silently pass against stale v1.0.0 fixtures).
    from pdf2dt.outlining import OutlineLoader
    outline = OutlineLoader().load(OUTLINE)
    assert outline.version == "1.0.1", (
        "Test assumption broken: outline on disk is no longer v1.0.1. "
        "Update the assertion or bump the test fixture."
    )

    # Collection-level provenance.
    assert collection.outline_used is not None
    assert collection.outline_used["version"] == outline.version
    assert collection.outline_used["outline_id"] == outline.outline_id
    assert collection.outline_used["sha256"] == outline.sha256

    # Per-plan provenance.
    for plan in collection.plans:
        assert plan.outline_used is not None
        assert plan.outline_used["version"] == outline.version, (
            f"plan {plan.plan_id} carries stale outline version "
            f"{plan.outline_used['version']!r}"
        )

    # And the persisted plans.json must reflect the same value, since
    # the PDF footer reads it from disk during Stage 7.
    plans_path = ws_root / "export_plans" / "plans.json"
    persisted = json.loads(plans_path.read_text(encoding="utf-8"))
    assert persisted["outline_used"]["version"] == outline.version
    for plan in persisted["plans"]:
        assert plan["outline_used"]["version"] == outline.version


def test_pdf_footer_uses_real_outline_version(tmp_path: Path) -> None:
    """End-to-end: render a single PDF and confirm the outline
    version string is baked into the footer. Pins the contract that
    Stage 7 surfaces the real outline version, not the hard-coded
    default.

    We use pypdf (already an indirect dependency via fpdf2's own
    tooling) instead of shelling out to ``pdftotext`` so this test
    runs in any environment without requiring poppler on PATH.
    """
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)
    results = render_exports(ws)
    assert results, "no PDFs were rendered"

    pdf = results[0].output_path
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    # The PDF footer line reads e.g. "Outline: elementary-math-v1 v1.0.1".
    # We do not want it to say v1.0.0 any more.
    assert "v1.0.0" not in text, (
        f"PDF footer still shows stale v1.0.0 in {pdf.name}:\n{text[:400]}"
    )
    assert "elementary-math-v1 v1.0.1" in text, (
        f"PDF footer missing the bumped outline version in {pdf.name}:"
        f"\n{text[:400]}"
    )

    manifest_path = ws_root / "exports" / "deeptutor" / "export_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plans_rendered"] == len(results)
    assert len(manifest["files"]) == len(results)


def test_export_manifest_records_stages(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)
    render_exports(ws)

    manifest = ws.load_manifest()
    stages = manifest.get("stages") or {}
    assert stages.get("stage4c_export_plan", {}).get("status") == "completed"
    assert stages.get("stage7_export", {}).get("status") == "completed"
    assert stages["stage4c_export_plan"]["metadata"]["plans"] == len(
        json.loads(
            (ws_root / "export_plans" / "plans.json").read_text(encoding="utf-8")
        )["plans"]
    )


def test_force_mode_b_respected_in_cli_planning(tmp_path: Path) -> None:
    """When force_mode=True is passed to ExportPlanner, outline overrides
    are ignored even for ReorgMode.B.
    """
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)

    from pdf2dt.export import ExportPlanner, ReorgMode
    from pdf2dt.outlining import OutlineLoader

    outline = OutlineLoader().load(OUTLINE)
    # Inject a fake override so we can observe it being blocked.
    from dataclasses import replace as dc_replace

    outline_with_override = dc_replace(
        outline,
        strategy_overrides={**outline.strategy_overrides, "geometry-plane-triangles": "A"},
    )

    bv_path = ws.book_view_dir / "book_view.json"
    book = json.loads(bv_path.read_text(encoding="utf-8"))

    collection = ExportPlanner(
        book,
        mode=ReorgMode.B,
        force_mode=True,
        project_id=ws.root.name,
        outline=outline_with_override,
    ).plan()
    for plan in collection.plans:
        if plan.topic_id == "geometry-plane-triangles":
            assert plan.mode == ReorgMode.B, (
                f"force_mode=True did not block outline override: "
                f"plan {plan.plan_id} has mode {plan.mode.value}"
            )


def test_render_manifest_records_per_file_mode_and_requested_mode(
    tmp_path: Path,
) -> None:
    """Export manifest must record the actual mode of each plan and the
    collection-level requested_mode.
    """
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)
    results = render_exports(ws)

    manifest_path = ws_root / "exports" / "deeptutor" / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert "requested_mode" in manifest
    assert manifest["requested_mode"] == "B"
    assert "mode" not in manifest  # old top-level key renamed

    for file_entry, result in zip(manifest["files"], results, strict=True):
        assert file_entry.get("mode") == result.plan_mode

    project_manifest = ws.load_manifest()
    assert {
        entry["mode"] for entry in project_manifest["exports"]
    } == {result.plan_mode for result in results}


class TestFigureValidation:
    """Tests for per-export figure validation and validation_status."""

    def test_validation_status_ready_when_all_figures_present(self, tmp_path: Path) -> None:
        """When all figures embed successfully, status is 'ready'."""
        from pdf2dt.export.renderer import RenderResult

        r = RenderResult(
            output_path=tmp_path / "dummy.pdf",
            plan_id="p1",
            item_count=3,
            figure_count=2,
            missing_figures=[],
        )
        r.finalise()
        assert r.validation_status == "ready"

    def test_validation_status_warning_when_some_missing(self, tmp_path: Path) -> None:
        """When some figures are missing, status is 'warning'."""
        from pdf2dt.export.renderer import RenderResult

        r = RenderResult(
            output_path=tmp_path / "dummy.pdf",
            plan_id="p2",
            item_count=3,
            figure_count=2,
            missing_figures=["fig-missing-1"],
        )
        r.finalise()
        assert r.validation_status == "warning"

    def test_validation_status_blocked_when_all_missing(self, tmp_path: Path) -> None:
        """When no figures embed and some are referenced, status is 'blocked'."""
        from pdf2dt.export.renderer import RenderResult

        r = RenderResult(
            output_path=tmp_path / "dummy.pdf",
            plan_id="p3",
            item_count=3,
            figure_count=0,
            missing_figures=["fig-a", "fig-b"],
        )
        r.finalise()
        assert r.validation_status == "blocked"

    def test_exports_array_written_to_project_manifest(self, tmp_path: Path) -> None:
        """After render_exports(), project.json must have a populated exports array."""
        ws_root = _build_demo_workspace(tmp_path)
        ws = load_workspace(ws_root)
        plan_exports(ws, mode="B", outline_path=OUTLINE)
        results = render_exports(ws)

        manifest = ws.load_manifest()
        exports = manifest.get("exports") or []
        assert len(exports) == len(results)
        for entry in exports:
            assert "export_id" in entry
            assert "path" in entry
            assert "sha256" in entry
            assert "validation_status" in entry

    def test_export_manifest_has_per_file_validation(self, tmp_path: Path) -> None:
        """export_manifest.json must carry missing_figures and validation_status per file."""
        ws_root = _build_demo_workspace(tmp_path)
        ws = load_workspace(ws_root)
        plan_exports(ws, mode="B", outline_path=OUTLINE)
        render_exports(ws)

        export_manifest_path = ws_root / "exports" / "deeptutor" / "export_manifest.json"
        data = json.loads(export_manifest_path.read_text(encoding="utf-8"))
        for file_entry in data["files"]:
            assert "validation_status" in file_entry
            assert "missing_figures" in file_entry
            assert isinstance(file_entry["missing_figures"], list)


def test_renderer_without_cjk_font(tmp_path: Path) -> None:
    """PdfRenderer must produce output even when no system CJK font exists."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)

    renderer = PdfRenderer(ws)
    # Temporarily rename CJK font paths to simulate missing font.
    from pdf2dt.export import renderer as renderer_mod
    saved = list(renderer_mod._CANDIDATE_CJK_FONTS)
    renderer_mod._CANDIDATE_CJK_FONTS = [Path("/__nonexistent__/font.ttf")]
    try:
        collection_data = json.loads(
            (ws_root / "export_plans" / "plans.json").read_text(encoding="utf-8")
        )
        collection = ExportPlanCollection(
            project_id=collection_data["project_id"],
            generated_at=collection_data["generated_at"],
            mode=ReorgMode(collection_data["mode"]),
            outline_used=collection_data.get("outline_used"),
            plans=[ExportPlan.from_dict(p) for p in collection_data["plans"]],
        )
        results = renderer.render_collection(collection)
        assert all(r.output_path.is_file() for r in results)
    finally:
        renderer_mod._CANDIDATE_CJK_FONTS = saved


def test_mode_a_source_order(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="A", outline_path=OUTLINE)
    assert collection.mode.value == "A"
    # At least one plan should exist; source order does not change
    # plan count.
    assert len(collection.plans) >= 2


# ---------------------------------------------------------------------- #
# Mode C bridge insertion (Next Steps #2)
# ---------------------------------------------------------------------- #


def _demo_workspace_has_min_two_plans(ws_root: Path) -> bool:
    """Helper: Mode C behaviour only kicks in for collections with
    two or more plans. Skip otherwise.
    """
    plans_path = ws_root / "export_plans" / "plans.json"
    if not plans_path.is_file():
        return False
    import json as _json
    data = _json.loads(plans_path.read_text(encoding="utf-8"))
    return len(data.get("plans") or []) >= 2


def test_mode_c_inserts_one_bridge_per_adjacent_pair(tmp_path: Path) -> None:
    """In Mode C every plan after the first must carry exactly one
    bridge, and the bridge must reference the immediately preceding
    plan. Plans before the second must have no bridges (the
    collection opens without a predecessor)."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="C", outline_path=OUTLINE)
    if len(collection.plans) < 2:
        pytest.skip("demo fixture produced only one plan; cannot test pairs")
    expected_bridges = len(collection.plans) - 1
    total_bridges = sum(len(p.bridges) for p in collection.plans)
    assert total_bridges == expected_bridges, (
        f"Mode C expected {expected_bridges} bridges across "
        f"{len(collection.plans)} plans, got {total_bridges}"
    )
    # Every plan except the first should have exactly one bridge.
    for prev, curr in zip(collection.plans, collection.plans[1:], strict=False):
        assert len(curr.bridges) == 1, (
            f"plan {curr.plan_id} should have exactly one bridge, "
            f"got {len(curr.bridges)}"
        )
        assert curr.bridges[0].follows_plan_id == prev.plan_id
        assert curr.bridges[0].provider == "mock"


def test_mode_c_persists_bridges_in_plans_json(tmp_path: Path) -> None:
    """The persisted plans.json must include the bridges so a
    renderer loaded from disk can rebuild the document verbatim.
    """
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="C", outline_path=OUTLINE)
    if not _demo_workspace_has_min_two_plans(ws_root):
        pytest.skip("demo fixture produced only one plan")
    plans_path = ws_root / "export_plans" / "plans.json"
    import json as _json
    data = _json.loads(plans_path.read_text(encoding="utf-8"))
    total = sum(len(p.get("bridges") or []) for p in data["plans"])
    assert total == len(data["plans"]) - 1, (
        f"persisted plans.json must carry one bridge per adjacent "
        f"pair: total={total}, plans={len(data['plans'])}"
    )
    # Bridge dicts preserve provenance.
    second = data["plans"][1]
    assert "bridges" in second and len(second["bridges"]) == 1
    bridge = second["bridges"][0]
    assert bridge["provider"] == "mock"
    assert bridge["follows_plan_id"] == data["plans"][0]["plan_id"]


def test_mode_c_renders_bridge_text_in_pdf(tmp_path: Path) -> None:
    """A rendered Mode C PDF must contain the bridge text exactly
    once at the top of the second (and later) plan; the very
    first plan must contain zero bridge text. Uses pypdf to read
    the rendered PDF (no poppler dependency)."""
    from pypdf import PdfReader
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="C", outline_path=OUTLINE)
    if not _demo_workspace_has_min_two_plans(ws_root):
        pytest.skip("demo fixture produced only one plan")
    results = render_exports(ws)
    assert len(results) >= 2

    def _pdf_text(p: Path) -> str:
        return "\n".join(
            (pg.extract_text() or "") for pg in PdfReader(str(p)).pages
        )

    # The first rendered PDF must not contain the bridge body text;
    # the second must contain the bridge. fpdf2's CJK text wrapper
    # inserts spaces around ASCII brackets, so we probe the CJK
    # body of the placeholder rather than the literal "[Mock bridge]".
    bridge_body = "本节承接自"
    first_pdf_text = _pdf_text(results[0].output_path)
    second_pdf_text = _pdf_text(results[1].output_path)
    assert bridge_body not in first_pdf_text, (
        f"first plan should not embed a mock bridge, got: "
        f"{first_pdf_text[:300]}"
    )
    assert bridge_body in second_pdf_text, (
        f"second plan should embed a mock bridge, got: "
        f"{second_pdf_text[:300]}"
    )


def test_mode_b_does_not_insert_bridges(tmp_path: Path) -> None:
    """Mode B must never insert bridges; Mode C is the only mode
    that uses the BridgeProvider. This pins the contract that
    Mode B remains the deterministic word-preserving v1 path."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)
    for plan in collection.plans:
        assert plan.bridges == [], (
            f"Mode B plan {plan.plan_id} unexpectedly carried "
            f"bridges: {plan.bridges}"
        )


def test_bridge_provider_raises_is_captured_not_propagated(tmp_path: Path) -> None:
    """A misbehaving provider that raises must degrade gracefully:
    the planner records an explicit ``[bridge-error]`` bridge so
    review tools can see what went wrong, but the rest of the
    collection still plans normally."""
    from pdf2dt.export.bridges import Bridge, BridgeContext

    class FlakyProvider:
        name = "flaky"

        def generate_bridge(self, context: BridgeContext) -> Bridge:
            raise RuntimeError("simulated outage")

        def attach_context(self, context: object) -> None:
            return None

    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(
        ws, mode="C", outline_path=OUTLINE,
        bridge_provider=FlakyProvider(),  # type: ignore[arg-type]
    )
    if len(collection.plans) < 2:
        pytest.skip("demo fixture produced only one plan")
    # Every bridge in the collection must be a recorded error.
    bridge_count = sum(len(p.bridges) for p in collection.plans)
    assert bridge_count == len(collection.plans) - 1
    for _prev, curr in zip(collection.plans, collection.plans[1:], strict=False):
        assert len(curr.bridges) == 1
        assert "[bridge-error]" in curr.bridges[0].text
        assert curr.bridges[0].provider.endswith("-error")


def test_custom_bridge_provider_can_be_selected_by_name(tmp_path: Path) -> None:
    """Custom providers registered via the Python API must be selectable
    by string name in plan_exports (regression: CLI used to restrict
    choices to mock/noop only).
    """
    from pdf2dt.export.bridges import (
        Bridge,
        BridgeContext,
        register_bridge_provider,
    )

    class CountingProvider:
        name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        def attach_context(self, _ctx: Any) -> None:
            pass

        def generate_bridge(self, ctx: BridgeContext, _metadata: dict | None = None) -> Bridge:
            self.calls += 1
            return Bridge(
                text=f"[counting-bridge {self.calls}]",
                follows_plan_id=ctx.follows_plan_id,
                follows_topic_id=ctx.follows_topic_id,
                provider="counting",
            )

    register_bridge_provider(CountingProvider())

    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(
        ws, mode="C", outline_path=OUTLINE, bridge_provider="counting"
    )

    if len(collection.plans) < 2:
        pytest.skip("demo fixture produced only one plan")

    assert any(
        bridge.provider == "counting" and "counting-bridge" in bridge.text
        for plan in collection.plans
        for bridge in plan.bridges
    )
