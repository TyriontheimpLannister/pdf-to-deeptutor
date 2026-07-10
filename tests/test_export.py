"""Tests for Stage 4c export planner and Stage 7 PDF renderer.

Domain-neutral port of the upstream ``test_export.py``. It builds the
workspace end-to-end from the in-repo ``demos/inbox-sample/g8-triangle-ch03``
MinerU fixture and the domain-neutral ``outlines/sample-outline-v1.yaml``
outline, then asserts the planner/renderer's generic contracts (topic
grouping, persistence, outline provenance, Mode C bridges, CJK-safe
rendering). Subject-specific topic ids are never hard-coded.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pdf2dt.export import (
    ExportPlan,
    ExportPlanCollection,
    PdfRenderer,
    ReorgMode,
    plan_exports,
    render_exports,
)
from pdf2dt.export.renderer import _find_cjk_font
from pdf2dt.outlining import OutlineLoader
from pdf2dt.project import load_workspace

import fpdf

# fpdf2's non-CJK fallback in the renderer loads ``DejaVuSans.ttf`` from
# FPDF_FONT_DIR. Some fpdf2 builds (e.g. 2.8.x wheels) do not bundle it,
# so the "no CJK font" path cannot run there. Skip cleanly when absent;
# the test still exercises that path wherever DejaVu is available.
_FPDF_FONT_DIR = Path(getattr(fpdf, "FPDF_FONT_DIR", "") or Path(fpdf.__file__).parent)
_HAS_DEJAVU = (_FPDF_FONT_DIR / "DejaVuSans.ttf").is_file()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INBOX = PROJECT_ROOT / "demos" / "inbox-sample" / "g8-triangle-ch03"
OUTLINE = PROJECT_ROOT / "outlines" / "sample-outline-v1.yaml"


def _build_demo_workspace(tmp_path: Path) -> Path:
    """Run Stages 0-2-4b-3 on the in-repo fixture and return the workspace root."""
    ws_root = tmp_path / "demo-export"
    subprocess.run(
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
    return ws_root


def test_plan_exports_groups_by_topic(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)

    assert isinstance(collection, ExportPlanCollection)
    assert collection.mode.value == "B"
    topic_ids = [p.topic_id for p in collection.plans]
    assert "_misc" in topic_ids
    # At least one non-misc (topic-routed) plan must exist.
    non_misc = [p for p in collection.plans if not p.is_misc_fallback]
    assert non_misc, "expected at least one topic-routed plan"
    misc_plan = next(p for p in collection.plans if p.topic_id == "_misc")
    assert misc_plan.is_misc_fallback
    assert misc_plan.unclassified_count == len(misc_plan.items)
    for plan in non_misc:
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
    """Regression: plan_exports must surface the on-disk outline's real
    version, not a hard-coded default. Asserted dynamically so it holds
    for whatever version the fixture outline carries."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)

    outline = OutlineLoader().load(OUTLINE)
    assert collection.outline_used is not None
    assert collection.outline_used["version"] == outline.version
    assert collection.outline_used["outline_id"] == outline.outline_id
    assert collection.outline_used["sha256"] == outline.sha256

    for plan in collection.plans:
        assert plan.outline_used is not None
        assert plan.outline_used["version"] == outline.version

    persisted = json.loads(
        (ws_root / "export_plans" / "plans.json").read_text(encoding="utf-8")
    )
    assert persisted["outline_used"]["version"] == outline.version
    for plan in persisted["plans"]:
        assert plan["outline_used"]["version"] == outline.version


def test_pdf_footer_uses_real_outline_version(tmp_path: Path) -> None:
    """End-to-end: the rendered PDF carries the real outline id + version
    on its cover, not a stale hard-coded default."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)
    results = render_exports(ws)
    assert results, "no PDFs were rendered"

    outline = OutlineLoader().load(OUTLINE)
    expected_label = f"{outline.outline_id} v{outline.version}"

    pdf = results[0].output_path
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert expected_label in text, (
        f"PDF cover missing the real outline label {expected_label!r} "
        f"in {pdf.name}:\n{text[:400]}"
    )
    # The planner's legacy default sentinel must never leak into the PDF.
    assert "v0.0.0" not in text, (
        f"PDF footer shows stale v0.0.0 in {pdf.name}:\n{text[:400]}"
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
        json.loads((ws_root / "export_plans" / "plans.json").read_text())["plans"]
    )


@pytest.mark.skipif(
    not _HAS_DEJAVU,
    reason="DejaVuSans.ttf not bundled with fpdf2 in this environment; "
    "non-CJK fallback path cannot run",
)
def test_renderer_without_cjk_font(tmp_path: Path) -> None:
    """PdfRenderer must still produce output when no system CJK font exists."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="B", outline_path=OUTLINE)

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
        renderer = PdfRenderer(ws)
        results = renderer.render_collection(collection)
        assert all(r.output_path.is_file() for r in results)
    finally:
        renderer_mod._CANDIDATE_CJK_FONTS = saved


def test_mode_a_source_order(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="A", outline_path=OUTLINE)
    assert collection.mode.value == "A"
    assert len(collection.plans) >= 2


# ---------------------------------------------------------------------- #
# Mode C bridge insertion
# ---------------------------------------------------------------------- #


def _demo_workspace_has_min_two_plans(ws_root: Path) -> bool:
    plans_path = ws_root / "export_plans" / "plans.json"
    if not plans_path.is_file():
        return False
    data = json.loads(plans_path.read_text(encoding="utf-8"))
    return len(data.get("plans") or []) >= 2


def test_mode_c_inserts_one_bridge_per_adjacent_pair(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="C", outline_path=OUTLINE)
    if len(collection.plans) < 2:
        pytest.skip("fixture produced only one plan; cannot test pairs")
    expected_bridges = len(collection.plans) - 1
    total_bridges = sum(len(p.bridges) for p in collection.plans)
    assert total_bridges == expected_bridges, (
        f"Mode C expected {expected_bridges} bridges across "
        f"{len(collection.plans)} plans, got {total_bridges}"
    )
    for prev, curr in zip(collection.plans, collection.plans[1:]):
        assert len(curr.bridges) == 1, (
            f"plan {curr.plan_id} should have exactly one bridge, got {len(curr.bridges)}"
        )
        assert curr.bridges[0].follows_plan_id == prev.plan_id
        assert curr.bridges[0].provider == "mock"


def test_mode_c_persists_bridges_in_plans_json(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="C", outline_path=OUTLINE)
    if not _demo_workspace_has_min_two_plans(ws_root):
        pytest.skip("fixture produced only one plan")
    plans_path = ws_root / "export_plans" / "plans.json"
    data = json.loads(plans_path.read_text(encoding="utf-8"))
    total = sum(len(p.get("bridges") or []) for p in data["plans"])
    assert total == len(data["plans"]) - 1, (
        f"persisted plans.json must carry one bridge per adjacent pair: "
        f"total={total}, plans={len(data['plans'])}"
    )
    second = data["plans"][1]
    assert "bridges" in second and len(second["bridges"]) == 1
    bridge = second["bridges"][0]
    assert bridge["provider"] == "mock"
    assert bridge["follows_plan_id"] == data["plans"][0]["plan_id"]


@pytest.mark.skipif(
    _find_cjk_font() is None,
    reason="CJK font required to render the Chinese mock-bridge body",
)
def test_mode_c_renders_bridge_text_in_pdf(tmp_path: Path) -> None:
    """A rendered Mode C PDF must embed the bridge body on the second (and
    later) plan; the first plan must contain none."""
    from pypdf import PdfReader

    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    plan_exports(ws, mode="C", outline_path=OUTLINE)
    if not _demo_workspace_has_min_two_plans(ws_root):
        pytest.skip("fixture produced only one plan")
    results = render_exports(ws)
    assert len(results) >= 2

    def _pdf_text(p: Path) -> str:
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)

    bridge_body = "本节承接自"
    first_pdf_text = _pdf_text(results[0].output_path)
    second_pdf_text = _pdf_text(results[1].output_path)
    assert bridge_body not in first_pdf_text, (
        f"first plan should not embed a mock bridge, got: {first_pdf_text[:300]}"
    )
    assert bridge_body in second_pdf_text, (
        f"second plan should embed a mock bridge, got: {second_pdf_text[:300]}"
    )


def test_mode_b_does_not_insert_bridges(tmp_path: Path) -> None:
    """Mode B must never insert bridges; Mode C is the only mode that uses
    the BridgeProvider."""
    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(ws, mode="B", outline_path=OUTLINE)
    for plan in collection.plans:
        assert plan.bridges == [], (
            f"Mode B plan {plan.plan_id} unexpectedly carried bridges: {plan.bridges}"
        )


def test_bridge_provider_raises_is_captured_not_propagated(tmp_path: Path) -> None:
    """A misbehaving provider that raises must degrade gracefully: the
    planner records an explicit ``[bridge-error]`` bridge so review tools
    can see what went wrong, but the rest of the collection still plans."""
    from pdf2dt.export.bridges import Bridge, BridgeContext, BridgeProvider

    class FlakyProvider:
        name = "flaky"

        def generate_bridge(self, context: BridgeContext) -> Bridge:
            raise RuntimeError("simulated outage")

    ws_root = _build_demo_workspace(tmp_path)
    ws = load_workspace(ws_root)
    collection = plan_exports(
        ws, mode="C", outline_path=OUTLINE, bridge_provider=FlakyProvider()
    )
    if len(collection.plans) < 2:
        pytest.skip("fixture produced only one plan")
    bridge_count = sum(len(p.bridges) for p in collection.plans)
    assert bridge_count == len(collection.plans) - 1
    for prev, curr in zip(collection.plans, collection.plans[1:]):
        assert len(curr.bridges) == 1
        assert "[bridge-error]" in curr.bridges[0].text
        assert curr.bridges[0].provider.endswith("-error")
