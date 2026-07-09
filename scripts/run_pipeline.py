"""End-to-end CLI for the MVP pipeline (Stages 0-2, optional Stage 4b).

Usage:

    python scripts/run_pipeline.py \
        --inbox demos/inbox-sample/g8-triangle-ch03 \
        --project-root projects/demo-g8-triangle \
        --project-id demo-g8-triangle \
        --title "Demo: 八年级全等三角形" \
        --downloader local \
        --mirror demos/inbox-sample/g8-triangle-ch03/images

Pass ``--outline <path-to-yaml>`` to additionally run Stage 4b
(outline-driven topic assignment) against the workspace produced by
Stages 0-2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make src/ importable without installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.assets import HttpxDownloader, LocalMirrorDownloader  # noqa: E402
from pdf2dt.outlining import OutlineLoadError, match_project  # noqa: E402
from pdf2dt.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run the MVP pipeline (Stages 0-2, optional Stage 4b).")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
