"""End-to-end CLI for the MVP pipeline (Stages 0-2, optional Stages 3 / 4b / 4c / 7).

Usage:

    python scripts/run_pipeline.py \
        --inbox inbox-sample/g8-triangle-ch03 \
        --project-root projects/demo-g8-triangle \
        --project-id demo-g8-triangle \
        --title "Demo: 八年级全等三角形" \
        --downloader local \
        --mirror inbox-sample/g8-triangle-ch03/images \
        --outline outlines/elementary-math-v1.yaml \
        --book-view \
        --mode B \
        --export

Pass ``--outline <path-to-yaml>`` to additionally run Stage 4b
(outline-driven topic assignment) against the workspace produced by
Stages 0-2.

Pass ``--book-view`` to additionally run Stage 3 (BookView builder)
which links items, source blocks, and localized assets and writes
``book_view/book_view.json``. When combined with ``--outline``, the
BookView inherits topic_ids from Stage 4b.

Pass ``--mode {A,B,C}`` to control the export reorganization mode.
The default is ``B`` (topic-clustered, wording preserved). Requires
``--book-view``.

Pass ``--export`` to run Stage 4c (export planning) and Stage 7
(PDF rendering). Requires ``--book-view``.

Pass ``--bridge-provider {mock,noop}`` (with ``--mode C``) to choose
which :class:`BridgeProvider` writes the transition paragraph
between adjacent plans. Defaults to ``mock``, which ships a
deterministic placeholder. Custom providers can be registered via
the public Python API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make src/ importable without installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.assets import HttpxDownloader, LocalMirrorDownloader  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from pdf2dt.bookview import BookViewBuildError, build_book_view  # noqa: E402
from pdf2dt.export import (  # noqa: E402
    PlanError,
    known_bridge_providers,
    plan_exports,
    render_exports,
    resolve_bridge_provider,
)
from pdf2dt.geometry import analyze_geometry, build_geometry_analyzer  # noqa: E402
from pdf2dt.outlining import OutlineLoadError, match_project  # noqa: E402
from pdf2dt.pipeline import run_pipeline  # noqa: E402
from pdf2dt.project import (  # noqa: E402
    StageStatus,
    is_stage_completed,
    record_stage,
)
from pdf2dt.review import (  # noqa: E402
    PromotionError,
    ReviewDecision,
    apply_review,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Run the MVP pipeline (Stages 0-2).")
    p.add_argument("--inbox", required=True, help="Path to the MinerU task directory.")
    p.add_argument("--project-root", required=True, help="Where to create the project.")
    p.add_argument("--project-id", required=True, help="Stable project identifier.")
    p.add_argument("--title", required=True, help="Human-readable project title.")
    p.add_argument("--subject", default="math")
    p.add_argument("--stage", default="middle-G8")
    p.add_argument(
        "--downloader",
        choices=["local", "http"],
        default="local",
        help="Use LocalMirrorDownloader (fixture-friendly) or HttpxDownloader.",
    )
    p.add_argument(
        "--mirror",
        default=None,
        help="Required for --downloader local; mirror directory for fixture images.",
    )
    p.add_argument(
        "--outline",
        type=Path,
        default=None,
        help=(
            "Optional path to an outline YAML. When supplied, runs Stage 4b "
            "(outline-driven topic assignment) after Stages 0-2 complete."
        ),
    )
    p.add_argument(
        "--book-view",
        action="store_true",
        help=(
            "Run Stage 3 (BookView builder) after Stages 0-2. Persists "
            "book_view/book_view.json and records the stage in the "
            "project manifest. Use together with --outline to inherit "
            "Stage 4b topic_ids."
        ),
    )
    p.add_argument(
        "--mode",
        choices=["A", "B", "C"],
        default=None,
        help=(
            "Export reorganization mode: A=source order, B=topic cluster "
            "(default), C=topic cluster with generative transitions "
            "(writes one Bridge per adjacent plan via --bridge-provider). "
            "Used with --export. When explicitly supplied, the mode is "
            "forced for every topic; otherwise the outline's per-topic "
            "strategy is respected."
        ),
    )
    p.add_argument(
        "--bridge-provider",
        choices=known_bridge_providers(),
        default=None,
        help=(
            "BridgeProvider for Mode C. Default 'mock' ships a "
            "deterministic placeholder so the test suite and the default "
            "CLI UX never require external LLM access. 'noop' skips "
            "bridges entirely (Mode C degrades to Mode B)."
        ),
    )
    p.add_argument(
        "--export",
        action="store_true",
        help=(
            "Run Stage 4c (export planning) and Stage 7 (PDF rendering). "
            "Requires --book-view. Writes export_plan/plans.json and "
            "exports/deeptutor/*.pdf."
        ),
    )
    p.add_argument(
        "--geometry",
        action="store_true",
        help=(
            "Run Stage 5 (geometry analysis) after Stage 3. Persists "
            "review/geometry_figures.json and reports/"
            "geometry_extraction_report.json. Skipped when the stage "
            "is already completed in the project manifest; pass "
            "--force-geometry to re-extract."
        ),
    )
    p.add_argument(
        "--geometry-provider",
        choices=["rules", "hybrid-minimax-m3", "hybrid-sensenova"],
        default="rules",
        help=(
            "Geometry provider. rules is offline; hybrid providers add "
            "review-only VLM visual inferences and fall back to rules on errors."
        ),
    )
    p.add_argument(
        "--force-geometry",
        action="store_true",
        help=(
            "Re-run Stage 5 even when stage5_geometry is already "
            "completed. Clears review/review_state.json because the "
            "new queue overwrites every relation's review_state; the "
            "reset is recorded in the project manifest."
        ),
    )
    p.add_argument(
        "--review",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSON file of review decisions to apply "
            "after Stage 5. See scripts/review.py for the schema. When "
            "omitted, the existing review/geometry_figures.json is left "
            "as-is (the renderer still honours whatever review_state "
            "is already recorded for each relation)."
        ),
    )
    p.add_argument(
        "--classify-figure-roles",
        action="store_true",
        help=(
            "Run Stage 5 figure role classification after Stage 3 (and "
            "after --geometry, if set). Persists review/figure_roles.json "
            "and records the stage in the project manifest. Requires "
            "--book-view. Skipped when stage5_figure_roles is already "
            "completed; pass --force-figure-roles to re-classify."
        ),
    )
    p.add_argument(
        "--figure-role-provider",
        choices=["mock", "minimax-m3", "sensenova"],
        default="mock",
        help="Provider for figure role classification. Default 'mock' is offline.",
    )
    p.add_argument(
        "--max-figure-roles",
        type=int,
        default=None,
        help="Cap the number of figures classified (smoke / cost control).",
    )
    p.add_argument(
        "--force-figure-roles",
        action="store_true",
        help="Re-run figure role classification even when the stage is completed.",
    )
    args = p.parse_args()

    if args.outline is not None and not args.outline.is_file():
        print(
            f"Error: --outline file not found: {args.outline}",
            file=sys.stderr,
        )
        return 2

    if args.downloader == "local":
        if args.mirror is None:
            print("--mirror is required when --downloader=local", file=sys.stderr)
            return 2
        downloader = LocalMirrorDownloader(args.mirror)
    else:
        downloader = HttpxDownloader()

    result = run_pipeline(
        project_root=args.project_root,
        inbox_task_dir=args.inbox,
        project_id=args.project_id,
        title=args.title,
        downloader=downloader,
        subject=args.subject,
        stage=args.stage,
    )

    print(f"OK project created at: {result.workspace.root}")
    print(f"   localized assets:  {len(result.asset_registry)}")
    for asset in result.asset_registry.by_id.values():
        print(f"     - {asset.asset_id}  {asset.byte_size}B  {asset.width}x{asset.height}")

    if args.outline is not None:
        try:
            assignments, report = match_project(
                result.workspace,
                args.outline,
                markdown_path=result.workspace.normalized_dir / "full.md",
            )
        except OutlineLoadError as exc:
            print(
                f"Error: failed to load outline {args.outline}: {exc}",
                file=sys.stderr,
            )
            return 3

        report_rel = (
            result.workspace.reports_dir / "topic_assignment_report.json"
        ).relative_to(result.workspace.root)
        # ``report_rel`` already begins with ``reports/`` (it is the path
        # relative to the workspace root); the ``reports/`` literal in the
        # summary format is meant as a category label, so use just the
        # basename to avoid doubling the prefix.
        report_name = Path(report_rel).name
        print(
            f"[stage4b] outline={report.outline_id} v{report.outline_version}, "
            f"items={report.total_items}, unclassified={len(report.unclassified_items)}, "
            f"reports/{report_name}"
        )

    if args.book_view:
        try:
            book = build_book_view(result.workspace)
        except BookViewBuildError as exc:
            print(f"Error: BookView build failed: {exc}", file=sys.stderr)
            return 4

        chapters = len(book.chapters)
        sections = sum(len(c.sections) for c in book.chapters)
        items = sum(
            len(s.items) for c in book.chapters for s in c.sections
        )
        assets = sum(
            len(it.asset_refs)
            for c in book.chapters
            for s in c.sections
            for it in s.items
        )
        print(
            f"[stage3] book={book.book_id}, chapters={chapters}, "
            f"sections={sections}, items={items}, assets={assets}, "
            f"book_view/book_view.json"
        )

    if args.geometry:
        if not args.book_view:
            print("Error: --geometry requires --book-view", file=sys.stderr)
            return 8
        # Mirror scripts/rerun_late_stages.py: skip Stage 5 when it is
        # already completed unless the user explicitly forces a re-run.
        # Without this guard, analyze_geometry() would overwrite
        # review/geometry_figures.json and discard every confirmed/
        # corrected/rejected review_state while review_state.json
        # stayed stale.
        if args.force_geometry or not is_stage_completed(
            result.workspace, "stage5_geometry"
        ):
            figures, report = analyze_geometry(
                result.workspace,
                analyzer=build_geometry_analyzer(args.geometry_provider),
                force=args.force_geometry,
            )
            print(
                f"[stage5] figures={report.figures_total}, "
                f"with_relations={report.figures_with_relations}, "
                f"relations={report.relations_total}, "
                f"evidence={report.evidence_counts}, "
                f"review/geometry_figures.json"
            )
        else:
            record_stage(
                result.workspace, "stage5_geometry", status=StageStatus.SKIPPED
            )
            print(
                "[stage5] skipped (already completed; pass "
                "--force-geometry to re-extract)"
            )

    if args.classify_figure_roles:
        if not args.book_view:
            print(
                "Error: --classify-figure-roles requires --book-view",
                file=sys.stderr,
            )
            return 13
        # Mirror the geometry guard: skip when already completed unless
        # --force-figure-roles is passed. The cache key is keyed by
        # (asset_sha256, model_id, prompt_hash) so re-running without
        # --force is essentially free.
        if args.force_figure_roles or not is_stage_completed(
            result.workspace, "stage5_figure_roles"
        ):
            import importlib.util

            from pdf2dt.review.figure_roles import (
                FigureRole,
                classify_figure_roles,
            )
            _spec = importlib.util.spec_from_file_location(
                "classify_image_roles",
                ROOT / "scripts" / "classify_image_roles.py",
            )
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            provider = _mod._build_provider(args.figure_role_provider)
            roles = classify_figure_roles(
                result.workspace,
                provider=provider,
                max_images=args.max_figure_roles,
            )
            dist: dict[str, int] = {
                FigureRole.DECOR.value: 0,
                FigureRole.AMBIGUOUS.value: 0,
                FigureRole.CONTENT.value: 0,
            }
            for r in roles:
                dist[r.role.value] = dist.get(r.role.value, 0) + 1
            print(
                f"[stage5-figroles] classified={len(roles)}, "
                f"decor={dist[FigureRole.DECOR.value]}, "
                f"ambiguous={dist[FigureRole.AMBIGUOUS.value]}, "
                f"content={dist[FigureRole.CONTENT.value]}, "
                f"review/figure_roles.json"
            )
        else:
            record_stage(
                result.workspace,
                "stage5_figure_roles",
                status=StageStatus.SKIPPED,
            )
            print(
                "[stage5-figroles] skipped (already completed; pass "
                "--force-figure-roles to re-classify)"
            )

    if args.review is not None:
        if not args.review.is_file():
            print(f"Error: --review file not found: {args.review}", file=sys.stderr)
            return 9
        decisions_payload = json.loads(args.review.read_text(encoding="utf-8"))
        if not isinstance(decisions_payload, list):
            print(
                "Error: --review file must be a JSON array of decisions",
                file=sys.stderr,
            )
            return 10
        decisions = [ReviewDecision.from_dict(d) for d in decisions_payload]
        try:
            applied, counts = apply_review(result.workspace, decisions)
        except PromotionError as exc:
            print(f"Error: review promotion failed: {exc}", file=sys.stderr)
            return 11
        except FileNotFoundError as exc:
            print(
                f"Error: stage 5 output missing — run --geometry first: {exc}",
                file=sys.stderr,
            )
            return 12
        print(
            f"[stage6] applied={len(applied)} decision(s), "
            f"state_counts={counts}, review/review_state.json"
        )

    # Geometry analysis and review must happen before Stage 4c/7 so the
    # renderer can include reviewed relations and block unsafe inferences.
    if args.export:
        if not args.book_view:
            print("Error: --export requires --book-view", file=sys.stderr)
            return 5
        plan_mode = args.mode if args.mode is not None else "B"
        force_mode = args.mode is not None
        try:
            collection = plan_exports(
                result.workspace,
                mode=plan_mode,
                force_mode=force_mode,
                outline_path=args.outline,
                bridge_provider=resolve_bridge_provider(args.bridge_provider),
            )
        except PlanError as exc:
            print(f"Error: export planning failed: {exc}", file=sys.stderr)
            return 6

        bridge_count = sum(len(p.bridges) for p in collection.plans)
        print(
            f"[stage4c] mode={plan_mode}, plans={len(collection.plans)}, "
            f"bridges={bridge_count} ({args.bridge_provider or 'mock'}), "
            f"export_plans/plans.json"
        )

        try:
            rendered = render_exports(result.workspace)
        except FileNotFoundError as exc:
            print(f"Error: rendering failed: {exc}", file=sys.stderr)
            return 7

        blocked = sum(1 for r in rendered if r.validation_status == "blocked")
        warning = sum(1 for r in rendered if r.validation_status == "warning")
        ready = sum(1 for r in rendered if r.validation_status == "ready")
        print(
            f"[stage7] rendered={len(rendered)} PDFs "
            f"(ready={ready}, warning={warning}, blocked={blocked}), "
            f"exports/deeptutor/"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
