"""Re-run Stages 3 / 4b / 4c / 7 against an existing workspace.

Skips Stages 0-2 (the workspace already exists, with localized
assets). Used to verify Stage 4c/7 against a freshly-bumped
outline without re-downloading assets.

Run as::

    python scripts/rerun_late_stages.py \\
        --project-root projects/demo-g8-triangle \\
        --outline outlines/elementary-math-v1.yaml \\
        --mode B

The script also re-runs Stage 5 (geometry analysis) and Stage 6
(review) when their respective flags are supplied.  By default,
Stage 5 is skipped when the project manifest already records
``stage5_geometry`` as ``completed``; pass ``--force-geometry``
to re-extract anyway (useful after editing the geometry rules
in :mod:`pdf2dt.geometry.analyzer`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as ``python scripts/<script>.py`` without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
from pdf2dt.project import (  # noqa: E402
    ProjectWorkspace,
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, required=True)
    ap.add_argument("--outline", type=Path, default=None)
    ap.add_argument(
        "--mode",
        choices=["A", "B", "C"],
        default=None,
        help=(
            "Export reorganization mode (default B). "
            "When explicitly supplied, the mode is forced for every topic; "
            "otherwise the outline's per-topic strategy is respected."
        ),
    )
    ap.add_argument(
        "--bridge-provider",
        choices=known_bridge_providers(),
        default=None,
        help=(
            "BridgeProvider for Mode C. Default 'mock' writes "
            "deterministic placeholders; 'noop' skips bridges."
        ),
    )
    ap.add_argument(
        "--geometry",
        action="store_true",
        help=(
            "Run Stage 5 (geometry analysis) after Stage 3. "
            "Skipped automatically when stage5_geometry is "
            "already completed; pass --force-geometry to "
            "re-extract anyway."
        ),
    )
    ap.add_argument(
        "--geometry-provider",
        choices=["rules", "hybrid-minimax-m3", "hybrid-sensenova"],
        default="rules",
        help="Geometry provider; hybrid modes preserve rule results on VLM failure.",
    )
    ap.add_argument(
        "--force-geometry",
        action="store_true",
        help=(
            "Re-run Stage 5 even when stage5_geometry is "
            "already completed in the project manifest."
        ),
    )
    ap.add_argument(
        "--review",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSON file of review decisions to "
            "apply after Stage 5. See scripts/review.py for the "
            "schema. When omitted, the on-disk queue is left "
            "as-is; Stage 6 still records an SKIPPED entry so "
            "the manifest reflects that the stage ran."
        ),
    )
    args = ap.parse_args()

    ws = ProjectWorkspace(args.project_root)
    if not ws.exists():
        print(f"Error: workspace {ws.root} does not exist", file=sys.stderr)
        return 2

    if args.outline is not None and not args.outline.is_file():
        print(f"Error: --outline file not found: {args.outline}", file=sys.stderr)
        return 3

    # Validate --review up-front so we can return a precise
    # error code without first running Stages 3-4b.  This is
    # also a small correctness improvement: if the user got the
    # file path wrong they want a fast, specific failure.
    # Promotion rules (which require the Stage 5 queue) are
    # deferred to apply_review() below.
    review_decisions: list | None = None
    if args.review is not None:
        if not args.review.is_file():
            print(
                f"Error: --review file not found: {args.review}",
                file=sys.stderr,
            )
            return 8
        try:
            raw = args.review.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"Error: could not read --review file: {exc}",
                file=sys.stderr,
            )
            return 9
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"Error: --review file is not valid JSON: {exc}",
                file=sys.stderr,
            )
            return 10
        if not isinstance(parsed, list):
            print(
                "Error: --review file must be a JSON array of decisions",
                file=sys.stderr,
            )
            return 11
        try:
            review_decisions = [ReviewDecision.from_dict(d) for d in parsed]
        except (KeyError, TypeError, ValueError) as exc:
            print(
                f"Error: malformed decision in --review file: {exc}",
                file=sys.stderr,
            )
            return 12

    # Stage 4b (skipped when no outline is supplied)
    if args.outline is not None:
        try:
            assignments, report = match_project(
                ws,
                args.outline,
                markdown_path=ws.normalized_dir / "full.md",
            )
        except OutlineLoadError as exc:
            print(
                f"Error: failed to load outline {args.outline}: {exc}",
                file=sys.stderr,
            )
            return 4

        report_name = "topic_assignment_report.json"
        print(
            f"[stage4b] outline={report.outline_id} v{report.outline_version}, "
            f"items={report.total_items}, unclassified={len(report.unclassified_items)}, "
            f"reports/{report_name}"
        )

    # Stage 3
    try:
        book = build_book_view(ws)
    except BookViewBuildError as exc:
        print(f"Error: BookView build failed: {exc}", file=sys.stderr)
        return 5

    chapters = len(book.chapters)
    sections = sum(len(c.sections) for c in book.chapters)
    items = sum(len(s.items) for c in book.chapters for s in c.sections)
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

    # Stage 5 — geometry analysis. Skipped on resume unless the
    # user passes --force-geometry. The CLI mirrors
    # pdf2dt.pipeline.PipelineRunner._run_geometry().  When forced,
    # analyze_geometry(force=True) also clears review_state.json so
    # the audit log does not contradict the freshly unreviewed queue.
    if args.geometry:
        if args.force_geometry or not is_stage_completed(ws, "stage5_geometry"):
            figures, report = analyze_geometry(
                ws,
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
            record_stage(ws, "stage5_geometry", status=StageStatus.SKIPPED)
            print(
                "[stage5] skipped (already completed; pass "
                "--force-geometry to re-extract)"
            )

    # Stage 6 — review apply. The --review decisions have
    # already been validated up-front (see the early block
    # above), so this block just calls :func:`apply_review`
    # which enforces promotion rules.  Without --review
    # we keep the on-disk queue intact and record SKIPPED
    # so the manifest reflects that Stage 6 was reached.
    if review_decisions is not None:
        try:
            applied, counts = apply_review(ws, review_decisions)
        except PromotionError as exc:
            print(
                f"Error: review promotion failed: {exc}",
                file=sys.stderr,
            )
            return 13
        except FileNotFoundError as exc:
            print(
                f"Error: stage 5 output missing — run --geometry first: {exc}",
                file=sys.stderr,
            )
            return 14
        print(
            f"[stage6] applied={len(applied)} decision(s), "
            f"state_counts={counts}, review/review_state.json"
        )
    elif args.geometry or is_stage_completed(ws, "stage6_review"):
        if not is_stage_completed(ws, "stage6_review"):
            apply_review(ws, [])
        else:
            record_stage(ws, "stage6_review", status=StageStatus.SKIPPED)

    # Stage 4c
    plan_mode = args.mode if args.mode is not None else "B"
    force_mode = args.mode is not None
    try:
        collection = plan_exports(
            ws,
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

    # Stage 7
    try:
        rendered = render_exports(ws)
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
