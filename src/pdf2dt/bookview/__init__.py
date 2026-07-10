"""Stage 3 — BookView builder.

A *BookView* is the canonical in-memory representation of the source
content after normalization. It links:

* items extracted from ``normalized/full.md`` (see :mod:`pdf2dt.outlining.items`)
* source blocks from ``normalized/layout.localized.json``
* localized assets from ``normalized/assets_registry.json``
* topic assignments from Stage 4b ``topic_assignments/assignments.json``

Public surface:

* :class:`BookView` / :class:`Chapter` / :class:`Section` / :class:`BookItem` /
  :class:`SourceBlockRef` / :class:`AssetRef` — dataclasses for the
  resulting JSON document.
* :class:`BookViewBuilder` — given a project workspace, build a
  :class:`BookView`.
* :func:`build_book_view` — convenience wrapper that loads, builds,
  and persists the JSON under ``book_view/book_view.json``.
* :func:`adapt_mineru_layout` / :func:`is_mineru_layout` — adapter
  for the real MinerU ``pdf_info[]`` layout. The BookView builder
  transparently invokes this adapter when the input does not use
  the simplified ``pages[]`` schema.

The builder is deterministic: given the same normalized inputs, the
output JSON is byte-stable across runs (modulo asset_url fingerprint
re-derivation). It does not call any external model.
"""
from .builder import (
    AssetRef,
    BookItem,
    BookView,
    BookViewBuildError,
    BookViewBuilder,
    Chapter,
    Section,
    SourceBlockRef,
    build_book_view,
)
from .mineru_adapter import adapt_mineru_layout, is_mineru_layout

__all__ = [
    "AssetRef",
    "BookItem",
    "BookView",
    "BookViewBuildError",
    "BookViewBuilder",
    "Chapter",
    "Section",
    "SourceBlockRef",
    "adapt_mineru_layout",
    "build_book_view",
    "is_mineru_layout",
]