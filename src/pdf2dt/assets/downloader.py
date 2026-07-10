"""Downloader abstraction with an httpx default implementation."""

from __future__ import annotations

import abc
import time
from pathlib import Path
from urllib.parse import urlparse

from .models import DownloadResult, DownloadStatus

# Image MIME types we are willing to accept.
_IMAGE_MIME_PREFIXES = ("image/",)
_MAX_BYTES_DEFAULT = 20 * 1024 * 1024  # 20 MB safety cap


class AssetDownloader(abc.ABC):
    """Pluggable downloader interface for asset localization."""

    @abc.abstractmethod
    def download(self, url: str) -> DownloadResult:
        """Download a single URL. Must not raise — return DownloadResult."""


class HttpxDownloader(AssetDownloader):
    """Default downloader using httpx with bounded retries."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        max_bytes: int = _MAX_BYTES_DEFAULT,
        user_agent: str = "math-content-preprocessor/0.1",
    ) -> None:
        import httpx  # local import keeps the dependency optional for tests

        self._client_factory = lambda: httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._max_bytes = max_bytes

    def download(self, url: str) -> DownloadResult:
        attempts = 0
        last_error = "unknown error"
        with self._client_factory() as client:
            for attempt in range(1, self._max_retries + 1):
                attempts = attempt
                try:
                    with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            last_error = f"HTTP {resp.status_code}"
                            if resp.status_code in (404, 410):
                                # Permanent — do not retry.
                                return DownloadResult(
                                    url=url,
                                    status=DownloadStatus.FAILED,
                                    error=last_error,
                                    http_status=resp.status_code,
                                    attempts=attempts,
                                )
                            time.sleep(self._backoff * attempt)
                            continue
                        content_type = resp.headers.get("Content-Type", "")
                        buf = bytearray()
                        for chunk in resp.iter_bytes():
                            buf.extend(chunk)
                            if len(buf) > self._max_bytes:
                                return DownloadResult(
                                    url=url,
                                    status=DownloadStatus.FAILED,
                                    error=f"exceeds max_bytes={self._max_bytes}",
                                    http_status=200,
                                    attempts=attempts,
                                )
                        return DownloadResult(
                            url=url,
                            status=DownloadStatus.OK,
                            content=bytes(buf),
                            content_type=content_type.split(";")[0].strip().lower(),
                            http_status=200,
                            attempts=attempts,
                        )
                except Exception as exc:  # noqa: BLE001 — surface to caller
                    last_error = f"{type(exc).__name__}: {exc}"
                    time.sleep(self._backoff * attempt)
        return DownloadResult(
            url=url,
            status=DownloadStatus.FAILED,
            error=last_error,
            attempts=attempts,
        )


class LocalMirrorDownloader(AssetDownloader):
    """Serve bytes from a local directory by URL path tail.

    Used by tests: it maps ``https://.../img_p001_001.png`` to
    ``<root>/img_p001_001.png`` and reads the file contents. Useful when
    fixtures cannot reach real MinerU URLs.
    """

    def __init__(self, mirror_root: Path | str, *, default_content_type: str = "image/png") -> None:
        self._root = Path(mirror_root)
        self._default_ct = default_content_type

    def download(self, url: str) -> DownloadResult:
        tail = Path(urlparse(url).path).name
        path = self._root / tail
        if not path.is_file():
            return DownloadResult(
                url=url,
                status=DownloadStatus.FAILED,
                error=f"local mirror miss: {path}",
            )
        return DownloadResult(
            url=url,
            status=DownloadStatus.OK,
            content=path.read_bytes(),
            content_type=self._default_ct,
            http_status=200,
        )


# MIME type detection from file suffix for local-first downloads.
_SUFFIX_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class LocalFirstDownloader(AssetDownloader):
    """Try local files first, then fall back to HTTP.

    This is the recommended production downloader when the user has dropped
    a MinerU export into ``inbox/<task>/images/`` and we want to avoid
    re-downloading already-local content. Resolution order:

      1. ``file://`` URLs: read the file directly.
      2. ``http(s)://`` URLs whose tail filename exists under any of the
         given search paths: read the file with content-type inferred from
         the suffix.
      3. Anything else (or a local miss): delegate to ``HttpxDownloader``.
    """

    def __init__(
        self,
        local_search_paths: list[Path | str],
        *,
        fallback: AssetDownloader | None = None,
    ) -> None:
        self._roots = [Path(p) for p in local_search_paths]
        if fallback is None:
            fallback = HttpxDownloader()
        self._fallback = fallback

    def download(self, url: str) -> DownloadResult:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return self._read_file(Path(parsed.path), url)
        if parsed.scheme in ("http", "https"):
            tail = Path(parsed.path).name
            for root in self._roots:
                candidate = root / tail
                if candidate.is_file():
                    return self._read_file(candidate, url)
            return self._fallback.download(url)
        return self._fallback.download(url)

    def _read_file(self, path: Path, original_url: str) -> DownloadResult:
        if not path.is_file():
            return DownloadResult(
                url=original_url,
                status=DownloadStatus.FAILED,
                error=f"local file missing: {path}",
            )
        suffix = path.suffix.lower()
        content_type = _SUFFIX_TO_MIME.get(suffix, "application/octet-stream")
        return DownloadResult(
            url=original_url,
            status=DownloadStatus.OK,
            content=path.read_bytes(),
            content_type=content_type,
            http_status=200,
        )