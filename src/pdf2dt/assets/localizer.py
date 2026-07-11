"""Asset localizer — Stage 2 pipeline step."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Callable
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..inbox.models import LoadedMinerU
from .downloader import AssetDownloader
from .models import Asset, AssetId, AssetRegistry, AssetValidationError, DownloadStatus

# Markdown image reference: ![alt](url)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# Pillow-recognized image MIME prefixes
_VALID_MIME_PREFIXES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")


class AssetLocalizer:
    """Download, validate, hash, deduplicate, persist, and rewrite assets."""

    def __init__(
        self,
        assets_dir: Path | str,
        downloader: AssetDownloader,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._downloader = downloader
        self._progress = progress_callback

    def localize(self, loaded: LoadedMinerU) -> AssetRegistry:
        """Process every remote URL in the loaded MinerU output."""
        registry = AssetRegistry()
        urls = list(loaded.image_references)
        for idx, url in enumerate(urls, start=1):
            if self._progress:
                self._progress(url, idx, len(urls))

            asset, error = self._localize_one(url, registry)
            if asset is None:
                registry.add_failure(url, error or "unknown localization error")
                continue
            registry.add(asset, source_url=url)

        return registry

    def rewrite_markdown(self, markdown_text: str, registry: AssetRegistry) -> str:
        """Replace remote URLs in Markdown with local asset path references.

        Accepts ``http://``, ``https://``, ``file://``, and MinerU-style
        ``images/<filename>`` references. The lookup falls back to matching
        by the URL tail filename when the full URL is not in the registry
        (covers the case where the user dropped files locally and the
        Markdown references them by relative path).
        """

        def _sub(match: re.Match[str]) -> str:
            alt = match.group(1)
            url = match.group(2).strip()
            asset = self._lookup_asset(url, registry)
            if asset is None:
                return match.group(0)
            return f"![{alt}](assets/{asset.asset_id}.{self._ext(asset)})"

        return _MARKDOWN_IMAGE_RE.sub(_sub, markdown_text)

    def rewrite_layout(self, layout_data: dict, registry: AssetRegistry) -> dict:
        """Replace remote URLs in layout.json with local asset IDs (in place)."""
        if layout_data is None:
            return {}
        for page in layout_data.get("pages", []):
            for block in page.get("blocks", []):
                url = block.get("image_url")
                if isinstance(url, str):
                    asset = self._lookup_asset(url, registry)
                    if asset is not None:
                        block["image_url"] = f"assets/{asset.asset_id}.{self._ext(asset)}"
                        block["asset_id"] = asset.asset_id
        return layout_data

    def _lookup_asset(self, url: str, registry: AssetRegistry) -> Asset | None:
        """Look up an asset by URL, with an unambiguous filename fallback.

        This covers both full URLs (including ``file://``) and MinerU-style
        relative paths like ``images/<filename>``.
        """
        asset = registry.get_by_url(url)
        if asset is not None:
            return asset
        tail = url.rsplit("/", 1)[-1]
        if not tail:
            return None
        matched_ids = {
            asset_id
            for source_url, asset_id in registry.by_url.items()
            if source_url.rsplit("/", 1)[-1] == tail
        }
        if len(matched_ids) != 1:
            return None
        return registry.get_by_id(next(iter(matched_ids)))

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _localize_one(
        self, url: str, registry: AssetRegistry
    ) -> tuple[Asset | None, str | None]:
        # Already processed this URL? Reuse.
        existing = registry.get_by_url(url)
        if existing is not None:
            return existing, None

        result = self._downloader.download(url)
        if result.status != DownloadStatus.OK or result.content is None:
            return None, result.error or "downloader returned no content"

        try:
            asset = self._build_asset(result.content, result.content_type, url)
        except AssetValidationError as exc:
            return None, str(exc)

        # Dedup by content hash: same content under a different URL reuses
        # the existing asset (but we still want the new URL → asset_id map).
        dup = self._find_duplicate(asset.sha256, registry)
        if dup is not None:
            return dup, None

        # Persist the raw bytes we just downloaded. If a previous run already
        # wrote the file, leave it alone (idempotent).
        if not asset.local_path.exists():
            asset.local_path.write_bytes(result.content)
        return asset, None

    def _build_asset(self, content: bytes, content_type: str | None, url: str) -> Asset:
        if not content_type or not content_type.startswith(_VALID_MIME_PREFIXES):
            raise AssetValidationError(f"unsupported MIME type: {content_type}")
        if len(content) < 32:
            raise AssetValidationError("content too small")

        sha = hashlib.sha256(content).hexdigest()
        try:
            with Image.open(io.BytesIO(content)) as img:
                img.verify()  # type: ignore[attr-defined]
            # verify() consumes the image; reopen to read dimensions
            with Image.open(io.BytesIO(content)) as img:
                width, height = img.size
        except (UnidentifiedImageError, Exception) as exc:  # noqa: BLE001
            raise AssetValidationError(f"not a decodable image: {exc}") from exc

        asset_id = AssetId(sha[:12])
        ext = self._ext_from_mime(content_type)
        local_path = self.assets_dir / f"{asset_id}.{ext}"
        return Asset(
            asset_id=asset_id,
            sha256=sha,
            mime_type=content_type,
            byte_size=len(content),
            width=width,
            height=height,
            local_path=local_path,
            source_url=url,
        )

    def _find_duplicate(self, sha: str, registry: AssetRegistry) -> Asset | None:
        for asset in registry.by_id.values():
            if asset.sha256 == sha:
                return asset
        return None

    @staticmethod
    def _ext_from_mime(mime: str) -> str:
        return {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }.get(mime, "bin")

    @staticmethod
    def _ext(asset: Asset) -> str:
        suffix = asset.local_path.suffix.lstrip(".")
        return suffix or "bin"


def localize_loaded_task(
    loaded: LoadedMinerU,
    assets_dir: Path | str,
    downloader: AssetDownloader,
) -> AssetRegistry:
    """Convenience entry point."""
    return AssetLocalizer(assets_dir, downloader).localize(loaded)
