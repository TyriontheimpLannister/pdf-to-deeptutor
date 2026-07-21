"""Poll a previously-submitted MinerU task and download the result.

Resume helper for ``scripts/submit_to_mineru.py --no-wait`` and for
crashed/interrupted runs whose handle has been persisted to
``inbox/<task>/.mineru_handle.json``.

Usage:

    python scripts/poll_mineru_task.py \
        --inbox inbox/<task-name> \
        [--workspace-root projects/<project-id>] \
        [--poll-interval 5] \
        [--timeout 3600]

Token is read from ``MINERU_API_TOKEN``. Exits non-zero on auth,
polling timeout, or terminal-failure states. On success the inbox
directory is populated exactly as ``submit_to_mineru.py`` would have
done (minus re-submitting the PDF).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.providers.mineru import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    MinerUAPIError,
    MinerUAuthError,
    MinerUSubmission,
    QuotaState,
    clear_handle,
    load_handle,
    load_quota_state,
)

# ``scripts/`` is not a package; load submit_to_mineru by file path so we
# can reuse its presentation + meta-writing helpers without duplicating
# them.
_spec = importlib.util.spec_from_file_location(
    "submit_to_mineru",
    ROOT / "scripts" / "submit_to_mineru.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_print_success = _mod._print_success
_write_expiry_to_meta = _mod._write_expiry_to_meta


def _read_token() -> str:
    token = os.environ.get("MINERU_API_TOKEN", "").strip()
    if not token:
        raise MinerUAuthError(
            "MINERU_API_TOKEN is not set; cannot poll."
        )
    return token


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--inbox",
        required=True,
        help="Inbox directory carrying .mineru_handle.json.",
    )
    p.add_argument(
        "--workspace-root",
        default=None,
        help="Project workspace root for quota state.",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
    )
    p.add_argument(
        "--quota-pages-per-day",
        type=int,
        default=1000,
    )
    args = p.parse_args()

    inbox_dir = Path(args.inbox).resolve()
    workspace_root = (
        Path(args.workspace_root).resolve() if args.workspace_root else None
    )

    try:
        token = _read_token()
    except MinerUAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        handle = load_handle(inbox_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    print(
        f"Resuming task_id={handle.task_id} batch_id={handle.batch_id} "
        f"(submitted {handle.submitted_at})"
    )

    submission = MinerUSubmission(
        token,
        workspace_root=workspace_root,
        quota_pages_per_day=args.quota_pages_per_day,
    )

    try:
        result = submission.wait(
            handle,
            poll_interval_s=args.poll_interval,
            timeout_s=args.timeout,
        )
    except MinerUAPIError as exc:
        print(f"ERROR: polling failed: {exc}", file=sys.stderr)
        return 4

    if result.state not in ("done", "success"):
        print(
            f"ERROR: task {result.task_id} ended in state {result.state!r}.",
            file=sys.stderr,
        )
        return 5

    try:
        layout = submission.download(result, inbox_dir)
    except MinerUAPIError as exc:
        print(f"ERROR: download failed: {exc}", file=sys.stderr)
        return 6

    _write_expiry_to_meta(inbox_dir, result)
    clear_handle(inbox_dir)  # download succeeded; handle no longer needed

    state = (
        load_quota_state(workspace_root)
        if workspace_root is not None
        else QuotaState(date_utc="", pages_used=0)
    )
    _print_success(handle, result, layout, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
