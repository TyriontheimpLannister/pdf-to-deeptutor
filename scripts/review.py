"""Stage 6 review CLI.

This script is the user-facing entry point for managing geometry
review decisions.  It supports three subcommands:

* ``list`` — print the current review queue to stdout (or a
  reviewer-friendly Markdown file when ``--format md`` is given).
* ``apply`` — apply a list of decisions from a JSON file to the
  on-disk queue.  Promotion rules are enforced
  (``visual_inference`` / ``unknown`` cannot be confirmed).
* ``report`` — print per-state counts and the list of figures
  that still block export.

Usage:

    # 1. Inspect the queue.
    python scripts/review.py list \\
        --project-root projects/demo-g8-triangle

    # 2. Apply a JSON file of decisions.
    python scripts/review.py apply \\
        --project-root projects/demo-g8-triangle \\
        --decisions review_decisions.json

    # 3. See which figures still block the export.
    python scripts/review.py report \\
        --project-root projects/demo-g8-triangle
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as ``python scripts/<script>.py`` without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf2dt.geometry import NON_PROMOTABLE_EVIDENCE  # noqa: E402
from pdf2dt.project.workspace import ProjectWorkspace  # noqa: E402
from pdf2dt.review import (  # noqa: E402
    PromotionError,
    ReviewDecision,
    ReviewStateStore,
    apply_review,
)


def _load_workspace(project_root: Path) -> ProjectWorkspace:
    ws = ProjectWorkspace(project_root)
    if not ws.exists():
        print(f"Error: workspace {project_root} does not exist", file=sys.stderr)
        raise SystemExit(2)
    return ws


def _cmd_list(args: argparse.Namespace) -> int:
    ws = _load_workspace(args.project_root)
    store = ReviewStateStore(ws)
    figures = store.load_queue()
    if not figures:
        print("no geometry figures found; run analyze_geometry first")
        return 0
    if args.format == "md":
        out = ws.review_dir / "review_queue.md"
        out.write_text(_render_markdown(figures), encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(json.dumps(
            [f.to_dict() for f in figures], ensure_ascii=False, indent=2
        ))
    return 0


def _render_markdown(figures: list) -> str:
    out: list[str] = ["# Geometry review queue", ""]
    for fig in figures:
        out.append(f"## Figure {fig.figure_id} (asset {fig.asset_id})")
        out.append(f"- item: `{fig.associated_item_id}`")
        out.append(f"- points: {', '.join(fig.points) or '—'}")
        out.append(f"- segments: {', '.join(fig.segments) or '—'}")
        if not fig.relations:
            out.append("- relations: (none)")
            out.append("")
            continue
        out.append("- relations:")
        for r in fig.relations:
            tag = f"**{r.evidence.value}**"
            out.append(
                f"  - `{r.key}` — {r.type.value} over "
                f"{', '.join(r.entities)} (state: "
                f"{r.review_state.value}) {tag}"
            )
        out.append("")
    return "\n".join(out)


def _cmd_apply(args: argparse.Namespace) -> int:
    ws = _load_workspace(args.project_root)
    decisions_path = Path(args.decisions)
    if not decisions_path.is_file():
        print(f"Error: decisions file not found: {decisions_path}", file=sys.stderr)
        return 3
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        print(
            "Error: decisions file must be a JSON array",
            file=sys.stderr,
        )
        return 4
    decisions = [ReviewDecision.from_dict(d) for d in payload]
    try:
        applied, counts = apply_review(ws, decisions)
    except PromotionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 5
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 6
    print(f"applied {len(applied)} decision(s); state counts: {counts}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    ws = _load_workspace(args.project_root)
    store = ReviewStateStore(ws)
    figures = store.load_queue()

    counts: dict[str, int] = {}
    blocked: list[dict[str, str]] = []
    for fig in figures:
        for rel in fig.relations:
            counts[rel.review_state.value] = (
                counts.get(rel.review_state.value, 0) + 1
            )
            if (
                rel.evidence in NON_PROMOTABLE_EVIDENCE
                and rel.review_state.value not in {"confirmed", "corrected"}
            ):
                blocked.append(
                    {
                        "figure_id": fig.figure_id,
                        "asset_id": fig.asset_id,
                        "relation_key": rel.key,
                        "evidence": rel.evidence.value,
                    }
                )

    print(f"project: {args.project_root}")
    print(f"figures: {len(figures)}")
    print(f"relations: {sum(counts.values())}")
    for k in sorted(counts):
        print(f"  - {k}: {counts[k]}")
    if blocked:
        print(f"blocked figures: {len(blocked)}")
        for entry in blocked:
            print(
                f"  - {entry['figure_id']} ({entry['asset_id']}) "
                f"{entry['relation_key']} [{entry['evidence']}]"
            )
    else:
        print("blocked figures: 0 (export eligible)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", type=Path, required=True)
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Print the current review queue.")
    p_list.add_argument(
        "--format", choices=["json", "md"], default="json"
    )
    p_list.set_defaults(func=_cmd_list)

    p_apply = sub.add_parser(
        "apply", help="Apply a JSON file of review decisions."
    )
    p_apply.add_argument(
        "--decisions", type=Path, required=True,
        help="JSON array of decision objects (see schema).",
    )
    p_apply.set_defaults(func=_cmd_apply)

    p_report = sub.add_parser(
        "report", help="Print per-state counts and blocked figures."
    )
    p_report.set_defaults(func=_cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
