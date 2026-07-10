"""MinerU ``pdf_info[]`` schema adapter.

BookView's ``_load_layout`` understands two layouts:

1. The simplified fixture layout: ``pages[]`` of ``blocks[]`` with
   ``type``, ``bbox``, ``text``, ``image_url``, ``asset_id`` etc.
2. Real MinerU layouts (this module): top-level ``pdf_info[]`` of
   pages, each carrying ``preproc_blocks[]`` and ``para_blocks[]``.

This adapter flattens the MinerU layout into the fixture layout's
shape, so :class:`BookViewBuilder` can consume it without code
changes. The mapping is intentionally lossy:

* ``para_blocks[]`` becomes the page's ``blocks[]`` (they are
  already paragraph-level for text + image + title).
* Text content is collected from ``para_blocks[].blocks[].lines
  [].spans[].content``.
* Image blocks emit ``image_url`` from
  ``preproc_blocks[].blocks[].lines[].spans[].image_path``.
* No semantic text-type inference (definition / theorem / example)
  — MinerU only exposes ``text`` / ``title`` / ``image``. The
  BookView builder matches by heading, paragraph, or figure plus
  token / head overlap, so the semantic types are recovered at the
  matcher layer, not here.

The function returns the same shape as the fixture layout's
``pages`` list, ready to feed :func:`BookViewBuilder._index_blocks`.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


# Mapping MinerU para block type → fixture layout block type. Fixture
# builder treats "figure" specially; non-figure text and headings go
# through the same item-block matcher anyway.
_TYPE_MAP = {
    "image": "figure",
    "title": "heading",
    "text": "paragraph",
    "table": "table",
    "equation": "equation",
    "list": "list",
}


def _bbox(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = block.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _collect_text(para: dict[str, Any]) -> str:
    """Concatenate every span's ``content`` under this para block.

    Many MinerU text/title blocks carry their content only inside
    ``blocks[].lines[].spans[].content``. We walk recursively and
    join non-None contents with single spaces.
    """
    parts: list[str] = []
    para_blocks = para.get("blocks") or []
    for block in para_blocks:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = span.get("content")
                if text:
                    parts.append(str(text))
    if not parts and isinstance(para.get("text"), str):
        # Some MinerU versions embed a top-level ``text`` field.
        parts.append(para["text"])
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _first_image_url(para: dict[str, Any]) -> tuple[str | None, tuple[float, float, float, float] | None]:
    """Return the first ``image_path`` under a paragraph, plus its bbox."""
    for block in para.get("blocks") or []:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                if span.get("type") == "image" or span.get("image_path"):
                    url = span.get("image_path")
                    if url:
                        bb = _bbox(span) or _bbox(line) or _bbox(block)
                        return str(url), bb
    return None, None


def _title_level(page_index: int, prev_levels: list[int], text: str) -> int | None:
    """Infer a Markdown-style heading level from the title text.

    MinerU emits only "title" without a level. We treat the first
    title on a page as level 1, subsequent ones as level 2, and
    fall back to 2 when previous pages have already established a
    chapter heading.
    """
    if not text.strip():
        return None
    if not prev_levels:
        return 1
    return 2


def adapt_mineru_layout(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a MinerU ``pdf_info[]`` layout into the fixture layout.

    Returns the ``pages[]`` list — same call site can feed the result
    straight into :func:`BookViewBuilder._index_blocks`.
    """
    if not isinstance(layout, dict) or "pdf_info" not in layout:
        raise ValueError("layout root must contain 'pdf_info'")
    pages: list[dict[str, Any]] = []
    for page_idx, page in enumerate(layout.get("pdf_info") or []):
        if not isinstance(page, dict):
            continue
        blocks: list[dict[str, Any]] = []
        for block_idx, para in enumerate(page.get("para_blocks") or []):
            if not isinstance(para, dict):
                continue
            mineru_type = str(para.get("type") or "text")
            fixture_type = _TYPE_MAP.get(mineru_type, "paragraph")
            block_id = "p{:03d}-b{:03d}".format(
                int(page.get("page_idx") or page_idx),
                block_idx,
            )
            entry: dict[str, Any] = {
                "block_id": block_id,
                "type": fixture_type,
                "bbox": list(_bbox(para) or (0.0, 0.0, 0.0, 0.0)),
                "text": _collect_text(para),
            }
            if fixture_type == "heading":
                # BookView builder uses block-level labels/sizes for
                # headings; we don't have them in MinerU, so re-use
                # the text as both text and label.
                entry["text"] = entry["text"] or ""
            if fixture_type == "figure":
                url, img_bbox = _first_image_url(para)
                if url:
                    entry["image_url"] = url
                    if img_bbox is not None:
                        entry["bbox"] = list(img_bbox)
                # Some MinerU exports place a "caption" in the next
                # sibling text block; the builder does not need it
                # because the figure block itself carries the asset.
            if entry.get("text") or fixture_type == "figure":
                blocks.append(entry)
        pages.append(
            {
                "page_index": int(page.get("page_idx") or page_idx),
                "page_number": int(page.get("page_idx") or page_idx) + 1,
                "blocks": blocks,
            }
        )
    return pages


def is_mineru_layout(layout: Any) -> bool:
    """Quick schema sniff: does the layout look like MinerU's output?"""
    return isinstance(layout, dict) and isinstance(layout.get("pdf_info"), list)


def iter_images(pages: Iterable[dict[str, Any]]) -> Iterable[str]:
    """Yield every ``image_url`` in adapted pages (for asset counting)."""
    for page in pages:
        for block in page.get("blocks") or []:
            url = block.get("image_url")
            if url:
                yield str(url)


__all__ = [
    "adapt_mineru_layout",
    "is_mineru_layout",
    "iter_images",
]  