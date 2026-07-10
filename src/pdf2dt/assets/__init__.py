"""Asset localization — Stage 2 of the pipeline.

Downloads remote image URLs from MinerU output, validates them, hashes
content for deduplication, persists them under a project-local assets
directory, and rewrites references in Markdown / layout.json to local
asset IDs.

The downloader is injected so the test suite can run against local
fixtures without network access.
"""

from .localizer import AssetLocalizer, localize_loaded_task
from .models import Asset, AssetRegistry, AssetValidationError, DownloadResult
from .downloader import (
    AssetDownloader,
    HttpxDownloader,
    LocalFirstDownloader,
    LocalMirrorDownloader,
)

__all__ = [
    "AssetLocalizer",
    "localize_loaded_task",
    "Asset",
    "AssetRegistry",
    "AssetValidationError",
    "DownloadResult",
    "AssetDownloader",
    "HttpxDownloader",
    "LocalMirrorDownloader",
    "LocalFirstDownloader",
]