"""MinerU submission adapter — Stage 0a provider module.

This package wraps the MinerU cloud API into a three-step workflow that
matches the project's "submit a PDF, get an inbox directory" contract:

1. :class:`MinerUSubmission.submit` uploads the PDF and returns a
   :class:`SubmissionHandle` immediately (does not block).
2. :meth:`MinerUSubmission.wait` polls the batch until a terminal state.
3. :meth:`MinerUSubmission.download` fetches the result archive and
   unzips it into the inbox contract shape.

The adapter is a Stage 0a provider — it runs *before* the existing
``init_inbox_meta.py`` + Stage 1 ingest. New pipeline entry point:

    source.pdf
        │
        ▼
    [ Stage 0a: MinerU submission adapter ] ──→ inbox/<task>/
        │                                           {full.md, layout.json,
        ▼                                            images/, source.pdf,
    [ Stage 0b: workspace setup + preflight ]         meta.json, .mineru_handle.json}
        ▼
    [ Stage 1..7 ]                              (unchanged)

The manual MinerU workflow (``docs/decisions/2026-07-08-manual-mineru.md``)
remains the supported fallback when ``MINERU_API_TOKEN`` is not set.
"""
from __future__ import annotations

from .api_client import (
    DEFAULT_API_VERSION,
    DEFAULT_BASE_URL,
    BatchResult,
    FileSpec,
    MinerUAPIError,
    MinerUAuthError,
    MinerUClient,
    MinerUQuotaError,
    TaskInfo,
    UploadGrant,
)
from .quota import (
    DEFAULT_QUOTA,
    QuotaDecision,
    QuotaState,
    check_quota,
    estimate_pdf_pages,
    load_quota_state,
    quota_path,
    record_pages_used,
    save_quota_state,
)
from .quota import (
    SCHEMA_VERSION as QUOTA_SCHEMA_VERSION,
)
from .submission import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    HANDLE_FILENAME,
    RESULT_EXPIRY_DAYS,
    FinalResult,
    InboxLayout,
    MinerUSubmission,
    SubmissionHandle,
    clear_handle,
    load_handle,
    save_handle,
)

__all__ = [
    "DEFAULT_API_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_QUOTA",
    "DEFAULT_TIMEOUT_S",
    "BatchResult",
    "FinalResult",
    "FileSpec",
    "HANDLE_FILENAME",
    "InboxLayout",
    "MinerUAPIError",
    "MinerUAuthError",
    "MinerUClient",
    "MinerUQuotaError",
    "MinerUSubmission",
    "QUOTA_SCHEMA_VERSION",
    "QuotaDecision",
    "QuotaState",
    "RESULT_EXPIRY_DAYS",
    "SubmissionHandle",
    "TaskInfo",
    "UploadGrant",
    "check_quota",
    "clear_handle",
    "estimate_pdf_pages",
    "load_handle",
    "load_quota_state",
    "quota_path",
    "record_pages_used",
    "save_handle",
    "save_quota_state",
]
