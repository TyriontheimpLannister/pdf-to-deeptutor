"""Deterministic Markdown-to-layout alignment for text-poor MinerU exports."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any

_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s)]+))"
    r"(?:\s+['\"][^'\"]*['\"])?\s*\)"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(?:[-*+•]\s+|\d+[.)、]\s*|[一二三四五六七八九十]+、)")
_MARKDOWN_FORMAT_RE = re.compile(r"[`*_~>#\[\]()]")
_MIN_MARKDOWN_CHARS = 200
_COVERAGE_THRESHOLD = 0.20
_TEXT_BLOCK_SHARE_THRESHOLD = 0.10


@dataclass(frozen=True)
class LayoutVisual:
    """One layout visual available as a Markdown image anchor target."""

    block_id: str
    page_index: int
    page_number: int
    image_path: str | None
    asset_id: str | None


@dataclass(frozen=True)
class AlignedMarkdownBlock:
    """A synthetic text block with Markdown and inferred page provenance."""

    block_id: str
    block_type: str
    text: str
    heading_level: int | None
    source_line_start: int
    source_line_end: int
    page_index: int | None
    page_number: int | None
    anchor_block_id: str | None
    location_confidence: float | None
    bbox: None = None


@dataclass(frozen=True)
class AlignmentRelation:
    """A relation derived from explicit Markdown structure or image order."""

    kind: str
    source_id: str
    target_id: str
    confidence: float
    evidence: str
    review_state: str


@dataclass(frozen=True)
class AlignmentSummary:
    """Reviewable activation and coverage diagnostics."""

    status: str
    reason: str
    markdown_text_chars: int
    layout_text_chars: int
    layout_text_coverage: float
    layout_text_blocks: int
    layout_text_block_share: float
    markdown_images: int
    layout_images: int
    matched_images: int
    markdown_only_images: int
    layout_only_images: int
    synthetic_blocks: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True)
class MarkdownAlignment:
    """Complete fallback result, independent from the Stage 2.5 models."""

    blocks: tuple[AlignedMarkdownBlock, ...]
    relations: tuple[AlignmentRelation, ...]
    summary: AlignmentSummary


@dataclass(frozen=True)
class _MarkdownBlock:
    block_id: str
    block_type: str
    text: str
    heading_level: int | None
    source_line_start: int
    source_line_end: int


@dataclass(frozen=True)
class _MarkdownImage:
    target: str
    source_line: int


@dataclass(frozen=True)
class _MatchedImage:
    markdown: _MarkdownImage
    visual: LayoutVisual


def align_markdown_to_layout(
    markdown_text: str | None,
    *,
    layout_text_chars: int,
    layout_text_blocks: int,
    visuals: tuple[LayoutVisual, ...],
) -> MarkdownAlignment:
    """Return inactive diagnostics or deterministic anchored fallback data."""
    text_block_share = layout_text_blocks / max(1, layout_text_blocks + len(visuals))
    if markdown_text is None:
        return _inactive_alignment(
            status="missing_markdown",
            reason="normalized Markdown is unavailable",
            markdown_text_chars=0,
            layout_text_chars=layout_text_chars,
            layout_text_blocks=layout_text_blocks,
            markdown_images=0,
            layout_images=len(visuals),
            text_block_share=text_block_share,
        )

    parsed_blocks, images = _parse_markdown(markdown_text)
    markdown_chars = _markdown_text_chars(markdown_text)
    coverage = min(1.0, layout_text_chars / markdown_chars) if markdown_chars else 1.0
    if markdown_chars < _MIN_MARKDOWN_CHARS:
        return _inactive_alignment(
            status="not_needed",
            reason="Markdown text is below the fallback minimum",
            markdown_text_chars=markdown_chars,
            layout_text_chars=layout_text_chars,
            layout_text_blocks=layout_text_blocks,
            markdown_images=len(images),
            layout_images=len(visuals),
            coverage=coverage,
            text_block_share=text_block_share,
        )
    if coverage >= _COVERAGE_THRESHOLD:
        return _inactive_alignment(
            status="not_needed",
            reason="layout text coverage is sufficient",
            markdown_text_chars=markdown_chars,
            layout_text_chars=layout_text_chars,
            layout_text_blocks=layout_text_blocks,
            markdown_images=len(images),
            layout_images=len(visuals),
            coverage=coverage,
            text_block_share=text_block_share,
        )
    if text_block_share >= _TEXT_BLOCK_SHARE_THRESHOLD:
        return _inactive_alignment(
            status="not_needed",
            reason="layout contains enough structured text blocks",
            markdown_text_chars=markdown_chars,
            layout_text_chars=layout_text_chars,
            layout_text_blocks=layout_text_blocks,
            markdown_images=len(images),
            layout_images=len(visuals),
            coverage=coverage,
            text_block_share=text_block_share,
        )

    matches = _match_images(images, visuals)
    if not matches:
        warnings = _warnings(len(images), len(visuals))
        return MarkdownAlignment(
            blocks=(),
            relations=(),
            summary=AlignmentSummary(
                status="unavailable_no_image_matches",
                reason="fallback required but no Markdown image matched layout",
                markdown_text_chars=markdown_chars,
                layout_text_chars=layout_text_chars,
                layout_text_coverage=coverage,
                layout_text_blocks=layout_text_blocks,
                layout_text_block_share=text_block_share,
                markdown_images=len(images),
                layout_images=len(visuals),
                matched_images=0,
                markdown_only_images=len(images),
                layout_only_images=len(visuals),
                synthetic_blocks=0,
                warnings=warnings,
            ),
        )

    aligned_blocks = _locate_blocks(parsed_blocks, matches)
    relations = _derive_relations(parsed_blocks, matches)
    markdown_only = len(images) - len(matches)
    layout_only = len(visuals) - len(matches)
    return MarkdownAlignment(
        blocks=aligned_blocks,
        relations=relations,
        summary=AlignmentSummary(
            status="active",
            reason="layout text coverage and text-block share require fallback",
            markdown_text_chars=markdown_chars,
            layout_text_chars=layout_text_chars,
            layout_text_coverage=coverage,
            layout_text_blocks=layout_text_blocks,
            layout_text_block_share=text_block_share,
            markdown_images=len(images),
            layout_images=len(visuals),
            matched_images=len(matches),
            markdown_only_images=markdown_only,
            layout_only_images=layout_only,
            synthetic_blocks=len(aligned_blocks),
            warnings=_warnings(markdown_only, layout_only),
        ),
    )


def _inactive_alignment(
    *,
    status: str,
    reason: str,
    markdown_text_chars: int,
    layout_text_chars: int,
    layout_text_blocks: int,
    markdown_images: int,
    layout_images: int,
    coverage: float = 0.0,
    text_block_share: float = 0.0,
) -> MarkdownAlignment:
    return MarkdownAlignment(
        blocks=(),
        relations=(),
        summary=AlignmentSummary(
            status=status,
            reason=reason,
            markdown_text_chars=markdown_text_chars,
            layout_text_chars=layout_text_chars,
            layout_text_coverage=coverage,
            layout_text_blocks=layout_text_blocks,
            layout_text_block_share=text_block_share,
            markdown_images=markdown_images,
            layout_images=layout_images,
            matched_images=0,
            markdown_only_images=markdown_images,
            layout_only_images=layout_images,
            synthetic_blocks=0,
            warnings=(),
        ),
    )


def _parse_markdown(
    markdown_text: str,
) -> tuple[tuple[_MarkdownBlock, ...], tuple[_MarkdownImage, ...]]:
    blocks: list[_MarkdownBlock] = []
    images: list[_MarkdownImage] = []
    buffered: list[tuple[int, str]] = []

    def flush() -> None:
        if not buffered:
            return
        text = "\n".join(part for _, part in buffered).strip()
        if text:
            blocks.append(
                _MarkdownBlock(
                    block_id=f"md-b{len(blocks) + 1:06d}",
                    block_type="list" if _LIST_RE.match(text) else "paragraph",
                    text=text,
                    heading_level=None,
                    source_line_start=buffered[0][0],
                    source_line_end=buffered[-1][0],
                )
            )
        buffered.clear()

    for line_number, raw_line in enumerate(markdown_text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            blocks.append(
                _MarkdownBlock(
                    block_id=f"md-b{len(blocks) + 1:06d}",
                    block_type="heading",
                    text=heading.group(2).strip(),
                    heading_level=len(heading.group(1)),
                    source_line_start=line_number,
                    source_line_end=line_number,
                )
            )
            continue
        for marker in _IMAGE_RE.finditer(line):
            target = marker.group("angle") or marker.group("plain") or ""
            images.append(_MarkdownImage(target=target, source_line=line_number))
        cleaned = _IMAGE_RE.sub("", line).strip()
        if cleaned:
            buffered.append((line_number, cleaned))
    flush()
    return tuple(blocks), tuple(images)


def _markdown_text_chars(markdown_text: str) -> int:
    text = _IMAGE_RE.sub("", markdown_text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = _MARKDOWN_FORMAT_RE.sub("", text)
    return len(re.sub(r"\s+", "", text))


def _normalize_image_target(value: str) -> str:
    value = value.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    return value.replace("\\", "/").lstrip("./")


def _image_identity(path: str | None, asset_id: str | None) -> tuple[str, str]:
    normalized = _normalize_image_target(path or "")
    basename = normalized.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return normalized, stem or (asset_id or "")


def _match_images(
    images: tuple[_MarkdownImage, ...], visuals: tuple[LayoutVisual, ...]
) -> tuple[_MatchedImage, ...]:
    exact: dict[str, deque[int]] = defaultdict(deque)
    stems: dict[str, deque[int]] = defaultdict(deque)
    for index, visual in enumerate(visuals):
        path, stem = _image_identity(visual.image_path, visual.asset_id)
        if path:
            exact[path].append(index)
        if stem:
            stems[stem].append(index)

    used: set[int] = set()
    matches: list[_MatchedImage] = []
    for image in images:
        path, stem = _image_identity(image.target, None)
        index = _pop_unused(exact.get(path), used) if path else None
        if index is None and stem:
            index = _pop_unused(stems.get(stem), used)
        if index is None:
            continue
        used.add(index)
        matches.append(_MatchedImage(markdown=image, visual=visuals[index]))
    return tuple(matches)


def _pop_unused(queue: deque[int] | None, used: set[int]) -> int | None:
    if queue is None:
        return None
    while queue and queue[0] in used:
        queue.popleft()
    return queue.popleft() if queue else None


def _locate_blocks(
    blocks: tuple[_MarkdownBlock, ...], matches: tuple[_MatchedImage, ...]
) -> tuple[AlignedMarkdownBlock, ...]:
    out: list[AlignedMarkdownBlock] = []
    for block in blocks:
        match = min(matches, key=lambda item: _anchor_distance_key(block, item))
        distance = _line_distance(block, match.markdown.source_line)
        confidence = 0.90 if distance <= 2 else 0.65
        out.append(
            AlignedMarkdownBlock(
                block_id=block.block_id,
                block_type=block.block_type,
                text=block.text,
                heading_level=block.heading_level,
                source_line_start=block.source_line_start,
                source_line_end=block.source_line_end,
                page_index=match.visual.page_index,
                page_number=match.visual.page_number,
                anchor_block_id=match.visual.block_id,
                location_confidence=confidence,
            )
        )
    return tuple(out)


def _line_distance(block: _MarkdownBlock, anchor_line: int) -> int:
    if block.source_line_start <= anchor_line <= block.source_line_end:
        return 0
    if anchor_line > block.source_line_end:
        return anchor_line - block.source_line_end
    return block.source_line_start - anchor_line


def _anchor_distance_key(
    block: _MarkdownBlock, match: _MatchedImage
) -> tuple[int, int, int, str]:
    line = match.markdown.source_line
    following = line >= block.source_line_end
    return (
        _line_distance(block, line),
        0 if following else 1,
        line,
        match.visual.block_id,
    )


def _derive_relations(
    blocks: tuple[_MarkdownBlock, ...], matches: tuple[_MatchedImage, ...]
) -> tuple[AlignmentRelation, ...]:
    relations: list[AlignmentRelation] = []
    heading_stack: list[_MarkdownBlock] = []
    for block in blocks:
        if block.block_type == "heading":
            level = block.heading_level or 2
            while heading_stack and (heading_stack[-1].heading_level or 2) >= level:
                heading_stack.pop()
            if heading_stack:
                relations.append(
                    AlignmentRelation(
                        kind="parent_heading",
                        source_id=block.block_id,
                        target_id=heading_stack[-1].block_id,
                        confidence=1.0,
                        evidence="markdown_heading_level",
                        review_state="confirmed",
                    )
                )
            heading_stack.append(block)
        elif heading_stack:
            relations.append(
                AlignmentRelation(
                    kind="parent_heading",
                    source_id=block.block_id,
                    target_id=heading_stack[-1].block_id,
                    confidence=1.0,
                    evidence="markdown_heading_level",
                    review_state="confirmed",
                )
            )

    for match in matches:
        anchor_line = match.markdown.source_line
        active_heading_line = max(
            (
                block.source_line_start
                for block in blocks
                if block.block_type == "heading" and block.source_line_start <= anchor_line
            ),
            default=0,
        )
        candidates = [
            block
            for block in blocks
            if block.block_type != "heading"
            and active_heading_line < block.source_line_start <= anchor_line
        ]
        if candidates:
            target = max(candidates, key=lambda block: (block.source_line_end, block.block_id))
        else:
            headings = [
                block
                for block in blocks
                if block.block_type == "heading" and block.source_line_start <= anchor_line
            ]
            if not headings:
                continue
            target = max(headings, key=lambda block: (block.source_line_start, block.block_id))
        relations.append(
            AlignmentRelation(
                kind="attached_to",
                source_id=match.visual.block_id,
                target_id=target.block_id,
                confidence=0.85,
                evidence="markdown_image_anchor",
                review_state="suggested",
            )
        )
    return tuple(
        sorted(relations, key=lambda item: (item.kind, item.source_id, item.target_id))
    )


def _warnings(markdown_only: int, layout_only: int) -> tuple[str, ...]:
    warnings: list[str] = []
    if markdown_only:
        warnings.append(f"unmatched_markdown_images:{markdown_only}")
    if layout_only:
        warnings.append(f"unmatched_layout_images:{layout_only}")
    return tuple(warnings)


__all__ = [
    "AlignedMarkdownBlock",
    "AlignmentRelation",
    "AlignmentSummary",
    "LayoutVisual",
    "MarkdownAlignment",
    "align_markdown_to_layout",
]
