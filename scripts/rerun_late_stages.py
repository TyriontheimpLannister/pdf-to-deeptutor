"""Re-run Stages 3 / 4b / 4c / 7 against an existing workspace.

Skips Stages 0-2 (the workspace already exists, with localized
assets). Used to verify Stage 4c/7 against a freshly-bumped outline
without re-downloading assets.

Run as:
    python scripts/rerun_late_stages.py --project-root projects/<book-id> --outline outlines/sample-outline-v1.yaml --mode B
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python scripts/<script>.py`` without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf2dt.bookview import BookViewBuildError, build_book_view  # noqa: E402
from pdf2dt.export import (  # noqa: E402
    PlanError,
    plan_exports,
    render_exports,
    resolve_bridge_provider,
)
from pdf2dt.outlining import OutlineLoadError, match_project  # noqa: E402
from pdf2dt.project.workspace import ProjectWorkspace  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, required=True)
    ap.add_argument("--outline", type=Path, required=True)
    ap.add_argument(
        "--mode",
        choices=["A", "B", "C"],
        default="B",
        help="Export reorganization mode (default B).",
    )
    ap.add_argument(
        "--bridge-provider",
        choices=["mock", "noop"],
        default=None,
        help=(
            "BridgeProvider for Mode C. Default 'mock' writes "
            "deterministic placeholders; 'noop' skips bridges."
        ),
    )
    args = ap.parse_args()

    ws = ProjectWorkspace(args.project_root)
    if not ws.exists():
        print(f"Error: workspace {ws.root} does not exist", file=sys.stderr)
        return 2

    if not args.outline.is_file():
        print(f"Error: --outline file not found: {args.outline}", file=sys.stderr)
        return 3

    # Stage 4b
    try:
        assignments, report = match_project(
            ws,
            args.outline,
            markdown_path=ws.normalized_dir / "full.md",
        )
    except OutlineLoadError as exc:
        print(f"Error: failed to load outline {args.outline}: {exc}", file=sys.stderr)
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

    # Stage 4c
    try:
        collection = plan_exports(
            ws,
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

    # Stage 7
    try:
        rendered = render_exports(ws)
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
