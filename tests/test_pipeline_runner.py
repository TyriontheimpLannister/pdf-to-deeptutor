"""End-to-end test for the pipeline runner against the synthetic fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.assets import LocalMirrorDownloader
from pdf2dt.pipeline import PipelineRunner
from pdf2dt.project import StageStatus

FIXTURE_TASK = Path(__file__).resolve().parents[1] / "demos" / "inbox-sample" / "sample-chapter-01"


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
        subject="general",
        stage="sample",
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
    assert manifest["subject"]["subject"] == "general"
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


def test_pipeline_idempotent(tmp_path: Path, mirror) -> None:
    """Re-running against the same project_root should refuse to overwrite."""
    from pdf2dt.project import create_workspace

    runner = PipelineRunner(mirror)
    project_root = tmp_path / "p1"

    runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_TASK,
        project_id="p1",
        title="first",
    )

    # Second run on the same project_root must raise.
    with pytest.raises(FileExistsError):
        runner.run(
            project_root=project_root,
            inbox_task_dir=FIXTURE_TASK,
            project_id="p1",
            title="second",
        )
