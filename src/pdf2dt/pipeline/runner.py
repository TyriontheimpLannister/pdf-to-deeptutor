"""Pipeline runner — orchestrates the full processing pipeline.

Stages orchestrated:

0 — workspace creation
1 — MinerU inbox ingestion
2 — asset localization
2.5 — document-structure recovery
4b — topic matching (outline → assignments)
3 — BookView construction
4c — export planning
7 — PDF rendering

Stages 4b/3/4c/7 are optional and controlled by the ``outline_path`` and
``mode`` parameters.  When ``outline_path`` is ``None``, only Stages 0-2
run (original MVP behaviour, preserved for backward compatibility).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..assets import AssetDownloader, AssetLocalizer, AssetRegistry
from ..bookview.builder import build_book_view
from ..document_structure import recover_document_structure
from ..export.planner import plan_exports
from ..export.renderer import render_exports
from ..geometry import GeometryAnalyzer, analyze_geometry
from ..inbox import InboxLoader
from ..outlining.matcher import match_project
from ..preflight import PreFlightChecker
from ..project import (
    ProjectWorkspace,
    StageStatus,
    create_workspace,
    is_stage_completed,
    load_workspace,
    record_stage,
)
from ..review import ReviewDecision, apply_review


@dataclass
class PipelineResult:
    """Result of a completed pipeline run."""

    workspace: ProjectWorkspace
    asset_registry: AssetRegistry
    render_results: list[Any] = field(default_factory=list)
    """Populated when Stage 7 (export) runs; one entry per rendered PDF."""
    geometry_figures: list[Any] = field(default_factory=list)
    """Populated when Stage 5 (geometry) runs."""
    review_decisions: list[Any] = field(default_factory=list)
    """Populated when Stage 6 (review) runs."""


class AssetLocalizationError(RuntimeError):
    """Raised when one or more discovered image references are not localizable."""


class PreFlightFailureError(RuntimeError):
    """Raised when pre-flight checks find blocking errors.

    The :attr:`report` attribute holds the full structured report so the
    caller can inspect individual check results before deciding what to do.
    """

    def __init__(self, report: Any) -> None:
        self.report = report
        errors = report.errors
        msg = f"Pre-flight check failed with {len(errors)} error(s):\n"
        for err in errors:
            msg += f"  [{err.name}] {err.message}\n"
        super().__init__(msg)


class PipelineRunner:
    """Run the full pipeline end-to-end.

    Stages 0-2.5 always run.  Stages 4b/3/4c/7 run only when
    ``outline_path`` is provided.  This preserves backward compatibility:
    callers that pass no ``outline_path`` get the original Stage 0-2
    behaviour.
    """

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
        outline_path: Path | str | None = None,
        mode: str = "B",
        force_mode: bool = False,
        preflight: bool = True,
        review_decisions: list[ReviewDecision] | None = None,
        geometry_analyzer: GeometryAnalyzer | None = None,
        force_geometry: bool = False,
    ) -> PipelineResult:
        inbox_task_dir = Path(inbox_task_dir)

        # Pre-flight — validate MinerU output before creating a workspace.
        if preflight:
            checker = PreFlightChecker(inbox_task_dir.parent)
            report = checker.check(inbox_task_dir)
            if not report.should_proceed:
                raise PreFlightFailureError(report)

        # ---- Stages 0, 1, 2 (always) ----
        workspace, registry = self._run_ingest(
            project_root=project_root,
            inbox_task_dir=inbox_task_dir,
            project_id=project_id,
            title=title,
            subject=subject,
            stage=stage,
        )

        # ---- Stages 4b, 3, 5, 6, 4c, 7 (when outline provided) ----
        render_results: list[Any] = []
        geometry_figures: list[Any] = []
        applied_decisions: list[Any] = []
        if outline_path is not None:
            outline_path = Path(outline_path)

            # Stage 4b — topic matching
            self._run_topic_match(workspace, outline_path)

            # Stage 3 — BookView construction
            self._run_book_view(workspace)

            # Stage 5 — geometry analysis (skipped on resume).
            geometry_figures = self._run_geometry(
                workspace,
                analyzer=geometry_analyzer,
                force=force_geometry,
            )

            # Stage 6 — review apply. When the caller supplies
            # explicit decisions we apply them on every run so users
            # can iterate on review edits without re-extracting
            # geometry. When no decisions are supplied we honour
            # the resumability contract: if stage6_review is already
            # completed, we record SKIPPED and load the existing
            # audit log; otherwise we record the stage as completed
            # with the current queue (a no-op edit) so the manifest
            # reflects that the stage was reached.
            if review_decisions:
                applied_decisions, _ = apply_review(workspace, review_decisions)
            else:
                from ..review import ReviewStateStore
                if is_stage_completed(workspace, "stage6_review"):
                    record_stage(
                        workspace, "stage6_review", status=StageStatus.SKIPPED
                    )
                else:
                    applied_decisions, _ = apply_review(workspace, [])
                store = ReviewStateStore(workspace)
                applied_decisions = store.load_state()

            # Stage 4c — export planning
            self._run_export_plan(
                workspace, outline_path=outline_path, mode=mode, force_mode=force_mode
            )

            # Stage 7 — PDF rendering
            render_results = self._run_export(workspace)

        return PipelineResult(
            workspace=workspace,
            asset_registry=registry,
            render_results=render_results,
            geometry_figures=geometry_figures,
            review_decisions=applied_decisions,
        )

    # ------------------------------------------------------------------ #
    # Stage 0-2.5 group (ingest and deterministic structure recovery)
    # ------------------------------------------------------------------ #

    def _run_ingest(
        self,
        *,
        project_root: Path | str,
        inbox_task_dir: Path,
        project_id: str,
        title: str,
        subject: str | None,
        stage: str | None,
    ) -> tuple[ProjectWorkspace, AssetRegistry]:
        """Run Stages 0, 1, 2, and 2.5 — resuming completed stages."""
        inbox_task_dir = Path(inbox_task_dir)

        # Stage 0 — workspace (create or resume)
        workspace = self._ensure_workspace(
            project_root,
            project_id=project_id,
            title=title,
            subject=subject,
            stage=stage,
        )

        # Stage 1 — ingest MinerU output
        if is_stage_completed(workspace, "stage1_ingest"):
            record_stage(
                workspace, "stage1_ingest", status=StageStatus.SKIPPED,
            )
        else:
            self._run_stage1(workspace, inbox_task_dir)

        # Stage 2 — localize assets
        if is_stage_completed(workspace, "stage2_localize"):
            record_stage(
                workspace, "stage2_localize", status=StageStatus.SKIPPED,
            )
            registry = self._load_existing_registry(workspace)
        else:
            registry = self._run_stage2(workspace, inbox_task_dir)

        # Stage 2.5 — derive reviewable document-level relations.
        if is_stage_completed(workspace, "stage2_5_document_structure"):
            record_stage(
                workspace, "stage2_5_document_structure", status=StageStatus.SKIPPED
            )
        else:
            self._run_document_structure(workspace)

        return workspace, registry

    # ------------------------------------------------------------------ #
    # Stage 0 — workspace creation / resumption
    # ------------------------------------------------------------------ #

    def _ensure_workspace(
        self,
        project_root: Path | str,
        *,
        project_id: str,
        title: str,
        subject: str | None,
        stage: str | None,
    ) -> ProjectWorkspace:
        """Create a new workspace or load an existing one."""
        project_root = Path(project_root)

        if is_stage_completed(ProjectWorkspace(project_root), "stage0_workspace"):
            workspace = load_workspace(project_root)
            record_stage(
                workspace, "stage0_workspace", status=StageStatus.SKIPPED,
            )
            return workspace

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
        return workspace

    # ------------------------------------------------------------------ #
    # Stage 1 — MinerU ingestion
    # ------------------------------------------------------------------ #

    def _run_stage1(
        self, workspace: ProjectWorkspace, inbox_task_dir: Path
    ) -> None:
        """Ingest MinerU output from inbox into normalized directory."""
        loaded = InboxLoader(inbox_task_dir.parent).load(inbox_task_dir)
        workspace.copy_mineru_raw(inbox_task_dir)

        # Persist the rewritten copies (URLs will be rewritten in Stage 2).
        raw_md = (
            workspace.mineru_raw_dir
            / loaded.task.task_dir.name
            / loaded.task.meta.products.markdown
        )
        normalized_md = workspace.normalized_dir / "full.md"
        normalized_md.write_text(loaded.markdown_text, encoding="utf-8")

        if loaded.layout_data is not None:
            layout_path = workspace.normalized_dir / "layout.json"
            layout_path.write_text(
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

    # ------------------------------------------------------------------ #
    # Stage 2 — asset localization
    # ------------------------------------------------------------------ #

    def _run_stage2(
        self, workspace: ProjectWorkspace, inbox_task_dir: Path
    ) -> AssetRegistry:
        """Download/copy all image references to local assets."""
        loaded = InboxLoader(inbox_task_dir.parent).load(inbox_task_dir)
        resolved_refs = [
            _resolve_relative_ref(url, inbox_task_dir) for url in loaded.image_references
        ]
        loaded_local = loaded.model_copy(update={"image_references": resolved_refs})
        localizer = AssetLocalizer(workspace.assets_dir, self._downloader)
        registry = localizer.localize(loaded_local)
        for original_ref, resolved_ref in zip(
            loaded.image_references, resolved_refs, strict=True
        ):
            asset = registry.get_by_url(resolved_ref)
            if asset is not None and original_ref != resolved_ref:
                registry.add_alias(original_ref, asset)

        if registry.failures:
            report_path = workspace.reports_dir / "asset_localization_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "discovered_count": len(resolved_refs),
                        "localized_count": len(registry),
                        "failures": [
                            {"url": url, "error": error}
                            for url, error in registry.failures.items()
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            record_stage(
                workspace,
                "stage2_localize",
                status=StageStatus.FAILED,
                error=(
                    f"{len(registry.failures)} image reference(s) "
                    "could not be localized"
                ),
                metadata={
                    "assets_localized": len(registry),
                    "assets_failed": len(registry.failures),
                    "report_path": str(report_path.relative_to(workspace.root)),
                },
            )
            raise AssetLocalizationError(
                "Stage 2 failed: "
                f"{len(registry.failures)} image reference(s) could not be localized"
            )

        # Rewrite references inside the normalized copies.
        normalized_md = workspace.normalized_dir / "full.md"
        original_md = normalized_md.read_text(encoding="utf-8")
        rewritten_md = localizer.rewrite_markdown(original_md, registry)
        normalized_md.write_text(rewritten_md, encoding="utf-8")

        rewritten_layout_path: Path | None = None
        layout_data_path = workspace.normalized_dir / "layout.json"
        if layout_data_path.is_file():
            layout_data = json.loads(
                layout_data_path.read_text(encoding="utf-8")
            )
            rewritten_layout = localizer.rewrite_layout(layout_data, registry)
            rewritten_layout_path = (
                workspace.normalized_dir / "layout.localized.json"
            )
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
                "rewritten_markdown": str(
                    normalized_md.relative_to(workspace.root)
                ),
                "rewritten_layout": (
                    str(rewritten_layout_path.relative_to(workspace.root))
                    if rewritten_layout_path is not None
                    else None
                ),
            },
        )

        return registry

    def _load_existing_registry(
        self, workspace: ProjectWorkspace
    ) -> AssetRegistry:
        """Load the asset registry from a previously completed Stage 2."""
        registry_path = workspace.normalized_dir / "assets_registry.json"
        if not registry_path.is_file():
            raise FileNotFoundError(
                f"Stage 2 marked completed but {registry_path} not found"
            )
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        return AssetRegistry.model_validate(data)

    # ------------------------------------------------------------------ #
    # Stage 2.5 — document structure recovery
    # ------------------------------------------------------------------ #

    def _run_document_structure(self, workspace: ProjectWorkspace) -> None:
        """Write the deterministic document-structure sidecar when layout exists."""
        layout_path = workspace.normalized_dir / "layout.localized.json"
        if not layout_path.is_file():
            record_stage(
                workspace,
                "stage2_5_document_structure",
                status=StageStatus.SKIPPED,
                metadata={"reason": "localized layout is unavailable"},
            )
            return

        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        markdown_path = workspace.normalized_dir / "full.md"
        markdown_text = (
            markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else None
        )
        structure = recover_document_structure(layout, markdown_text=markdown_text)
        output_path = workspace.normalized_dir / "document_structure.json"
        temp_path = output_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(structure.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(output_path)
        relation_counts: dict[str, int] = {}
        for relation in structure.relations:
            relation_counts[relation.kind] = relation_counts.get(relation.kind, 0) + 1
        record_stage(
            workspace,
            "stage2_5_document_structure",
            status=StageStatus.COMPLETED,
            input_fingerprint=_sha256_files(layout_path, markdown_path),
            output_fingerprint=_sha256_file(output_path),
            metadata={
                "structure_path": str(output_path.relative_to(workspace.root)),
                "blocks": len(structure.blocks),
                "relations": relation_counts,
                "alignment_status": structure.alignment.status,
                "layout_text_coverage": structure.alignment.layout_text_coverage,
                "layout_text_block_share": structure.alignment.layout_text_block_share,
                "markdown_images": structure.alignment.markdown_images,
                "layout_images": structure.alignment.layout_images,
                "matched_images": structure.alignment.matched_images,
                "synthetic_blocks": structure.alignment.synthetic_blocks,
            },
        )

    # ------------------------------------------------------------------ #
    # Stage 4b — topic matching
    # ------------------------------------------------------------------ #

    def _run_topic_match(
        self, workspace: ProjectWorkspace, outline_path: Path
    ) -> tuple[list[Any], Any]:
        """Run Stage 4b: match items to outline topics."""
        return match_project(workspace, outline_path)

    # ------------------------------------------------------------------ #
    # Stage 3 — BookView construction
    # ------------------------------------------------------------------ #

    def _run_book_view(self, workspace: ProjectWorkspace) -> Any:
        """Run Stage 3: build the BookView from normalized content."""
        return build_book_view(workspace)

    # ------------------------------------------------------------------ #
    # Stage 4c — export planning
    # ------------------------------------------------------------------ #

    def _run_export_plan(
        self,
        workspace: ProjectWorkspace,
        *,
        outline_path: Path | None = None,
        mode: str = "B",
        force_mode: bool = False,
    ) -> Any:
        """Run Stage 4c: plan exports based on BookView and mode."""
        return plan_exports(
            workspace, mode=mode, force_mode=force_mode, outline_path=outline_path
        )

    # ------------------------------------------------------------------ #
    # Stage 7 — PDF rendering
    # ------------------------------------------------------------------ #

    def _run_export(self, workspace: ProjectWorkspace) -> list[Any]:
        """Run Stage 7: render export plans to PDFs."""
        return render_exports(workspace)

    # ------------------------------------------------------------------ #
    # Stage 5 — geometry analysis
    # ------------------------------------------------------------------ #

    def _run_geometry(
        self,
        workspace: ProjectWorkspace,
        *,
        analyzer: GeometryAnalyzer | None = None,
        force: bool = False,
    ) -> list[Any]:
        """Run Stage 5: extract geometry figures from the BookView.

        Skipped on resume (when ``stage5_geometry`` is already
        completed).  Pass an injected ``analyzer`` to select a custom
        geometry backend (for example ``HybridGeometryAnalyzer`` with a
        fake VLM provider in tests); when ``None`` we fall back to the
        deterministic rules-only :class:`GeometryAnalyzer`.  Pass
        ``force=True`` to re-extract even on resume — the existing
        review_state.json audit log is then wiped and the reset is
        recorded in the manifest.
        """
        if is_stage_completed(workspace, "stage5_geometry") and not force:
            record_stage(workspace, "stage5_geometry", status=StageStatus.SKIPPED)
            from ..review import ReviewStateStore

            store = ReviewStateStore(workspace)
            return store.load_queue()
        figures, _report = analyze_geometry(workspace, analyzer=analyzer, force=force)
        return figures


# ---------------------------------------------------------------------- #
# Convenience function
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
    outline_path: Path | str | None = None,
    mode: str = "B",
    force_mode: bool = False,
    preflight: bool = True,
    review_decisions: list[ReviewDecision] | None = None,
    geometry_analyzer: GeometryAnalyzer | None = None,
    force_geometry: bool = False,
) -> PipelineResult:
    return PipelineRunner(downloader).run(
        project_root=project_root,
        inbox_task_dir=inbox_task_dir,
        project_id=project_id,
        title=title,
        subject=subject,
        stage=stage,
        outline_path=outline_path,
        mode=mode,
        force_mode=force_mode,
        preflight=preflight,
        review_decisions=review_decisions,
        geometry_analyzer=geometry_analyzer,
        force_geometry=force_geometry,
    )


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_files(*paths: Path) -> str:
    """Hash labeled file contents so every Stage 2.5 input is traceable."""
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve_relative_ref(url: str, task_dir: Path) -> str:
    """Translate a MinerU-relative path like ``images/foo.jpg`` into a
    file:// URL that the downloader can read directly. Remote URLs and
    existing file:// URLs pass through untouched.

    Paths that resolve outside ``task_dir`` are rejected and returned
    unchanged so they cannot be used to read arbitrary local files.
    """
    if url.startswith(("http://", "https://", "file://")):
        return url
    if url.startswith("images/") or url.startswith("./") or "/" not in url:
        abs_path = (task_dir / url).resolve()
        try:
            abs_path.relative_to(task_dir.resolve())
        except ValueError:
            # Path escapes task_dir — treat as unsafe and leave untouched.
            return url
        if abs_path.is_file():
            return abs_path.as_uri()
    return url


def _registry_to_dict(registry: AssetRegistry) -> dict[str, Any]:
    return {
        "count": len(registry),
        "by_url": registry.by_url,
        "failures": registry.failures,
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
