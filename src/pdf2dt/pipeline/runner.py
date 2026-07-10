"""Pipeline runner — orchestrates Stages 0, 1, and 2 for the MVP.

Stage 0 — workspace creation
Stage 1 — MinerU inbox ingestion
Stage 2 — asset localization

Stage 3+ is intentionally not yet wired here; the runner is the seam where
later stages will hook in.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..assets import AssetLocalizer, AssetRegistry, AssetDownloader
from ..inbox import InboxLoader
from ..project import (
    ProjectWorkspace,
    StageStatus,
    create_workspace,
    record_stage,
)


@dataclass
class PipelineResult:
    workspace: ProjectWorkspace
    asset_registry: AssetRegistry


class PipelineRunner:
    """Run the early pipeline stages end-to-end."""

    def __init__(self, downloader: AssetDownloader) -> None:
        self._downloader = downloader

    def run(
        self,
        *,
        project_root: Path | str,
        inbox_task_dir: Path | str,
        project_id: str,
        title: str,
        subject: str | None = "math",
        stage: str | None = "middle-G8",
    ) -> PipelineResult:
        inbox_task_dir = Path(inbox_task_dir)

        # Stage 0 — workspace
        workspace = create_workspace(
            project_root,
            project_id=project_id,
            title=title,
            subject=subject,
            stage=stage,
        )
        record_stage(
            workspace,
            "stage0_workspace",
            status=StageStatus.COMPLETED,
            metadata={"project_id": project_id},
        )

        # Stage 1 — ingest MinerU output
        loaded = InboxLoader(inbox_task_dir.parent).load(inbox_task_dir)
        workspace.copy_mineru_raw(inbox_task_dir)

        # Persist the rewritten copies (URLs will be rewritten in Stage 2).
        raw_md = workspace.mineru_raw_dir / loaded.task.task_dir.name / loaded.task.meta.products.markdown
        normalized_md = workspace.normalized_dir / "full.md"
        normalized_md.write_text(loaded.markdown_text, encoding="utf-8")

        layout_copied: Path | None = None
        if loaded.layout_data is not None:
            layout_copied = workspace.normalized_dir / "layout.json"
            layout_copied.write_text(
                json.dumps(loaded.layout_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        record_stage(
            workspace,
            "stage1_ingest",
            status=StageStatus.COMPLETED,
            input_fingerprint=_sha256_file(raw_md) if raw_md.is_file() else None,
            output_fingerprint=_sha256_file(normalized_md),
            metadata={
                "task_id": loaded.task_id,
                "image_references_count": len(loaded.image_references),
                "layout_included": loaded.layout_data is not None,
            },
        )

        # Stage 2 — localize assets. Resolve MinerU-relative references like
        # ``images/<hash>.jpg`` against the task directory before passing
        # them to the downloader.
        resolved_refs = [
            _resolve_relative_ref(url, inbox_task_dir) for url in loaded.image_references
        ]
        loaded_local = loaded.model_copy(update={"image_references": resolved_refs})
        localizer = AssetLocalizer(workspace.assets_dir, self._downloader)
        registry = localizer.localize(loaded_local)

        # Rewrite references inside the normalized copies.
        rewritten_md = localizer.rewrite_markdown(loaded.markdown_text, registry)
        normalized_md.write_text(rewritten_md, encoding="utf-8")

        rewritten_layout_path: Path | None = None
        if loaded.layout_data is not None:
            rewritten_layout = localizer.rewrite_layout(loaded.layout_data, registry)
            rewritten_layout_path = workspace.normalized_dir / "layout.localized.json"
            rewritten_layout_path.write_text(
                json.dumps(rewritten_layout, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Persist the asset registry for downstream stages.
        registry_path = workspace.normalized_dir / "assets_registry.json"
        registry_path.write_text(
            json.dumps(_registry_to_dict(registry), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        record_stage(
            workspace,
            "stage2_localize",
            status=StageStatus.COMPLETED,
            output_fingerprint=_sha256_file(registry_path),
            metadata={
                "assets_localized": len(registry),
                "registry_path": str(registry_path.relative_to(workspace.root)),
                "rewritten_markdown": str(normalized_md.relative_to(workspace.root)),
                "rewritten_layout": (
                    str(rewritten_layout_path.relative_to(workspace.root))
                    if rewritten_layout_path is not None
                    else None
                ),
            },
        )

        return PipelineResult(workspace=workspace, asset_registry=registry)


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def run_pipeline(
    *,
    project_root: Path | str,
    inbox_task_dir: Path | str,
    project_id: str,
    title: str,
    downloader: AssetDownloader,
    subject: str | None = "math",
    stage: str | None = "middle-G8",
) -> PipelineResult:
    return PipelineRunner(downloader).run(
        project_root=project_root,
        inbox_task_dir=inbox_task_dir,
        project_id=project_id,
        title=title,
        subject=subject,
        stage=stage,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_relative_ref(url: str, task_dir: Path) -> str:
    """Translate a MinerU-relative path like ``images/foo.jpg`` into a
    file:// URL that the downloader can read directly. Remote URLs and
    existing file:// URLs pass through untouched.
    """
    if url.startswith(("http://", "https://", "file://")):
        return url
    if url.startswith("images/") or url.startswith("./") or "/" not in url:
        abs_path = (task_dir / url).resolve()
        if abs_path.is_file():
            return abs_path.as_uri()
    return url


def _registry_to_dict(registry: AssetRegistry) -> dict[str, Any]:
    return {
        "count": len(registry),
        "by_url": registry.by_url,
        "assets": [
            {
                "asset_id": a.asset_id,
                "sha256": a.sha256,
                "mime_type": a.mime_type,
                "byte_size": a.byte_size,
                "width": a.width,
                "height": a.height,
                "local_path": str(a.local_path),
                "source_url": a.source_url,
                "source_page": a.source_page,
            }
            for a in registry.by_id.values()
        ],
    }