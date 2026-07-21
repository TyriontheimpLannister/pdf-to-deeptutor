"""Tests for the MinerU submission adapter.

The full flow is submit → poll → download. Tests use
:class:`httpx.MockTransport` to simulate the MinerU v4 API so no
network is touched. The handlers also assert that the ``Authorization``
header is present and well-formed without ever asserting on the token
value itself — token safety is checked separately via message-text
inspection.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pdf2dt.providers.mineru import (
    HANDLE_FILENAME,
    MinerUAPIError,
    MinerUAuthError,
    MinerUClient,
    MinerUQuotaError,
    MinerUSubmission,
    SubmissionHandle,
    clear_handle,
    load_handle,
    save_handle,
)

# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #


TOKEN = "test-token-deadbeef"  # never asserted on directly
BASE_URL = "https://mineru.test"


def _make_pdf(path: Path, *, size_bytes: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        # A few "pages" worth of bytes — pypdf won't parse this, so
        # estimate_pdf_pages returns None, which is fine for the tests.
        f.write(b"%PDF-1.4\n" + b"X" * (size_bytes - 10) + b"\n%%EOF")


def _make_real_pdf(path: Path, *, pages: int = 3) -> None:
    """Write a minimal valid PDF that pypdf can parse."""
    try:
        import pypdf
    except ImportError:
        # pypdf not installed in this env — fall back to bytes-only PDF.
        _make_pdf(path)
        return
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def _build_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] | None = None,
) -> tuple[MinerUClient, httpx.Client]:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="")
    return (
        MinerUClient(
            TOKEN,
            base_url=BASE_URL,
            client=http_client,
            sleep=sleep or (lambda _s: None),
            max_retries=0,
        ),
        http_client,
    )


def _build_submission(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    workspace_root: Path | None = None,
    sleep: Callable[[float], None] | None = None,
    quota_pages_per_day: int = 1000,
) -> tuple[MinerUSubmission, httpx.Client]:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="")
    submission = MinerUSubmission(
        TOKEN,
        base_url=BASE_URL,
        workspace_root=workspace_root,
        client=http_client,
        sleep=sleep or (lambda _s: None),
        quota_pages_per_day=quota_pages_per_day,
    )
    return submission, http_client


def _ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def _build_zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------- #
# Auth & token safety
# ---------------------------------------------------------------------- #


def test_mineru_client_rejects_empty_token() -> None:
    with pytest.raises(MinerUAuthError, match="MINERU_API_TOKEN"):
        MinerUClient("")


def test_401_raises_auth_error_without_token_echo(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(401, json={"code": 401, "msg": "bad token"})

    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    submission, client = _build_submission(handler)
    try:
        with pytest.raises(MinerUAuthError) as exc_info:
            submission.submit(pdf)
        # The error message must NOT contain the token.
        assert TOKEN not in str(exc_info.value)
    finally:
        client.close()
    # Sanity: the request did carry a bearer token.
    assert captured["auth"] == f"Bearer {TOKEN}"


def test_403_raises_auth_error_without_token_echo(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 403, "msg": "forbidden"})

    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    submission, client = _build_submission(handler)
    try:
        with pytest.raises(MinerUAuthError) as exc_info:
            submission.submit(pdf)
        assert TOKEN not in str(exc_info.value)
    finally:
        client.close()


# ---------------------------------------------------------------------- #
# Happy path: submit → wait → download
# ---------------------------------------------------------------------- #


def _happy_handler(
    *,
    batch_id: str = "batch-1",
    zip_url: str | None = None,
    zip_bytes: bytes | None = None,
    final_state: str = "done",
    page_count_actual: int | None = 12,
    full_zip_url: str = "https://cdn.example.com/result.zip",
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler that lets one PDF flow through submit→poll→download."""
    state = {"calls": 0, "zip_url": zip_url or full_zip_url, "zip_bytes": zip_bytes}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if request.method == "POST" and request.url.path == "/api/v4/file-urls/batch":
            return _ok(
                {
                    "code": 0,
                    "data": {
                        "batch_id": batch_id,
                        "file_urls": [
                            "https://upload.example.com/signed-put"
                        ],
                    },
                }
            )
        if request.method == "PUT" and request.url.host == "upload.example.com":
            return httpx.Response(200, content=b"")
        if request.method == "GET" and request.url.path.startswith(
            f"/api/v4/extract-results/batch/{batch_id}"
        ):
            # First poll: still running; second: done.
            if state["calls"] <= 3:
                return _ok(
                    {
                        "code": 0,
                        "data": [
                            {
                                "data_id": "ignored",
                                "status": "running",
                            }
                        ],
                    }
                )
            task: dict[str, Any] = {
                "data_id": "ignored",
                "status": final_state,
            }
            if state["zip_url"]:
                task["full_zip_url"] = state["zip_url"]
            if page_count_actual is not None:
                task["page_count"] = page_count_actual
            return _ok({"code": 0, "data": [task]})
        if request.method == "GET" and request.url.host == "cdn.example.com":
            return httpx.Response(
                200, content=state["zip_bytes"] or b""
            )
        return httpx.Response(404, json={"code": 404, "msg": "not found"})

    return handler


def test_happy_path_submit_wait_download(tmp_path: Path) -> None:
    zip_bytes = _build_zip_bytes(
        {
            "full.md": b"# Title\n\nbody",
            "layout.json": b'{"pages": []}',
            "images/img1.png": b"\x89PNGfake",
            "source.pdf": b"%PDF-1.4 fake",
        }
    )
    handler = _happy_handler(zip_bytes=zip_bytes)
    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    inbox = tmp_path / "inbox" / "task-1"
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        assert handle.batch_id == "batch-1"
        result = submission.wait(handle, poll_interval_s=0.0, timeout_s=5.0)
        assert result.state == "done"
        assert result.full_zip_url == "https://cdn.example.com/result.zip"
        assert result.page_count_actual == 12

        layout = submission.download(result, inbox)
        assert layout.inbox_dir == inbox
        assert layout.markdown_path == inbox / "full.md"
        assert layout.layout_path == inbox / "layout.json"
        assert layout.images_dir is not None
        assert layout.source_pdf_path == inbox / "source.pdf"
        assert (inbox / "full.md").read_text(encoding="utf-8") == "# Title\n\nbody"
        assert (inbox / "images" / "img1.png").exists()
    finally:
        client.close()


def test_download_is_idempotent(tmp_path: Path) -> None:
    zip_bytes = _build_zip_bytes({"full.md": b"# V1"})
    handler = _happy_handler(zip_bytes=zip_bytes)
    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    inbox = tmp_path / "inbox" / "task-2"
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        result = submission.wait(handle, poll_interval_s=0.0, timeout_s=5.0)
        # First download
        submission.download(result, inbox)
        first_files = sorted(p.name for p in inbox.iterdir() if p.is_file())
        # Second download — should overwrite cleanly, not duplicate.
        submission.download(result, inbox)
        second_files = sorted(p.name for p in inbox.iterdir() if p.is_file())
        assert first_files == second_files
        assert (inbox / "full.md").read_text(encoding="utf-8") == "# V1"
    finally:
        client.close()


# ---------------------------------------------------------------------- #
# 429 retries with backoff
# ---------------------------------------------------------------------- #


def test_429_retries_with_backoff(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v4/file-urls/batch":
            # First call: 429 with Retry-After. Second call: success.
            call_count = handler._calls  # type: ignore[attr-defined]
            handler._calls = call_count + 1  # type: ignore[attr-defined]
            if call_count == 0:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0.1"},
                    json={"code": 429, "msg": "slow down"},
                )
            return _ok(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "b-1",
                        "file_urls": ["https://upload.example.com/u"],
                    },
                }
            )
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(404)

    handler._calls = 0  # type: ignore[attr-defined]

    # Build with retries enabled.
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="")
    client = MinerUClient(
        TOKEN,
        base_url=BASE_URL,
        client=http_client,
        sleep=sleeps.append,
        max_retries=2,
        backoff_base_s=0.5,
    )
    try:
        grant = client.request_upload(
            [
                # FileSpec is in api_client; import locally
                __import__(
                    "pdf2dt.providers.mineru.api_client",
                    fromlist=["FileSpec"],
                ).FileSpec(name="x.pdf", data_id="x", size_bytes=10)
            ]
        )
        assert grant.batch_id == "b-1"
        # First retry used Retry-After=0.1 (clamped).
        assert sleeps == [0.1]
    finally:
        http_client.close()


# ---------------------------------------------------------------------- #
# Polling timeout persists handle
# ---------------------------------------------------------------------- #


def test_polling_timeout_raises_and_handle_remains(tmp_path: Path) -> None:
    """A timeout must NOT clear the persisted handle. The user retries."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "b-stuck",
                        "file_urls": ["https://upload.example.com/u"],
                    },
                }
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return _ok(
                {
                    "code": 0,
                    "data": [{"status": "running"}],
                }
            )
        return httpx.Response(404)

    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    inbox = tmp_path / "inbox" / "task-stuck"
    inbox.mkdir(parents=True)
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        save_handle(inbox, handle)
        with pytest.raises(MinerUAPIError, match="timed out"):
            submission.wait(handle, poll_interval_s=0.0, timeout_s=0.5)
        # Handle is still on disk so the user can resume.
        assert (inbox / HANDLE_FILENAME).is_file()
        loaded = load_handle(inbox)
        assert loaded.batch_id == "b-stuck"
    finally:
        client.close()


def test_clear_handle_removes_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    handle = SubmissionHandle(
        task_id="t1",
        batch_id="b1",
        submitted_at="2026-07-14T00:00:00+00:00",
        pdf_filename="src.pdf",
        pdf_sha256="abc",
        page_count_estimated=5,
        data_id="d1",
    )
    save_handle(inbox, handle)
    assert (inbox / HANDLE_FILENAME).is_file()
    assert clear_handle(inbox) is True
    assert not (inbox / HANDLE_FILENAME).exists()
    # Second call returns False (nothing to delete).
    assert clear_handle(inbox) is False


# ---------------------------------------------------------------------- #
# Failed task state
# ---------------------------------------------------------------------- #


def test_failed_task_state_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "b-fail",
                        "file_urls": ["https://upload.example.com/u"],
                    },
                }
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return _ok(
                {
                    "code": 0,
                    "data": [
                        {
                            "status": "failed",
                            "err_msg": "server crashed",
                        }
                    ],
                }
            )
        return httpx.Response(404)

    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        result = submission.wait(handle, poll_interval_s=0.0, timeout_s=5.0)
        assert result.state == "failed"
        with pytest.raises(MinerUAPIError, match="Cannot download"):
            submission.download(result, tmp_path / "out")
    finally:
        client.close()


# ---------------------------------------------------------------------- #
# --no-wait path: handle written, no polling
# ---------------------------------------------------------------------- #


def test_no_wait_writes_handle_only(tmp_path: Path) -> None:
    """Mirror what submit_to_mineru.py --no-wait does: submit, save, exit."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "b-no-wait",
                        "file_urls": ["https://upload.example.com/u"],
                    },
                }
            )
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(404)

    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    inbox = tmp_path / "inbox" / "task-no-wait"
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        save_handle(inbox, handle)
    finally:
        client.close()

    # No download happened: inbox has the handle only.
    assert (inbox / HANDLE_FILENAME).is_file()
    children = sorted(p.name for p in inbox.iterdir())
    assert children == [HANDLE_FILENAME]


# ---------------------------------------------------------------------- #
# Quota refused pre-submit
# ---------------------------------------------------------------------- #


def test_quota_pre_check_refuses_over_quota(tmp_path: Path) -> None:
    """The local quota state refuses a submission that would exceed 1000 pages."""

    def handler(_request: httpx.Request) -> httpx.Response:
        # The handler must never be reached — quota pre-check fires first.
        return httpx.Response(500, json={"code": 500, "msg": "unreachable"})

    pdf = tmp_path / "src.pdf"
    _make_real_pdf(pdf, pages=200)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Seed the quota state to 900/1000 — adding 200 should refuse.
    from datetime import datetime, timezone

    from pdf2dt.providers.mineru.quota import QuotaState, save_quota_state
    today = datetime.now(timezone.utc).date().isoformat()
    save_quota_state(workspace, QuotaState(date_utc=today, pages_used=900))

    submission, client = _build_submission(
        handler, workspace_root=workspace, quota_pages_per_day=1000
    )
    try:
        with pytest.raises(MinerUQuotaError, match="Refusing"):
            submission.submit(pdf)
    finally:
        client.close()


# ---------------------------------------------------------------------- #
# Handle persistence round-trip
# ---------------------------------------------------------------------- #


def test_handle_round_trip(tmp_path: Path) -> None:
    handle = SubmissionHandle(
        task_id="task-rt",
        batch_id="batch-rt",
        submitted_at="2026-07-14T01:02:03+00:00",
        pdf_filename="book.pdf",
        pdf_sha256="abcdef",
        page_count_estimated=42,
        data_id="book.pdf-abcdef",
    )
    inbox = tmp_path / "inbox-rt"
    save_handle(inbox, handle)
    loaded = load_handle(inbox)
    assert loaded == handle


# ---------------------------------------------------------------------- #
# Inbox normalization
# ---------------------------------------------------------------------- #


def test_download_normalizes_mineru_aliases(tmp_path: Path) -> None:
    """MinerU's archive uses ``MinerU_markdown_full.md`` and a nested
    source PDF — the adapter must rename them to ``full.md`` and
    ``source.pdf`` to match the inbox contract."""
    zip_bytes = _build_zip_bytes(
        {
            "MinerU_markdown_full.md": b"# from MinerU",
            "MinerU_layout.json": b'{"pdf_info": []}',
            "images/fig.png": b"\x89PNGfake",
            "original.pdf": b"%PDF-1.4 original",
        }
    )
    handler = _happy_handler(zip_bytes=zip_bytes)
    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf)
    inbox = tmp_path / "inbox" / "task-alias"
    submission, client = _build_submission(handler)
    try:
        handle = submission.submit(pdf)
        result = submission.wait(handle, poll_interval_s=0.0, timeout_s=5.0)
        layout = submission.download(result, inbox)
        assert (inbox / "full.md").read_text(encoding="utf-8") == "# from MinerU"
        assert (inbox / "layout.json").read_text(encoding="utf-8") == '{"pdf_info": []}'
        assert layout.source_pdf_path == inbox / "source.pdf"
        assert (inbox / "source.pdf").exists()
        assert not (inbox / "original.pdf").exists()
    finally:
        client.close()
