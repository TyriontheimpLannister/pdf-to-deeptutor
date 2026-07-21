"""Deterministic document-level relation recovery for localized MinerU layouts.

This module intentionally produces a sidecar.  It never mutates the raw or
localized layout, and it records weak structural signals as ``suggested`` so
later stages can preserve reviewability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..bookview.mineru_adapter import adapt_mineru_layout, is_mineru_layout
from .alignment import (
    AlignmentSummary,
    LayoutVisual,
    align_markdown_to_layout,
)

_TEXT_TYPES = frozenset({"paragraph", "text", "list", "list_item", "other"})
_VISUAL_TYPES = frozenset({"figure", "image", "table", "chart"})
_CAPTION_TYPES = frozenset({"caption", "image_caption", "table_caption"})
_TERMINAL_RE = re.compile(r"[。！？!?；;：:]$")
_LIST_START_RE = re.compile(r"^(?:[#*\-•]|\d+[.)、]|[一二三四五六七八九十]+、)")
_CHAPTER_RE = re.compile(r"^(?:第[一二三四五六七八九十百千0-9]+章|chapter\b)", re.I)
_SECTION_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千0-9]+节|\d+(?:\.\d+)+|[一二三四五六七八九十]+、)"
)


@dataclass(frozen=True)
class DocumentBlock:
    """One layout block retained in the structure sidecar."""

    block_id: str
    page_index: int | None
    page_number: int | None
    block_type: str
    text: str
    bbox: tuple[float, float, float, float] | None
    asset_id: str | None = None
    image_path: str | None = None
    heading_level: int | None = None
    source: str = "layout"
    source_line_start: int | None = None
    source_line_end: int | None = None
    anchor_block_id: str | None = None
    location_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "page_index": self.page_index,
            "page_number": self.page_number,
            "block_type": self.block_type,
            "text": self.text,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "asset_id": self.asset_id,
            "image_path": self.image_path,
            "heading_level": self.heading_level,
            "source": self.source,
            "source_line_start": self.source_line_start,
            "source_line_end": self.source_line_end,
            "anchor_block_id": self.anchor_block_id,
            "location_confidence": self.location_confidence,
        }


@dataclass(frozen=True)
class DocumentRelation:
    """A directed structural relationship with explicit provenance."""

    relation_id: str
    kind: str
    source_id: str
    target_id: str
    confidence: float
    evidence: str
    review_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "review_state": self.review_state,
        }


@dataclass(frozen=True)
class DocumentStructure:
    """Stable, serializable Stage 2.5 output."""

    blocks: tuple[DocumentBlock, ...]
    relations: tuple[DocumentRelation, ...]
    alignment: AlignmentSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "document_structure/v1",
            "blocks": [block.to_dict() for block in self.blocks],
            "relations": [relation.to_dict() for relation in self.relations],
            "alignment": self.alignment.to_dict(),
        }


def recover_document_structure(
    layout: dict[str, Any], *, markdown_text: str | None = None
) -> DocumentStructure:
    """Derive deterministic document relations from either supported layout shape."""
    pages = _pages(layout)
    layout_blocks = tuple(_ordered_blocks(pages))
    relations: list[DocumentRelation] = []
    relations.extend(_heading_relations(layout_blocks))
    caption_links = _caption_relations(layout_blocks)
    relations.extend(caption_links)
    relations.extend(_attachment_relations(layout_blocks, caption_links))
    relations.extend(_continuation_relations(layout_blocks))

    text_blocks = tuple(
        block
        for block in layout_blocks
        if block.block_type in _TEXT_TYPES | _CAPTION_TYPES | {"heading", "equation"}
        and block.text
    )
    layout_text_chars = sum(
        len(re.sub(r"\s+", "", block.text))
        for block in text_blocks
    )
    visuals = tuple(
        LayoutVisual(
            block_id=block.block_id,
            page_index=block.page_index or 0,
            page_number=block.page_number or (block.page_index or 0) + 1,
            image_path=block.image_path,
            asset_id=block.asset_id,
        )
        for block in layout_blocks
        if block.block_type in _VISUAL_TYPES and (block.image_path or block.asset_id)
    )
    alignment = align_markdown_to_layout(
        markdown_text,
        layout_text_chars=layout_text_chars,
        layout_text_blocks=len(text_blocks),
        visuals=visuals,
    )
    markdown_blocks = tuple(
        DocumentBlock(
            block_id=block.block_id,
            page_index=block.page_index,
            page_number=block.page_number,
            block_type=block.block_type,
            text=block.text,
            bbox=None,
            heading_level=block.heading_level,
            source="markdown_fallback",
            source_line_start=block.source_line_start,
            source_line_end=block.source_line_end,
            anchor_block_id=block.anchor_block_id,
            location_confidence=block.location_confidence,
        )
        for block in alignment.blocks
    )
    relations.extend(
        _relation(
            relation.kind,
            relation.source_id,
            relation.target_id,
            relation.confidence,
            relation.evidence,
            relation.review_state,
        )
        for relation in alignment.relations
    )
    return DocumentStructure(
        blocks=layout_blocks + markdown_blocks,
        relations=tuple(sorted(relations, key=lambda r: (r.kind, r.source_id, r.target_id))),
        alignment=alignment.summary,
    )


def _pages(layout: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(layout, dict):
        raise ValueError("layout root must be a mapping")
    if "pages" in layout:
        pages = layout["pages"]
        if not isinstance(pages, list):
            raise ValueError("layout.pages must be a list")
        return pages
    if is_mineru_layout(layout):
        return adapt_mineru_layout(layout)
    raise ValueError("layout missing 'pages' or 'pdf_info'")


def _ordered_blocks(pages: list[dict[str, Any]]) -> list[DocumentBlock]:
    out: list[DocumentBlock] = []
    for page_order, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_index = int(page.get("page_index", page_order))
        page_number = int(page.get("page_number", page_index + 1))
        for block_order, raw in enumerate(page.get("blocks") or []):
            if not isinstance(raw, dict):
                continue
            block_id = str(raw.get("block_id") or f"p{page_index:03d}-b{block_order:03d}")
            out.append(
                DocumentBlock(
                    block_id=block_id,
                    page_index=page_index,
                    page_number=page_number,
                    block_type=str(raw.get("type") or "other"),
                    text=str(raw.get("text") or "").strip(),
                    bbox=_bbox(raw.get("bbox")),
                    asset_id=str(raw["asset_id"]) if raw.get("asset_id") else None,
                    image_path=str(raw["image_url"]) if raw.get("image_url") else None,
                    heading_level=_positive_int(raw.get("level")),
                )
            )
    return out


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _relation(
    kind: str, source_id: str, target_id: str, confidence: float, evidence: str, review_state: str
) -> DocumentRelation:
    return DocumentRelation(
        relation_id=f"{kind}:{source_id}:{target_id}",
        kind=kind,
        source_id=source_id,
        target_id=target_id,
        confidence=confidence,
        evidence=evidence,
        review_state=review_state,
    )


def _heading_relations(blocks: tuple[DocumentBlock, ...]) -> list[DocumentRelation]:
    relations: list[DocumentRelation] = []
    stack: list[tuple[DocumentBlock, int, bool]] = []
    for block in blocks:
        if block.block_type == "heading":
            level, inferred = _heading_level(block, first_heading=not stack)
            while stack and stack[-1][1] >= level:
                stack.pop()
            if stack:
                relations.append(
                    _relation(
                        "parent_heading",
                        block.block_id,
                        stack[-1][0].block_id,
                        0.75 if inferred else 0.95,
                        "inferred_heading_level" if inferred else "layout_heading_level",
                        "suggested" if inferred else "confirmed",
                    )
                )
            stack.append((block, level, inferred))
        elif stack:
            parent, _, parent_inferred = stack[-1]
            relations.append(
                _relation(
                    "parent_heading",
                    block.block_id,
                    parent.block_id,
                    0.75 if parent_inferred else 0.95,
                    "inferred_heading_level" if parent_inferred else "layout_heading_level",
                    "suggested" if parent_inferred else "confirmed",
                )
            )
    return relations


def _heading_level(block: DocumentBlock, *, first_heading: bool) -> tuple[int, bool]:
    text = block.text.strip()
    if block.heading_level is not None:
        return block.heading_level, False
    # Common title patterns provide the conservative fallback used by this stage.
    if _CHAPTER_RE.match(text):
        return 1, True
    if _SECTION_RE.match(text):
        return 2, True
    return (1 if first_heading else 2), True


def _caption_relations(blocks: tuple[DocumentBlock, ...]) -> list[DocumentRelation]:
    by_page: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_index, []).append(block)
    relations: list[DocumentRelation] = []
    for page_blocks in by_page.values():
        for caption in page_blocks:
            if not _is_caption(caption):
                continue
            candidates = [
                candidate for candidate in page_blocks if candidate.block_type in _VISUAL_TYPES
            ]
            linked = _unique_nearest(caption, candidates)
            if linked is not None:
                relations.append(
                    _relation(
                        "caption_for",
                        caption.block_id,
                        linked.block_id,
                        0.95,
                        "same_page_caption_geometry",
                        "confirmed",
                    )
                )
    return relations


def _attachment_relations(
    blocks: tuple[DocumentBlock, ...], caption_links: list[DocumentRelation]
) -> list[DocumentRelation]:
    captions_by_visual = {relation.target_id for relation in caption_links}
    by_page: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_index, []).append(block)
    relations: list[DocumentRelation] = []
    for page_blocks in by_page.values():
        for index, visual in enumerate(page_blocks):
            if visual.block_type not in _VISUAL_TYPES:
                continue
            candidates = [
                candidate
                for candidate in page_blocks[:index]
                if candidate.block_type in _TEXT_TYPES | {"heading"} and candidate.text
            ]
            attached = _unique_nearest(visual, candidates, prefer_preceding=True)
            if attached is None:
                continue
            relations.append(
                _relation(
                    "attached_to",
                    visual.block_id,
                    attached.block_id,
                    0.8 if visual.block_id in captions_by_visual else 0.65,
                    "caption_backed_local_context"
                    if visual.block_id in captions_by_visual
                    else "nearest_preceding_local_context",
                    "suggested",
                )
            )
    return relations


def _continuation_relations(blocks: tuple[DocumentBlock, ...]) -> list[DocumentRelation]:
    by_page: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_index, []).append(block)
    relations: list[DocumentRelation] = []
    for page_index in sorted(by_page):
        next_blocks = by_page.get(page_index + 1)
        if not next_blocks:
            continue
        previous = _last_text_block(by_page[page_index])
        following = _first_text_block(next_blocks)
        if (
            previous is None
            or following is None
            or not _continuation_candidate(previous, following)
        ):
            continue
        compatible = _horizontal_overlap(previous.bbox, following.bbox) >= 0.35
        relations.append(
            _relation(
                "continues_to",
                previous.block_id,
                following.block_id,
                0.9 if compatible else 0.6,
                "adjacent_page_text_flow"
                if compatible
                else "adjacent_page_text_flow_bbox_mismatch",
                "confirmed" if compatible else "suggested",
            )
        )
    return relations


def _last_text_block(blocks: list[DocumentBlock]) -> DocumentBlock | None:
    return next(
        (block for block in reversed(blocks) if block.block_type in _TEXT_TYPES and block.text),
        None,
    )


def _first_text_block(blocks: list[DocumentBlock]) -> DocumentBlock | None:
    return next((block for block in blocks if block.block_type in _TEXT_TYPES and block.text), None)


def _continuation_candidate(previous: DocumentBlock, following: DocumentBlock) -> bool:
    return bool(
        len(previous.text) >= 10
        and not _TERMINAL_RE.search(previous.text)
        and following.text
        and not _LIST_START_RE.match(following.text)
    )


def _is_caption(block: DocumentBlock) -> bool:
    return block.block_type in _CAPTION_TYPES or bool(
        re.match(r"^(?:图|表|figure|table)\s*\d", block.text, re.I)
    )


def _unique_nearest(
    source: DocumentBlock, candidates: list[DocumentBlock], *, prefer_preceding: bool = False
) -> DocumentBlock | None:
    if not candidates:
        return None
    ranked = sorted(
        ((_distance(source, candidate, prefer_preceding), candidate) for candidate in candidates),
        key=lambda pair: (pair[0], pair[1].block_id),
    )
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.001:
        return None
    return ranked[0][1]


def _distance(source: DocumentBlock, candidate: DocumentBlock, prefer_preceding: bool) -> float:
    if source.bbox is None or candidate.bbox is None:
        return 1_000_000.0
    sx1, sy1, sx2, sy2 = source.bbox
    cx1, cy1, cx2, cy2 = candidate.bbox
    vertical = abs(((sy1 + sy2) / 2) - ((cy1 + cy2) / 2))
    horizontal = abs(((sx1 + sx2) / 2) - ((cx1 + cx2) / 2))
    penalty = 0.0
    if prefer_preceding and cy1 > sy1:
        penalty = 1_000_000.0
    return penalty + vertical + horizontal * 0.25


def _horizontal_overlap(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> float:
    if first is None or second is None:
        return 1.0
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    width = min(max(0.0, first[2] - first[0]), max(0.0, second[2] - second[0]))
    return overlap / width if width else 0.0
