"""Data models for asset localization."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import NewType

from pydantic import BaseModel, Field

# Stable identifier for a localized asset. Format: sha256[:12].
AssetId = NewType("AssetId", str)


class AssetValidationError(Exception):
    """Raised when a downloaded asset fails validation."""


class DownloadStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"


class DownloadResult(BaseModel):
    """Raw result from a downloader for one URL."""

    url: str
    status: DownloadStatus
    content: bytes | None = None
    content_type: str | None = None
    error: str | None = None
    http_status: int | None = None
    attempts: int = 1


class Asset(BaseModel):
    """A localized image asset, ready to embed in an export."""

    asset_id: AssetId
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    mime_type: str
    byte_size: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    local_path: Path
    source_url: str | None = None
    source_page: int | None = None


class AssetRegistry(BaseModel):
    """Maps source URLs and asset IDs to their localized Asset."""

    by_url: dict[str, AssetId] = Field(default_factory=dict)
    by_id: dict[AssetId, Asset] = Field(default_factory=dict)
    failures: dict[str, str] = Field(default_factory=dict)

    def add(self, asset: Asset, *, source_url: str | None = None) -> None:
        self.by_id[asset.asset_id] = asset
        url = source_url if source_url is not None else asset.source_url
        if url is not None:
            self.by_url[url] = asset.asset_id

    def add_alias(self, url: str, asset: Asset) -> None:
        """Record another source reference for an already localized asset."""
        self.by_url[url] = asset.asset_id

    def add_failure(self, url: str, error: str) -> None:
        """Record a source image that could not be localized."""
        self.failures[url] = error

    def get_by_url(self, url: str) -> Asset | None:
        asset_id = self.by_url.get(url)
        if asset_id is None:
            return None
        return self.by_id.get(asset_id)

    def get_by_id(self, asset_id: AssetId) -> Asset | None:
        return self.by_id.get(asset_id)

    def __len__(self) -> int:
        return len(self.by_id)
