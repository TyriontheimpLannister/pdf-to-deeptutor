"""End-to-end CLI for the MVP pipeline (Stages 0-2, optional Stages 3 / 4b / 4c / 7).

Usage:

    python scripts/run_pipeline.py \
        --inbox demos/inbox-sample/g8-triangle-ch03 \
        --project-root projects/demo-g8-triangle \
        --project-id demo-g8-triangle \
        --title "Demo: Chapter Export" \
        --downloader local \
        --mirror demos/inbox-sample/g8-triangle-ch03/images \
        --outline outlines/sample-outline-v1.yaml \
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
import sys
from pathlib import Path

# Make src/ importable without installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.assets import HttpxDownloader, LocalMirrorDownloader  # noqa: E402
from pdf2dt.bookview import BookViewBuildError, build_book_view  # noqa: E402
from pdf2dt.export import (  # noqa: E402
    PlanError,
    known_bridge_providers,
    plan_exports,
    render_exports,
    resolve_bridge_provider,
)
from pdf2dt.outlining import OutlineLoadError, match_project  # noqa: E402
from pdf2dt.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run the MVP pipeline (Stages 0-2).")
    p.add_argument("--inbox", required=True, help="Path to the MinerU task directory.")
    p.add_argument("--project-root", required=True, help="Where to create the project.")
    p.add_argument("--project-id", required=True, help="Stable project identifier.")
    p.add_argument("--title", required=True, help="Human-readable project title.")
    p.add_argument("--subject", default="general")
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
        default="B",
        help=(
            "Export reorganization mode: A=source order, B=topic cluster "
            "(default), C=topic cluster with generative transitions "
            "(writes one Bridge per adjacent plan via --bridge-provider). "
            "Used with --export."
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
            "bridges entirely (Mode C degrades to Mode B). Custom "
            "providers can be registered via the Python API "
            "(pdf2dt.export.register_bridge_provider)."
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

    if args.export:
        if not args.book_view:
            print("Error: --export requires --book-view", file=sys.stderr)
            return 5
        try:
            collection = plan_exports(
                result.workspace,
                mode=args.mode,
                outline_path=args.outline,
                bridge_provider=resolve_bridge_provider(args.bridge_provider),
            )
        except PlanError as exc:
            print(f"Error: export planning failed: {exc}", file=sys.stderr)
            return 6

        bridge_count = sum(len(p.bridges) for p in collection.plans)
        print(
            f"[stage4c] mode={args.mode}, plans={len(collection.plans)}, "
            f"bridges={bridge_count} ({args.bridge_provider or 'mock'}), "
            f"export_plans/plans.json"
        )

        try:
            rendered = render_exports(result.workspace)
        except FileNotFoundError as exc:
            print(f"Error: rendering failed: {exc}", file=sys.stderr)
            return 7

        print(
            f"[stage7] rendered={len(rendered)} PDFs, "
            f"exports/deeptutor/"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())