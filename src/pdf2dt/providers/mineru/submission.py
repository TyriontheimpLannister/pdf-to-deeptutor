"""High-level MinerU submission orchestrator.

Wraps :class:`MinerUClient` into a three-step workflow that matches
the project's "submit a PDF, get an inbox directory" contract:

1. :meth:`MinerUSubmission.submit` — uploads the PDF and returns a
   :class:`SubmissionHandle` immediately (does not block).
2. :meth:`MinerUSubmission.wait` — polls until a terminal state.
3. :meth:`MinerUSubmission.download` — fetches the result archive
   and unzips it into the inbox contract shape.

For ``--no-wait`` workflows, the handle can be persisted to
``inbox/<task>/.mineru_handle.json`` via :func:`save_handle` and
resumed with :func:`load_handle` in a subsequent process.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .api_client import (
    DEFAULT_BASE_URL,
    BatchResult,
    FileSpec,
    MinerUAPIError,
    MinerUClient,
    MinerUQuotaError,
    TaskInfo,
)
from .quota import (
    QuotaDecision,
    check_quota,
    estimate_pdf_pages,
    load_quota_state,
    record_pages_used,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_TIMEOUT_S = 3600.0
HANDLE_FILENAME = ".mineru_handle.json"
RESULT_EXPIRY_DAYS = 90  # MinerU's documented 3-month URL validity.

# Default layout of the unzipped result directory. MinerU's archive
# typically contains ``full.md``, ``layout.json``, ``images/`` and a
# copy of the source PDF. Names vary between versions, so we apply
# renames when the inbox contract demands different filenames.
_INBOX_CONTRACT_FILES = {
    "full.md": ("markdown", "full.md", "MinerU_markdown.md", "MinerU_markdown_full.md"),
    "layout.json": ("layout_json", "layout.json", "MinerU_layout.json"),
}


@dataclass(frozen=True)
class SubmissionHandle:
    """Handle returned by :meth:`MinerUSubmission.submit`.

    Persisted to ``inbox/<task>/.mineru_handle.json`` so a later
    process can resume polling without re-submitting the PDF.
    """

    task_id: str  # user-facing stable id (the inbox dir basename)
    batch_id: str  # MinerU batch_id used for polling
    submitted_at: str  # ISO8601 UTC
    pdf_filename: str
    pdf_sha256: str
    page_count_estimated: int | None
    data_id: str  # identifies the file inside the batch

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubmissionHandle:
        return cls(
            task_id=str(data["task_id"]),
            batch_id=str(data["batch_id"]),
            submitted_at=str(data["submitted_at"]),
            pdf_filename=str(data["pdf_filename"]),
            pdf_sha256=str(data["pdf_sha256"]),
            page_count_estimated=(
                int(data["page_count_estimated"])
                if data.get("page_count_estimated") is not None
                else None
            ),
            data_id=str(data["data_id"]),
        )


@dataclass(frozen=True)
class FinalResult:
    """Terminal state of a submission. Carries the result archive URL."""

    task_id: str
    batch_id: str
    state: str  # "done" | "failed" | "expired"
    full_zip_url: str | None
    result_sha256: str | None  # populated after download
    page_count_actual: int | None
    expires_at: str  # ISO8601 UTC; RESULT_EXPIRY_DAYS from now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InboxLayout:
    """Result of :meth:`MinerUSubmission.download`. Paths are absolute."""

    inbox_dir: Path
    markdown_path: Path | None = None
    layout_path: Path | None = None
    images_dir: Path | None = None
    source_pdf_path: Path | None = None
    raw_archive_path: Path | None = None
    extracted_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- #
# Orchestrator
# ---------------------------------------------------------------------- #


class MinerUSubmission:
    """Orchestrates submit → poll → download for one PDF."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str | None = None,
        workspace_root: Path | str | None = None,
        quota_pages_per_day: int = 1000,
        client: httpx.Client | None = None,
        mineru_client: MinerUClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._mineru = mineru_client or MinerUClient(
            token,
            base_url=base_url or DEFAULT_BASE_URL,
            client=client,
            sleep=sleep,
        )
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._quota_pages_per_day = quota_pages_per_day
        self._sleep = sleep

    # ------------------------------------------------------------------ #
    # Submit
    # ------------------------------------------------------------------ #

    def submit(
        self,
        pdf_path: Path | str,
        *,
        task_id: str | None = None,
    ) -> SubmissionHandle:
        """Upload ``pdf_path`` to MinerU and return a handle.

        Pre-submission quota check is performed against the local
        :class:`QuotaState` when a workspace root was supplied. If the
        check refuses, raises :class:`MinerUQuotaError` without
        consuming API quota.
        """
        path = Path(pdf_path)
        if not path.is_file():
            raise MinerUAPIError(f"PDF not found: {path}")

        page_estimate = estimate_pdf_pages(path)
        if self._workspace_root is not None:
            state = load_quota_state(self._workspace_root)
            state.pages_quota = self._quota_pages_per_day
            decision, reason = check_quota(state, page_estimate)
            logger.info("MinerU quota pre-check: %s — %s", decision.value, reason)
            if decision is QuotaDecision.REFUSE_PRE:
                raise MinerUQuotaError(reason)

        pdf_sha = _sha256_file(path)
        data_id = _derive_data_id(pdf_sha, path.name)
        task_id = task_id or _derive_task_id(path.name, pdf_sha)
        filespec = FileSpec(
            name=path.name,
            data_id=data_id,
            size_bytes=path.stat().st_size,
        )
        grant = self._mineru.request_upload([filespec])
        upload_url = grant.file_urls[0]
        self._mineru.upload_pdf(upload_url, path)
        handle = SubmissionHandle(
            task_id=task_id,
            batch_id=grant.batch_id,
            submitted_at=_now_iso(),
            pdf_filename=path.name,
            pdf_sha256=pdf_sha,
            page_count_estimated=page_estimate,
            data_id=data_id,
        )
        logger.info(
            "MinerU submission accepted: task_id=%s, batch_id=%s, "
            "estimated_pages=%s",
            task_id,
            grant.batch_id,
            page_estimate,
        )
        return handle

    # ------------------------------------------------------------------ #
    # Wait
    # ------------------------------------------------------------------ #

    def wait(
        self,
        handle: SubmissionHandle,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> FinalResult:
        """Poll the batch until a terminal state or timeout."""
        start = time.monotonic()
        last_status: str = "unknown"
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout_s:
                raise MinerUAPIError(
                    f"MinerU polling timed out after {elapsed:.0f}s "
                    f"(batch_id={handle.batch_id}, last_status={last_status})"
                )
            batch = self._mineru.get_batch(handle.batch_id)
            task = _match_task(batch, handle.data_id)
            last_status = task.status
            if task.is_terminal:
                # Update local quota with the actual page count when known.
                actual_pages = _extract_page_count(task.raw, handle.page_count_estimated)
                if self._workspace_root is not None:
                    state = load_quota_state(self._workspace_root)
                    state.pages_quota = self._quota_pages_per_day
                    record_pages_used(
                        self._workspace_root,
                        state,
                        actual_pages or handle.page_count_estimated or 0,
                    )
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=RESULT_EXPIRY_DAYS)
                ).isoformat(timespec="seconds")
                return FinalResult(
                    task_id=handle.task_id,
                    batch_id=handle.batch_id,
                    state=task.status,
                    full_zip_url=task.full_zip_url,
                    result_sha256=None,
                    page_count_actual=actual_pages,
                    expires_at=expires_at,
                )
            self._sleep(max(0.1, poll_interval_s))


    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #

    def download(
        self,
        result: FinalResult,
        target_dir: Path | str,
    ) -> InboxLayout:
        """Download the result archive and unpack it into the inbox layout.

        ``target_dir`` becomes the ``inbox/<task>/`` directory. The
        archive is downloaded to a sibling ``.tmp`` path first, then
        unpacked; on success the archive is moved into
        ``<target_dir>/_raw_archive.zip`` for preservation.
        """
        if result.state not in ("done", "success"):
            raise MinerUAPIError(
                f"Cannot download: task {result.task_id} is in state "
                f"{result.state!r}, not 'done'."
            )
        if not result.full_zip_url:
            raise MinerUAPIError(
                f"Cannot download: task {result.task_id} has no full_zip_url."
            )
        inbox = Path(target_dir)
        inbox.mkdir(parents=True, exist_ok=True)
        tmp_archive = inbox / ".mineru_download.tmp.zip"
        try:
            self._mineru.download_archive(result.full_zip_url, tmp_archive)
            archive_sha = _sha256_file(tmp_archive)
            _unzip_into(tmp_archive, inbox)
            layout = _normalize_inbox_layout(inbox)
            layout.raw_archive_path = _preserve_archive(
                tmp_archive, inbox, archive_sha
            )
        finally:
            if tmp_archive.exists():
                with contextlib.suppress(OSError):
                    tmp_archive.unlink()
        return layout


# ---------------------------------------------------------------------- #
# Handle persistence
# ---------------------------------------------------------------------- #


def save_handle(inbox_dir: Path | str, handle: SubmissionHandle) -> Path:
    """Write ``.mineru_handle.json`` under ``inbox_dir``."""
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / HANDLE_FILENAME
    path.write_text(
        json.dumps(handle.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_handle(inbox_dir: Path | str) -> SubmissionHandle:
    """Read ``.mineru_handle.json`` from ``inbox_dir``."""
    path = Path(inbox_dir) / HANDLE_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"No MinerU handle at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return SubmissionHandle.from_dict(data)


def clear_handle(inbox_dir: Path | str) -> bool:
    """Remove the persisted handle. Returns ``True`` if a file was deleted."""
    path = Path(inbox_dir) / HANDLE_FILENAME
    if path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_data_id(sha: str, filename: str) -> str:
    # Stable, deterministic id so re-submits of the same PDF produce
    # the same data_id — useful when resuming a partial upload.
    short = sha[:12] if sha else "noid"
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    return f"{safe_name}-{short}"


def _derive_task_id(filename: str, sha: str) -> str:
    base = Path(filename).stem
    short = sha[:8] if sha else "noid"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    return f"{safe}-{short}"


def _match_task(batch: BatchResult, data_id: str) -> TaskInfo:
    # MinerU returns one task per file. For batches of one (always our
    # case), the first entry is the right one. If multiple files are
    # present, fall back to ``data_id`` matching.
    if len(batch.tasks) == 1:
        return batch.tasks[0]
    for task in batch.tasks:
        if str(task.raw.get("data_id") or "") == data_id:
            return task
    return batch.tasks[0]


def _extract_page_count(raw: dict[str, Any], fallback: int | None) -> int | None:
    for key in ("page_count", "page_num", "pages"):
        value = raw.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return fallback


def _unzip_into(archive_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(target_dir)


def _preserve_archive(tmp_archive: Path, inbox: Path, sha: str) -> Path:
    """Move the downloaded archive into the inbox for preservation."""
    archive_name = f"mineru_result_{sha[:12]}.zip"
    archive_path = inbox / archive_name
    if archive_path.exists():
        archive_path.unlink()
    shutil.move(str(tmp_archive), str(archive_path))
    return archive_path


def _normalize_inbox_layout(inbox: Path) -> InboxLayout:
    """Apply renames so the inbox matches the contract.

    MinerU's archive layout has shifted across versions. The inbox
    contract requires ``full.md`` and ``layout.json`` at the top
    level. We rename known MinerU aliases to those contract names
    when they don't already exist.

    The images directory and source PDF are passed through unchanged
    when found; otherwise they are left unset.
    """
    layout = InboxLayout(inbox_dir=inbox)
    # Markdown alias resolution
    for contract_name, aliases in _INBOX_CONTRACT_FILES.items():
        target = inbox / contract_name
        if target.is_file():
            continue
        for alias in aliases[1:]:
            candidate = _find_alias(inbox, alias)
            if candidate is not None and candidate.is_file():
                candidate.rename(target)
                break
    layout.markdown_path = inbox / "full.md" if (inbox / "full.md").is_file() else None
    layout.layout_path = (
        inbox / "layout.json" if (inbox / "layout.json").is_file() else None
    )
    for sub in ("images", "image", "imgs"):
        candidate = inbox / sub
        if candidate.is_dir():
            layout.images_dir = candidate
            break
    source_pdf = inbox / "source.pdf"
    if source_pdf.is_file():
        layout.source_pdf_path = source_pdf
    else:
        # MinerU often ships the original PDF under a different name.
        for pdf_candidate in inbox.glob("*.pdf"):
            if pdf_candidate.name == "source.pdf":
                continue
            pdf_candidate.rename(source_pdf)
            layout.source_pdf_path = source_pdf
            break
    layout.extracted_files = sorted(
        p.name for p in inbox.iterdir() if p.is_file()
    )
    return layout


def _find_alias(inbox: Path, alias: str) -> Path | None:
    # Direct match first
    direct = inbox / alias
    if direct.is_file():
        return direct
    # MinerU sometimes nests files one level deep.
    for nested in inbox.rglob(alias):
        if nested.is_file():
            return nested
    return None


__all__ = [
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TIMEOUT_S",
    "FinalResult",
    "HANDLE_FILENAME",
    "InboxLayout",
    "MinerUQuotaError",
    "MinerUSubmission",
    "RESULT_EXPIRY_DAYS",
    "SubmissionHandle",
    "clear_handle",
    "load_handle",
    "save_handle",
]
