"""End-to-end CLI for the MVP pipeline (Stages 0-2).

Usage:

    python scripts/run_pipeline.py \
        --inbox inbox-sample/g8-triangle-ch03 \
        --project-root projects/demo-g8-triangle \
        --project-id demo-g8-triangle \
        --title "Demo: 八年级全等三角形" \
        --downloader local \
        --mirror inbox-sample/g8-triangle-ch03/images
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make src/ importable without installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.assets import HttpxDownloader, LocalMirrorDownloader  # noqa: E402
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
    args = p.parse_args()

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())