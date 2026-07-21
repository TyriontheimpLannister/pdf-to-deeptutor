"""Low-level HTTP client for the MinerU cloud API.

This module wraps the four network calls the MinerU v4 API exposes for
the *batch upload* flow (the one that accepts a local PDF rather than a
pre-hosted URL):

1. ``POST /api/v4/file-urls/batch`` — request pre-signed PUT URLs and
   a ``batch_id`` that groups the upload + extraction together.
2. ``PUT <signed_url>`` — upload the PDF binary to MinerU's object
   storage. The signed URL is consumed once.
3. ``GET /api/v4/extract-results/batch/{batch_id}`` — poll the batch
   until each file reaches a terminal state (``done`` | ``failed`` |
   ``expired``). The response carries ``full_zip_url`` per file.
4. ``GET <full_zip_url>`` — stream the result archive to disk.

Contract notes
--------------

* The token is read **once** at construction and never persisted. It
  is sent only as ``Authorization: Bearer <token>``; logger output
  scrubs it. Tests inject an :class:`httpx.Client` with
  :class:`httpx.MockTransport` so no real network is touched.
* API responses are not trusted: a missing ``data`` envelope or an
  unexpected ``code`` value raises :class:`MinerUAPIError` with a
  status-only message (never the token, never the raw body that may
  contain echoed identifiers).
* Rate limiting: ``429`` retries with exponential backoff up to
  ``max_retries``; the ``Retry-After`` header is honoured when present.
* Quota exceeded: ``code=403`` style enforcement is left to the
  higher-level :mod:`quota` module — this client raises
  :class:`MinerUQuotaError` only when the API itself reports it
  on submit.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://mineru.net"
DEFAULT_API_VERSION = "v4"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_POLL_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE_S = 0.5
DEFAULT_USER_AGENT = "math-content-preprocessor/0.1"

# Terminal task states the API reports inside ``data[].status``.
TERMINAL_STATES = frozenset({"done", "success", "failed", "expired"})
SUCCESS_STATES = frozenset({"done", "success"})


class MinerUAPIError(RuntimeError):
    """Raised when the API returns an error that cannot be retried."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MinerUQuotaError(MinerUAPIError):
    """Raised when the API itself reports the daily quota is exhausted."""


class MinerUAuthError(MinerUAPIError):
    """Raised on 401/403 from the API. Never echoes the token."""


# ---------------------------------------------------------------------- #
# Response dataclasses
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class FileSpec:
    """One file to be uploaded as part of a batch."""

    name: str
    data_id: str
    size_bytes: int


@dataclass(frozen=True)
class UploadGrant:
    """Result of requesting upload URLs for a batch."""

    batch_id: str
    file_urls: list[str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TaskInfo:
    """Snapshot of one task inside a batch."""

    status: str
    full_zip_url: str | None = None
    err_msg: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def is_success(self) -> bool:
        return self.status in SUCCESS_STATES


@dataclass(frozen=True)
class BatchResult:
    """Result of polling a batch. Always carries one entry per file."""

    batch_id: str
    tasks: list[TaskInfo]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def all_terminal(self) -> bool:
        return bool(self.tasks) and all(t.is_terminal for t in self.tasks)


# ---------------------------------------------------------------------- #
# Client
# ---------------------------------------------------------------------- #


class MinerUClient:
    """HTTP client for the MinerU v4 cloud API.

    The client is *stateless* between calls — every method issues a
    fresh request. Callers may pass a shared :class:`httpx.Client` for
    connection pooling, or omit it and let each call create and close
    its own client.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        user_agent: str = DEFAULT_USER_AGENT,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not token:
            raise MinerUAuthError(
                "MINERU_API_TOKEN is empty; cannot construct MinerUClient",
                status_code=None,
            )
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout_s
        self._poll_timeout = poll_timeout_s
        self._max_retries = max(0, max_retries)
        self._backoff_base = max(0.0, backoff_base_s)
        self._user_agent = user_agent
        self._client = client
        self._sleep = sleep

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def request_upload(self, files: list[FileSpec]) -> UploadGrant:
        """Ask MinerU for pre-signed PUT URLs for one batch."""
        if not files:
            raise MinerUAPIError("Cannot request upload URLs for an empty batch.")
        payload = {
            "files": [
                {"name": f.name, "data_id": f.data_id, "size": f.size_bytes}
                for f in files
            ],
        }
        data = self._post(
            f"/api/{self._api_version}/file-urls/batch",
            json=payload,
        )
        grant = _parse_upload_grant(data)
        if len(grant.file_urls) != len(files):
            raise MinerUAPIError(
                f"MinerU returned {len(grant.file_urls)} upload URLs "
                f"for {len(files)} requested files"
            )
        return grant

    def upload_pdf(self, upload_url: str, pdf_path: Path) -> None:
        """PUT a local PDF to a pre-signed URL returned by ``request_upload``."""
        if not pdf_path.is_file():
            raise MinerUAPIError(f"PDF not found: {pdf_path}")
        size = pdf_path.stat().st_size
        with pdf_path.open("rb") as f:
            self._put(upload_url, content=f, content_length=size)

    def get_batch(self, batch_id: str) -> BatchResult:
        """Poll the batch status. Returns a snapshot; does not block."""
        data = self._get(
            f"/api/{self._api_version}/extract-results/batch/{batch_id}",
            timeout=self._poll_timeout,
        )
        return _parse_batch_result(batch_id, data)

    def download_archive(self, url: str, target_path: Path) -> Path:
        """Stream a result archive to ``target_path``. Idempotent overwrite."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        client = self._client_or_new(timeout=self._timeout)
        owns_client = self._client is None
        try:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > 1024:
                            body = body[:1024]
                            break
                    raise MinerUAPIError(
                        f"download failed: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                with target_path.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        finally:
            if owns_client:
                client.close()
        return target_path

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _client_or_new(self, *, timeout: float | None = None):
        if self._client is not None:
            return self._client
        return httpx.Client(
            timeout=timeout if timeout is not None else self._timeout,
            follow_redirects=True,
            headers={"User-Agent": self._user_agent},
        )

    def _headers(self) -> dict[str, str]:
        # Token only travels in the Authorization header. Never logged.
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
        }

    def _post(self, path: str, *, json: dict[str, Any], timeout: float | None = None):
        url = self._absolute(path)
        return self._request_with_retry(
            "POST", url, json=json, timeout=timeout or self._timeout
        )

    def _get(self, path: str, *, timeout: float | None = None):
        url = self._absolute(path)
        return self._request_with_retry(
            "GET", url, timeout=timeout or self._poll_timeout
        )

    def _put(self, url: str, *, content, content_length: int) -> None:
        # PUT to a pre-signed URL must NOT carry the Authorization header.
        headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(content_length),
            "User-Agent": self._user_agent,
        }
        client = self._client_or_new(timeout=self._timeout)
        owns_client = self._client is None
        try:
            resp = client.put(url, content=content, headers=headers)
        finally:
            if owns_client:
                client.close()
        if resp.status_code not in (200, 201, 204):
            raise MinerUAPIError(
                f"upload PUT failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

    def _absolute(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            client = self._client_or_new(timeout=timeout)
            owns_client = self._client is None
            try:
                resp = client.request(
                    method, url, json=json, headers=self._headers()
                )
            except httpx.TransportError as exc:
                last_exc = exc
                self._backoff(None, attempt)
                continue
            finally:
                if owns_client:
                    client.close()
            if resp.status_code == 401 or resp.status_code == 403:
                raise MinerUAuthError(
                    f"MinerU rejected the token (HTTP {resp.status_code}); "
                    "check MINERU_API_TOKEN",
                    status_code=resp.status_code,
                )
            if resp.status_code == 429:
                last_exc = MinerUAPIError(
                    "MinerU rate-limited the request (HTTP 429)",
                    status_code=429,
                )
                self._backoff(resp, attempt)
                continue
            if resp.status_code >= 500:
                last_exc = MinerUAPIError(
                    f"MinerU server error: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
                self._backoff(resp, attempt)
                continue
            if resp.status_code >= 400:
                raise MinerUAPIError(
                    f"MinerU returned HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            try:
                data = resp.json()
            except ValueError as exc:
                raise MinerUAPIError(
                    f"MinerU returned non-JSON body: {exc}",
                    status_code=resp.status_code,
                ) from exc
            _envelop_check(data)
            return data
        if last_exc is not None:
            raise MinerUAPIError(
                f"MinerU request failed after {self._max_retries + 1} attempts: "
                f"{last_exc}"
            ) from last_exc
        raise MinerUAPIError("MinerU request failed for unknown reasons")

    def _backoff(self, resp: httpx.Response | None, attempt: int) -> None:
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    self._sleep(min(float(retry_after), 60.0))
                    return
                except ValueError:
                    pass
        delay = self._backoff_base * (2**attempt)
        self._sleep(min(delay, 60.0))


# ---------------------------------------------------------------------- #
# Response parsing
# ---------------------------------------------------------------------- #


def _envelop_check(data: Any) -> None:
    if not isinstance(data, dict):
        raise MinerUAPIError(f"MinerU response root is not an object: {type(data).__name__}")
    code = data.get("code")
    if code is None:
        return  # Some endpoints omit ``code``; trust the HTTP status only.
    if code == 0:
        return
    if code in (403, 429) or code == "quota_exceeded":
        raise MinerUQuotaError(
            f"MinerU reports quota or auth refusal (code={code})",
            status_code=None,
        )
    raise MinerUAPIError(f"MinerU returned non-zero code={code}: {data.get('msg', '')}")


def _parse_upload_grant(data: dict[str, Any]) -> UploadGrant:
    inner = data.get("data")
    if not isinstance(inner, dict):
        raise MinerUAPIError("MinerU upload-grant response missing 'data' object")
    batch_id = inner.get("batch_id")
    file_urls = inner.get("file_urls") or inner.get("urls") or []
    if not isinstance(batch_id, str) or not batch_id:
        raise MinerUAPIError("MinerU upload-grant response missing batch_id")
    if not isinstance(file_urls, list) or not file_urls:
        raise MinerUAPIError("MinerU upload-grant response missing file_urls")
    clean_urls = [u for u in file_urls if isinstance(u, str) and u]
    if len(clean_urls) != len(file_urls):
        raise MinerUAPIError("MinerU returned malformed file_urls list")
    return UploadGrant(batch_id=batch_id, file_urls=clean_urls, raw=inner)


def _parse_batch_result(batch_id: str, data: dict[str, Any]) -> BatchResult:
    inner = data.get("data")
    # The batch endpoint returns a list of per-file task dicts, but
    # some versions wrap the list under ``extract_result``. Accept both.
    if isinstance(inner, list):
        tasks_raw = inner
    elif isinstance(inner, dict):
        tasks_raw = inner.get("extract_result") or inner.get("tasks") or []
    else:
        raise MinerUAPIError("MinerU batch-result response missing 'data' payload")
    tasks: list[TaskInfo] = []
    for entry in tasks_raw:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").lower()
        full_zip = entry.get("full_zip_url") or entry.get("zip_url")
        err_msg = entry.get("err_msg") or entry.get("error_msg")
        tasks.append(
            TaskInfo(
                status=status or "unknown",
                full_zip_url=str(full_zip) if full_zip else None,
                err_msg=str(err_msg) if err_msg else None,
                raw=entry,
            )
        )
    if not tasks:
        raise MinerUAPIError("MinerU batch-result returned no task entries")
    return BatchResult(batch_id=batch_id, tasks=tasks, raw=data)


__all__ = [
    "BatchResult",
    "DEFAULT_API_VERSION",
    "DEFAULT_BASE_URL",
    "FileSpec",
    "MinerUAPIError",
    "MinerUAuthError",
    "MinerUClient",
    "MinerUQuotaError",
    "SUCCESS_STATES",
    "TERMINAL_STATES",
    "TaskInfo",
    "UploadGrant",
]
