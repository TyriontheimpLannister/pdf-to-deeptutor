"""Submit a local PDF to the MinerU cloud API.

This is the Stage 0a CLI. It wraps :class:`MinerUSubmission` into a
single-shot command:

    python scripts/submit_to_mineru.py \
        --pdf path/to/source.pdf \
        --inbox inbox/<task-name> \
        [--workspace-root projects/<project-id>] \
        [--poll-interval 5] \
        [--timeout 3600] \
        [--no-wait]

Token is read from ``MINERU_API_TOKEN``. It is never echoed or
written to disk.

Outputs:
- ``inbox/<task>/.mineru_handle.json`` on every path (incl. --no-wait).
- ``inbox/<task>/{full.md, layout.json, images/, source.pdf, ...}`` on
  successful download.
- ``inbox/<task>/_raw_archive_<sha>.zip`` — preserved MinerU archive.
- ``inbox/<task>/meta.json`` — appended with ``minerU.result_expires_at``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.providers.mineru import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    FinalResult,
    InboxLayout,
    MinerUAPIError,
    MinerUAuthError,
    MinerUQuotaError,
    MinerUSubmission,
    QuotaState,
    SubmissionHandle,
    load_quota_state,
    save_handle,
)


def _read_token() -> str:
    token = os.environ.get("MINERU_API_TOKEN", "").strip()
    if not token:
        raise MinerUAuthError(
            "MINERU_API_TOKEN is not set; cannot submit. "
            "Either export it in your shell or use the manual MinerU "
            "workflow described in docs/decisions/2026-07-08-manual-"
            "mineru.md."
        )
    return token


def _write_expiry_to_meta(
    inbox_dir: Path, result: FinalResult
) -> None:
    """Append ``minerU.result_expires_at`` to the inbox meta.json."""
    meta_path = inbox_dir / "meta.json"
    meta: dict
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                meta = {}
        except ValueError:
            meta = {}
    else:
        meta = {}
    mineru_meta = meta.setdefault("minerU", {})
    if not isinstance(mineru_meta, dict):
        mineru_meta = {}
        meta["minerU"] = mineru_meta
    mineru_meta["result_expires_at"] = result.expires_at
    mineru_meta["batch_id"] = result.batch_id
    mineru_meta["task_id"] = result.task_id
    mineru_meta["page_count_actual"] = result.page_count_actual
    mineru_meta["result_sha256"] = result.result_sha256
    mineru_meta["downloaded_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_success(
    handle: SubmissionHandle,
    result: FinalResult,
    layout: InboxLayout,
    state: QuotaState,
) -> None:
    print("MinerU task complete:")
    print(f"  task_id: {result.task_id}")
    print(f"  batch_id: {result.batch_id}")
    print(f"  result sha256: {result.result_sha256 or '(unavailable)'}")
    print(f"  expires at: {result.expires_at}  ({90} days from now)")
    print(
        f"  pages used today: {state.pages_used} / {state.pages_quota}"
    )
    print(f"  inbox directory: {layout.inbox_dir}")
    if layout.markdown_path:
        print(f"  markdown: {layout.markdown_path.name}")
    if layout.layout_path:
        print(f"  layout: {layout.layout_path.name}")
    if layout.images_dir:
        print(f"  images: {layout.images_dir.name}/")
    if layout.source_pdf_path:
        print(f"  source pdf: {layout.source_pdf_path.name}")
    if layout.raw_archive_path:
        print(f"  raw archive: {layout.raw_archive_path.name}")
    print("Next step: run scripts/run_pipeline.py as usual.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pdf", required=True, help="Path to local source PDF.")
    p.add_argument(
        "--inbox",
        required=True,
        help=(
            "Output inbox directory, e.g. inbox/<task-name>. "
            "Created if missing."
        ),
    )
    p.add_argument(
        "--workspace-root",
        default=None,
        help=(
            "Project workspace root for quota state. When omitted, "
            "no quota tracking is performed."
        ),
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
        help=f"Polling interval seconds (default {DEFAULT_POLL_INTERVAL_S}).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Polling timeout seconds (default {DEFAULT_TIMEOUT_S}).",
    )
    p.add_argument(
        "--quota-pages-per-day",
        type=int,
        default=1000,
        help="MinerU daily page quota (default 1000).",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help=(
            "Submit only; write .mineru_handle.json and exit 0. "
            "Poll later via scripts/poll_mineru_task.py."
        ),
    )
    args = p.parse_args()

    pdf_path = Path(args.pdf).resolve()
    inbox_dir = Path(args.inbox).resolve()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else None

    try:
        token = _read_token()
    except MinerUAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    submission = MinerUSubmission(
        token,
        workspace_root=workspace_root,
        quota_pages_per_day=args.quota_pages_per_day,
    )

    try:
        handle = submission.submit(pdf_path)
    except MinerUQuotaError as exc:
        print(f"ERROR: quota refused: {exc}", file=sys.stderr)
        return 3
    except MinerUAuthError as exc:
        print(f"ERROR: auth failed: {exc}", file=sys.stderr)
        return 4
    except MinerUAPIError as exc:
        print(f"ERROR: submit failed: {exc}", file=sys.stderr)
        return 5

    inbox_dir.mkdir(parents=True, exist_ok=True)
    save_handle(inbox_dir, handle)
    print(
        f"OK submitted: task_id={handle.task_id} batch_id={handle.batch_id}"
    )
    print(f"   handle saved: {inbox_dir}/.mineru_handle.json")
    print(
        f"   estimated pages: {handle.page_count_estimated or 'unknown'}"
    )

    if args.no_wait:
        print("Next step: run scripts/poll_mineru_task.py --inbox <task>")
        return 0

    try:
        result = submission.wait(
            handle,
            poll_interval_s=args.poll_interval,
            timeout_s=args.timeout,
        )
    except MinerUAPIError as exc:
        print(f"ERROR: polling failed: {exc}", file=sys.stderr)
        print(
            "  The handle has been saved; rerun with --no-wait semantics "
            "via scripts/poll_mineru_task.py once the issue is resolved.",
            file=sys.stderr,
        )
        return 6

    if result.state not in ("done", "success"):
        print(
            f"ERROR: task {result.task_id} ended in state {result.state!r}.",
            file=sys.stderr,
        )
        return 7

    try:
        layout = submission.download(result, inbox_dir)
    except MinerUAPIError as exc:
        print(f"ERROR: download failed: {exc}", file=sys.stderr)
        print(
            "  The result URL is still valid for ~3 months; rerun "
            "scripts/poll_mineru_task.py --inbox <task> to retry.",
            file=sys.stderr,
        )
        return 8

    # Persist expiry into inbox meta.json so the pipeline can warn later.
    _write_expiry_to_meta(inbox_dir, result)

    state = (
        load_quota_state(workspace_root)
        if workspace_root is not None
        else QuotaState(date_utc="", pages_used=0)
    )
    _print_success(handle, result, layout, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
