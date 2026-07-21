"""BookView builder — Stage 3.

Inputs (already produced by Stages 1-2 and 4b):
* ``normalized/full.md`` — markdown the matcher read.
* ``normalized/layout.localized.json`` — page-level blocks with
  bbox, type, image_url, caption, labels, text.
* ``normalized/assets_registry.json`` — url → asset_id, local_path,
  byte_size, dimensions.
* ``topic_assignments/assignments.json`` — item_id → topic_ids.
  Optional: when missing, BookView still builds but every item gets
  an empty ``topic_ids`` list and ``assignment_review_state`` is
  ``"unassigned"``.

Output:
* ``book_view/book_view.json`` — structured tree:
  BookView → chapters[] → sections[] → items[]
  Each item carries: source_block_refs, asset_refs, topic_ids,
  page_refs, bbox_union, review_state, item_type.

Determinism:
* Iterating layout blocks in ``block_id`` order produces a stable
  mapping to items by chapter_path + text fingerprint. Item-block
  binding uses ``chapter_path`` containment and an ordered best-fit
  scan — no LLM calls, no random IDs.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..outlining.items import Item, extract_items
from ..project import ProjectWorkspace, StageStatus, record_stage


class BookViewBuildError(ValueError):
    """Raised when BookView inputs are malformed or self-inconsistent."""


# ---------------------------------------------------------------------- #
# Dataclasses
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class AssetRef:
    asset_id: str
    local_path: str
    sha256: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    source_url: str | None = None
    source_page: int | None = None
    caption: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "source_url": self.source_url,
            "source_page": self.source_page,
            "caption": self.caption,
        }


@dataclass(frozen=True)
class SourceBlockRef:
    block_id: str
    page_index: int
    page_number: int
    bbox: tuple[float, float, float, float]
    block_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "page_index": self.page_index,
            "page_number": self.page_number,
            "bbox": list(self.bbox),
            "block_type": self.block_type,
        }


@dataclass
class BookItem:
    """One Item enriched with source/page/asset/topic references."""

    item_id: str
    item_type: str
    title: str
    text: str
    chapter_path: tuple[str, ...]
    source_block_refs: list[SourceBlockRef] = field(default_factory=list)
    asset_refs: list[AssetRef] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    bbox_union: tuple[float, float, float, float] | None = None
    topic_ids: list[str] = field(default_factory=list)
    topic_match_scores: dict[str, int] = field(default_factory=dict)
    assignment_review_state: str = "unreviewed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "text": self.text,
            "chapter_path": list(self.chapter_path),
            "source_block_refs": [r.to_dict() for r in self.source_block_refs],
            "asset_refs": [a.to_dict() for a in self.asset_refs],
            "page_refs": list(self.page_refs),
            "bbox_union": list(self.bbox_union) if self.bbox_union is not None else None,
            "topic_ids": list(self.topic_ids),
            "topic_match_scores": dict(self.topic_match_scores),
            "assignment_review_state": self.assignment_review_state,
        }


@dataclass
class Section:
    title: str
    items: list[BookItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "items": [i.to_dict() for i in self.items]}


@dataclass
class Chapter:
    title: str
    sections: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class BookView:
    book_id: str
    generated_at: str
    normalized_fingerprint: str
    layout_fingerprint: str
    assets_fingerprint: str
    assignments_fingerprint: str
    chapters: list[Chapter]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "book_view/v1",
            "book_id": self.book_id,
            "generated_at": self.generated_at,
            "fingerprints": {
                "normalized": self.normalized_fingerprint,
                "layout": self.layout_fingerprint,
                "assets": self.assets_fingerprint,
                "assignments": self.assignments_fingerprint,
            },
            "chapters": [c.to_dict() for c in self.chapters],
        }


# ---------------------------------------------------------------------- #
# Loaders
# ---------------------------------------------------------------------- #


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_layout(path: Path) -> tuple[list[dict[str, Any]], str]:
    from .mineru_adapter import adapt_mineru_layout, is_mineru_layout

    data = _load_json(path)
    if not isinstance(data, dict):
        raise BookViewBuildError(f"{path}: layout root must be a mapping")
    fp = hashlib.sha256(path.read_bytes()).hexdigest()
    # Fixture layout: top-level "pages" key (list of page dicts).
    if "pages" in data:
        pages = data["pages"]
        if not isinstance(pages, list):
            raise BookViewBuildError(f"{path}: layout.pages must be a list")
        return pages, fp
    # MinerU real layout: top-level "pdf_info" key. Adapt it
    # transparently into the fixture schema.
    if is_mineru_layout(data):
        return adapt_mineru_layout(data), fp
    raise BookViewBuildError(
        f"{path}: layout missing 'pages' or 'pdf_info' (unknown schema)"
    )


def _load_assets_registry(path: Path) -> tuple[dict[str, AssetRef], str]:
    """Return (asset_id → AssetRef) and the file fingerprint.

    Two indexes are populated because Stage 2 may rewrite the URL
    (``by_url``) but always preserves the asset_id. The caller chooses
    which to look up against.
    """
    data = _load_json(path)
    if not isinstance(data, dict) or "by_url" not in data:
        raise BookViewBuildError(f"{path}: assets_registry missing 'by_url'")
    by_url_raw = data["by_url"]
    if not isinstance(by_url_raw, dict):
        raise BookViewBuildError(f"{path}: assets_registry.by_url must be a mapping")
    fp = hashlib.sha256(path.read_bytes()).hexdigest()
    by_id: dict[str, AssetRef] = {}
    for url, asset_id in by_url_raw.items():
        meta = next(
            (a for a in (data.get("assets") or []) if a.get("asset_id") == asset_id),
            {},
        )
        if not isinstance(meta, dict):
            meta = {}
        ref = AssetRef(
            asset_id=str(asset_id),
            local_path=str(meta.get("local_path") or ""),
            sha256=str(meta.get("sha256") or ""),
            mime_type=str(meta.get("mime_type") or "image/png"),
            width=meta.get("width"),
            height=meta.get("height"),
            source_url=str(url),
            source_page=meta.get("source_page"),
        )
        by_id[str(asset_id)] = ref
    return by_id, fp


def _load_assignments(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """Return (item_id → assignment dict) and the file fingerprint."""
    data = _load_json(path)
    fp = hashlib.sha256(path.read_bytes()).hexdigest()
    if not isinstance(data, dict):
        return {}, fp
    out: dict[str, dict[str, Any]] = {}
    for a in data.get("assignments") or []:
        if not isinstance(a, dict):
            continue
        item_id = a.get("item_id")
        if isinstance(item_id, str):
            out[item_id] = a
    return out, fp


def _topic_match_scores(assignment: dict[str, Any], topic_ids: list[str]) -> dict[str, int]:
    """Return valid Stage 4b scores for the assigned topics only."""
    allowed_topic_ids = set(topic_ids)
    scores: dict[str, int] = {}
    for detail in assignment.get("match_details") or []:
        if not isinstance(detail, dict):
            continue
        topic_id = detail.get("topic_id")
        score = detail.get("score")
        if (
            isinstance(topic_id, str)
            and topic_id in allowed_topic_ids
            and isinstance(score, int)
            and not isinstance(score, bool)
        ):
            scores[topic_id] = score
    return scores


@dataclass(frozen=True)
class _StructureContext:
    attachments_by_target: dict[str, tuple[str, ...]]
    synthetic_blocks: tuple[dict[str, Any], ...]


def _load_structure_context(path: Path) -> _StructureContext:
    """Load optional Stage 2.5 attachments and anchored fallback blocks.

    Workspaces created before Stage 2.5 remain valid when the sidecar is
    absent. A present sidecar is validated so corrupted relation data cannot
    silently alter figure ownership.
    """
    if not path.is_file():
        return _StructureContext({}, ())
    data = _load_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != "document_structure/v1":
        raise BookViewBuildError(f"{path}: invalid document structure sidecar")
    relations = data.get("relations")
    if not isinstance(relations, list):
        raise BookViewBuildError(f"{path}: relations must be a list")
    attachments: dict[str, list[str]] = {}
    for relation in relations:
        if not isinstance(relation, dict) or relation.get("kind") != "attached_to":
            continue
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            raise BookViewBuildError(f"{path}: attached_to relation has invalid block IDs")
        attachments.setdefault(target_id, []).append(source_id)
    normalized_attachments = {
        target: tuple(sorted(set(sources))) for target, sources in attachments.items()
    }

    synthetic_blocks: list[dict[str, Any]] = []
    alignment = data.get("alignment")
    if isinstance(alignment, dict) and alignment.get("status") == "active":
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            raise BookViewBuildError(f"{path}: blocks must be a list")
        for block in blocks:
            if not isinstance(block, dict) or block.get("source") != "markdown_fallback":
                continue
            if not isinstance(block.get("block_id"), str):
                raise BookViewBuildError(f"{path}: fallback block has invalid block_id")
            if not isinstance(block.get("anchor_block_id"), str):
                continue
            synthetic_blocks.append(block)
    synthetic_blocks.sort(
        key=lambda block: (int(block.get("source_line_start") or 0), str(block["block_id"]))
    )
    return _StructureContext(normalized_attachments, tuple(synthetic_blocks))


# ---------------------------------------------------------------------- #
# Builder
# ---------------------------------------------------------------------- #


def _bbox_from_block(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = block.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _bbox_union(
    boxes: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    items = list(boxes)
    if not items:
        return None
    xs = [b[0] for b in items] + [b[2] for b in items]
    ys = [b[1] for b in items] + [b[3] for b in items]
    return (min(xs), min(ys), max(xs), max(ys))


def _strip_markdown_image_alt(text: str) -> str:
    """Collapse ``![alt](url)`` → ``alt`` for matching purposes."""

    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)


def _normalize_text(text: str) -> str:
    """Lowercase, strip whitespace and markdown noise for fingerprint matching."""
    text = _strip_markdown_image_alt(text or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _normalize_structure_text(text: str) -> str:
    """Normalize Markdown source text for exact sidecar-to-item matching."""
    return re.sub(r"[*_~]+", "", _normalize_text(text))


def _tokens(text: str) -> set[str]:
    """Crude token splitter for Chinese + Latin text."""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", _normalize_text(text))
    return {tok for tok in cleaned.split() if len(tok) >= 2}


def _first_sentence(text: str, limit: int = 40) -> str:
    """Return the first non-trivial sentence / chunk for matching."""
    flat = _strip_markdown_image_alt(text or "").strip()
    if not flat:
        return ""
    # Split on sentence terminators; pick first.
    head = re.split(r"[。！？!?\.]", flat, maxsplit=1)[0].strip()
    return head[:limit]


class BookViewBuilder:
    """Build a :class:`BookView` from the project workspace."""

    def __init__(self, workspace: ProjectWorkspace) -> None:
        self._workspace = workspace
        self._book_id = workspace.root.name

    def build(
        self,
        *,
        markdown_path: Path | str | None = None,
        layout_path: Path | str | None = None,
        assets_path: Path | str | None = None,
        assignments_path: Path | str | None = None,
    ) -> BookView:
        ws = self._workspace
        md_path = Path(markdown_path) if markdown_path else (ws.normalized_dir / "full.md")
        layout_p = (
            Path(layout_path) if layout_path else (ws.normalized_dir / "layout.localized.json")
        )
        assets_p = (
            Path(assets_path) if assets_path else (ws.normalized_dir / "assets_registry.json")
        )
        # ``topic_assignments/assignments.json`` lives next to book_view.
        assignments_p = (
            Path(assignments_path)
            if assignments_path
            else (ws.topic_assignments_dir / "assignments.json")
        )

        for required, label in [
            (md_path, "normalized/full.md"),
            (layout_p, "normalized/layout.localized.json"),
            (assets_p, "normalized/assets_registry.json"),
        ]:
            if not required.is_file():
                raise BookViewBuildError(f"missing required input: {label}")

        # 1. Load normalized markdown and split into items.
        normalized_fp = hashlib.sha256(md_path.read_bytes()).hexdigest()
        items = extract_items(md_path.read_text(encoding="utf-8"))

        # 2. Load layout + assets + assignments.
        pages, layout_fp = _load_layout(layout_p)
        assets_by_id, assets_fp = _load_assets_registry(assets_p)
        assignments: dict[str, dict[str, Any]] = {}
        assignments_fp = ""
        if assignments_p.is_file():
            assignments, assignments_fp = _load_assignments(assignments_p)
        else:
            # Use a sentinel so the fingerprint field is still populated.
            assignments_fp = hashlib.sha256(b"").hexdigest()

        # 3. Bind layout blocks to items.
        block_index = self._index_blocks(pages, assets_by_id)
        self._index_synthetic_blocks(block_index, self._structure_synthetic_blocks)

        # 4. Walk items in chapter / section / item order and place
        #    them under their parent chapter/section.
        chapters = self._tree_from_items(items, block_index, assignments)

        return BookView(
            book_id=self._book_id,
            generated_at=_now(),
            normalized_fingerprint=normalized_fp,
            layout_fingerprint=layout_fp,
            assets_fingerprint=assets_fp,
            assignments_fingerprint=assignments_fp,
            chapters=chapters,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _index_blocks(
        self,
        pages: list[dict[str, Any]],
        assets_by_url: dict[str, AssetRef],
    ) -> dict[str, dict[str, Any]]:
        """Build a flat block index keyed by ``block_id``.

        Each entry contains:
        * ``ref`` (:class:`SourceBlockRef`);
        * ``chapter_path`` derived from the most-recent level-1 /
          level-2 heading block seen while scanning pages in order;
        * ``image_urls`` (list of urls from the block, if any);
        * ``asset_id`` (Stage 2's localized asset id, if any);
        * ``text_signature`` (a normalized chunk for matching items);
        * ``tokens`` (set of tokens for fuzzy overlap scoring).
        """
        index: dict[str, dict[str, Any]] = {}
        chapter_path: list[str] = []
        section_path: list[str] = []

        for page in pages:
            page_index = int(page.get("page_index") or 0)
            page_number = int(page.get("page_number") or page_index + 1)
            for block in page.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                block_id = block.get("block_id")
                if not isinstance(block_id, str):
                    continue
                block_type = str(block.get("type") or "other")
                bbox = _bbox_from_block(block)

                # Track chapter / section title from headings.
                if block_type == "heading":
                    level = int(block.get("level") or 0)
                    title = str(block.get("text") or "").strip()
                    if level == 1:
                        chapter_path = [title]
                        section_path = []
                    elif level == 2:
                        section_path = [title]

                text = block.get("text") or ""
                image_urls: list[str] = []
                if block.get("image_url"):
                    image_urls.append(str(block["image_url"]))
                asset_id = block.get("asset_id")

                index[block_id] = {
                    "ref": SourceBlockRef(
                        block_id=block_id,
                        page_index=page_index,
                        page_number=page_number,
                        bbox=bbox or (0.0, 0.0, 0.0, 0.0),
                        block_type=block_type,
                    ),
                    "chapter_path": tuple(chapter_path + section_path),
                    "text": text,
                    "text_signature": _first_sentence(text, limit=40),
                    "tokens": _tokens(text),
                    "image_urls": image_urls,
                    "asset_id": str(asset_id) if asset_id else None,
                    "caption": block.get("caption"),
                    "source": "layout",
                    "source_line_start": None,
                }
        return index

    def _index_synthetic_blocks(
        self,
        index: dict[str, dict[str, Any]],
        blocks: tuple[dict[str, Any], ...],
    ) -> None:
        """Add anchored Markdown text blocks without replacing layout blocks."""
        chapter_path: list[str] = []
        section_path: list[str] = []
        for block in blocks:
            block_id = str(block["block_id"])
            block_type = str(block.get("block_type") or "paragraph")
            text = str(block.get("text") or "")
            if block_type == "heading":
                level = int(block.get("heading_level") or 0)
                if level == 1:
                    chapter_path = [text]
                    section_path = []
                elif level == 2:
                    section_path = [text]
            page_index = block.get("page_index")
            page_number = block.get("page_number")
            if not isinstance(page_index, int) or not isinstance(page_number, int):
                continue
            index[block_id] = {
                "ref": SourceBlockRef(
                    block_id=block_id,
                    page_index=page_index,
                    page_number=page_number,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    block_type=block_type,
                ),
                "chapter_path": tuple(chapter_path + section_path),
                "text": text,
                "text_signature": _first_sentence(text, limit=40),
                "tokens": _tokens(text),
                "image_urls": [],
                "asset_id": None,
                "caption": None,
                "source": "markdown_fallback",
                "source_line_start": int(block.get("source_line_start") or 0),
            }

    def _matching_synthetic_blocks(
        self,
        item: Item,
        block_index: dict[str, dict[str, Any]],
        used_block_ids: set[str],
        target_chapter: str,
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Return every unused fallback block contained in one Markdown item."""
        item_text = _normalize_structure_text(item.text)
        item_tokens = _tokens(item.text)
        ranked: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
        for block_id, entry in block_index.items():
            if entry.get("source") != "markdown_fallback" or block_id in used_block_ids:
                continue
            block_chapter = entry["chapter_path"][0] if entry["chapter_path"] else ""
            if block_chapter and block_chapter != target_chapter:
                continue
            candidate_text = _normalize_structure_text(str(entry.get("text") or ""))
            if not candidate_text or not item_text:
                continue
            if candidate_text not in item_text and item_text not in candidate_text:
                continue
            overlap = len(item_tokens & entry["tokens"])
            source_line = int(entry.get("source_line_start") or 0)
            score = (min(len(candidate_text), len(item_text)), overlap, -source_line)
            ranked.append((score, block_id, entry))
        ranked.sort(key=lambda candidate: candidate[0], reverse=True)
        return tuple((block_id, entry) for _, block_id, entry in ranked)

    def _tree_from_items(
        self,
        items: list[Item],
        block_index: dict[str, dict[str, Any]],
        assignments: dict[str, dict[str, Any]],
    ) -> list[Chapter]:
        """Walk items in document order and build chapter / section trees."""
        # Track block usage so each block is bound to at most one item
        # (its strongest match). Items greedily consume the next
        # ``figure`` block encountered while scanning blocks in order,
        # which mirrors how the markdown and layout line up.
        chapter_map: dict[str, Chapter] = {}
        section_map: dict[tuple[str, str], Section] = {}
        used_block_ids: set[str] = set()

        # Build a block-iteration order list so we can move forward
        # through layout as items are emitted.

        # Cursor for figure association: each item may attach the next
        # non-text block whose chapter_path matches the item's.
        cursor = 0
        block_order = _block_order_with_chapter(block_index)

        for item in items:
            target_chapter_title = item.chapter_path[0] if item.chapter_path else ""
            target_section_title = (
                item.chapter_path[1] if len(item.chapter_path) > 1 else ""
            )

            chapter = chapter_map.setdefault(
                target_chapter_title, Chapter(title=target_chapter_title)
            )
            if target_section_title:
                section_key = (target_chapter_title, target_section_title)
                section = section_map.get(section_key)
                if section is None:
                    section = Section(title=target_section_title)
                    chapter.sections.append(section)
                    section_map[section_key] = section
            else:
                # Items directly under a chapter (no section) share a
                # synthetic "<root>" section to keep the JSON shape
                # uniform.
                section_key = (target_chapter_title, "<root>")
                section = section_map.get(section_key)
                if section is None:
                    section = Section(title="<root>")
                    chapter.sections.append(section)
                    section_map[section_key] = section

            book_item, cursor = self._make_book_item(
                item,
                block_index,
                block_order,
                used_block_ids,
                assignments,
                cursor,
                target_chapter_title,
            )
            section.items.append(book_item)

        return list(chapter_map.values())

    def _make_book_item(
        self,
        item: Item,
        block_index: dict[str, dict[str, Any]],
        block_order: list[tuple[str, str]],
        used_block_ids: set[str],
        assignments: dict[str, dict[str, Any]],
        cursor: int,
        target_chapter: str,
    ) -> tuple[BookItem, int]:
        """Find layout blocks that belong to this item.

        Strategy: scan ``block_order`` from ``cursor`` onward. A block
        belongs to the item when its chapter_path matches the item's
        chapter and either the block type matches the item's expected
        types or there is token / head overlap with the item's text.
        Once one block is bound, additional ``figure`` blocks in the
        same chapter are attached as image references.

        Returns the :class:`BookItem` and the new cursor position.
        """
        expected_types = _EXPECTED_BLOCK_TYPES_FOR_ITEM.get(
            item.item_type, {"paragraph", "heading", "other"}
        )
        item_tokens = _tokens(item.text)
        item_head = _first_sentence(item.text)

        source_blocks: list[SourceBlockRef] = []
        asset_refs: list[AssetRef] = []
        page_refs: list[int] = []
        bbox_boxes: list[tuple[float, float, float, float]] = []

        n = len(block_order)
        max_blocks = 6  # cap on blocks per item

        # Pass 1 — prefer an exact synthetic Markdown source when fallback is active.
        synthetic_matches = self._matching_synthetic_blocks(
            item, block_index, used_block_ids, target_chapter
        )
        synthetic_primary = synthetic_matches[0] if synthetic_matches else None
        primary_from_synthetic = bool(synthetic_matches)
        primary_block_id: str | None = None
        primary_entry: dict[str, Any] | None = None
        if synthetic_primary is not None:
            primary_block_id, primary_entry = synthetic_primary
        else:
            scan_cursor = cursor
            while scan_cursor < n:
                block_id, _ = block_order[scan_cursor]
                entry = block_index.get(block_id)
                if entry is None or block_id in used_block_ids:
                    scan_cursor += 1
                    continue
                block_chapter = entry["chapter_path"][0] if entry["chapter_path"] else ""
                if block_chapter and block_chapter != target_chapter:
                    scan_cursor += 1
                    continue
                block_type = entry["ref"].block_type
                if (
                    block_type == "figure"
                    and block_id in self._explicit_attachment_sources
                ):
                    # A Stage 2.5 relation already gives this visual an
                    # owner. Keep the legacy cursor from binding it to an
                    # earlier unmatched item before that target is visited.
                    scan_cursor += 1
                    continue
                type_match = (
                    block_type in expected_types
                    or block_type in {"heading", "paragraph", "other"}
                )
                token_overlap = bool(item_tokens & entry["tokens"])
                head_match = bool(item_head) and item_head[:10] in entry["text"]
                figure_match = block_type == "figure" and bool(
                    entry.get("image_urls") or []
                )
                if type_match or token_overlap or head_match or figure_match:
                    primary_block_id = block_id
                    primary_entry = entry
                    cursor = scan_cursor + 1
                    break
                scan_cursor += 1

        if primary_block_id is None or primary_entry is None:
            return self._finalize_book_item(
                item, source_blocks, asset_refs, page_refs, bbox_boxes, assignments
            ), cursor

        source_blocks.append(primary_entry["ref"])
        if primary_from_synthetic:
            used_block_ids.update(block_id for block_id, _ in synthetic_matches)
            attachment_target_ids = [
                block_id
                for block_id, _ in sorted(
                    synthetic_matches,
                    key=lambda match: int(match[1].get("source_line_start") or 0),
                )
            ]
        else:
            used_block_ids.add(primary_block_id)
            attachment_target_ids = [primary_block_id]
        page_refs.append(primary_entry["ref"].page_number)
        if primary_entry["ref"].bbox != (0.0, 0.0, 0.0, 0.0):
            bbox_boxes.append(primary_entry["ref"].bbox)
        asset_refs.extend(
            self._asset_refs_for_block(primary_entry, self._assets_by_id)
        )

        # Prefer explicit Stage 2.5 attachments over the legacy "next figure"
        # heuristic. They can point past an intervening heading or caption.
        for target_id in attachment_target_ids:
            for figure_id in self._attachments_by_target.get(target_id, ()):
                if (
                    not primary_from_synthetic and len(source_blocks) >= max_blocks
                ) or figure_id in used_block_ids:
                    continue
                figure_entry = block_index.get(figure_id)
                if figure_entry is None or figure_entry["ref"].block_type != "figure":
                    continue
                source_blocks.append(figure_entry["ref"])
                used_block_ids.add(figure_id)
                page_refs.append(figure_entry["ref"].page_number)
                if figure_entry["ref"].bbox != (0.0, 0.0, 0.0, 0.0):
                    bbox_boxes.append(figure_entry["ref"].bbox)
                asset_refs.extend(
                    self._asset_refs_for_block(figure_entry, self._assets_by_id)
                )

        # Pass 2 — consume any figure blocks that immediately follow
        # the primary block in the same chapter.
        while not primary_from_synthetic and cursor < n and len(source_blocks) < max_blocks:
            block_id, _ = block_order[cursor]
            entry = block_index.get(block_id)
            if entry is None or block_id in used_block_ids:
                cursor += 1
                continue
            block_chapter = entry["chapter_path"][0] if entry["chapter_path"] else ""
            if block_chapter and block_chapter != target_chapter:
                break
            if entry["ref"].block_type != "figure":
                break
            if block_id in self._explicit_attachment_sources:
                # Do not cross an explicitly-owned visual. Its target item
                # will consume it through ``attachments_by_target``.
                break
            image_urls = entry.get("image_urls") or []
            if not image_urls:
                break
            source_blocks.append(entry["ref"])
            used_block_ids.add(block_id)
            page_refs.append(entry["ref"].page_number)
            if entry["ref"].bbox != (0.0, 0.0, 0.0, 0.0):
                bbox_boxes.append(entry["ref"].bbox)
            asset_refs.extend(self._asset_refs_for_block(entry, self._assets_by_id))
            cursor += 1

        return self._finalize_book_item(
            item, source_blocks, asset_refs, page_refs, bbox_boxes, assignments
        ), cursor

    def _finalize_book_item(
        self,
        item: Item,
        source_blocks: list[SourceBlockRef],
        asset_refs: list[AssetRef],
        page_refs: list[int],
        bbox_boxes: list[tuple[float, float, float, float]],
        assignments: dict[str, dict[str, Any]],
    ) -> BookItem:
        """Wrap collected refs into a :class:`BookItem` and attach topic_ids."""
        a = assignments.get(item.item_id) or {}
        topic_ids = list(a.get("topic_ids") or [])
        review_state = str(a.get("review_state") or "unassigned")
        if not topic_ids:
            review_state = "unassigned"
        return BookItem(
            item_id=item.item_id,
            item_type=item.item_type,
            title=item.title,
            text=item.text,
            chapter_path=item.chapter_path,
            source_block_refs=source_blocks,
            asset_refs=asset_refs,
            page_refs=sorted(set(page_refs)),
            bbox_union=_bbox_union(bbox_boxes),
            topic_ids=topic_ids,
            topic_match_scores=_topic_match_scores(a, topic_ids),
            assignment_review_state=review_state,
        )

    def _asset_refs_for_block(
        self, entry: dict[str, Any], assets_by_id: dict[str, AssetRef]
    ) -> list[AssetRef]:
        """Resolve :class:`AssetRef` objects for a figure block.

        The lookup order is:

        1. ``block.asset_id`` (Stage 2's localized asset id field).
        2. ``block.image_url`` keying directly into ``assets_by_id``.
        3. URL / path basename match: MinerU's layout emits a bare
           hash like ``30079338947cc5b1c...`` as the ``image_path``;
           the Stage 2 registry records the original source URL
           whose final path segment is that hash plus ``.jpg`` /
           ``.png``. We strip the extension and look for any
           registered asset whose ``source_url`` ends with that
           stem.
        """
        image_urls = entry.get("image_urls") or []
        asset_id = entry.get("asset_id")
        if not image_urls and not asset_id:
            return []
        out: list[AssetRef] = []
        # 1. Direct asset_id lookup (Stage 2 fixture case).
        candidate: AssetRef | None = None
        if asset_id and asset_id in assets_by_id:
            candidate = assets_by_id[asset_id]
        # 2. Direct URL lookup (rare for MinerU real exports).
        if candidate is None:
            for url in image_urls:
                if url in assets_by_id:
                    candidate = assets_by_id[url]
                    break
        # 3. Basename match — covers the MinerU real export case
        # where the layout ``image_path`` is a bare hash. We use a
        # prefix match because the layout's ``image_path`` may be
        # shorter than the full hash in the asset registry's
        # ``source_url`` (the layout only stores the first ~64 chars).
        if candidate is None and image_urls:
            for url in image_urls:
                stem = url.rsplit("/", 1)[-1]
                if not stem:
                    continue
                # Strip a trailing extension if present.
                stem_no_ext = stem.rsplit(".", 1)[0] if "." in stem else stem
                best: AssetRef | None = None
                for _k, v in assets_by_id.items():
                    source_url = v.source_url or ""
                    if not source_url:
                        continue
                    # Longest stem that the source URL contains.
                    if (stem and len(stem) >= 16 and stem in source_url) or (
                        stem_no_ext and len(stem_no_ext) >= 16 and stem_no_ext in source_url
                    ):
                        if best is None or len(v.source_url or "") > len(best.source_url or ""):
                            best = v
                    # Fall back: the registry's local_path basename
                    # (asset_id + .png) appears nowhere in source_url,
                    # so we also accept stem prefix on the asset_id.
                    elif (
                        stem
                        and len(stem) >= 16
                        and stem.startswith(v.asset_id[: len(stem)])
                        and best is None
                    ):
                        best = v
                if best is not None:
                    candidate = best
                    break
        if candidate is None:
            return []
        out.append(
            AssetRef(
                asset_id=candidate.asset_id,
                local_path=candidate.local_path,
                sha256=candidate.sha256,
                mime_type=candidate.mime_type,
                width=candidate.width,
                height=candidate.height,
                source_url=candidate.source_url,
                source_page=candidate.source_page,
                caption=entry.get("caption") or candidate.caption,
            )
        )
        return out


_EXPECTED_BLOCK_TYPES_FOR_ITEM = {
    "chapter": {"heading"},
    "section": {"heading"},
    "definition": {"definition"},
    "theorem": {"theorem"},
    "example": {"worked_example", "example"},
    "solution": {"solution", "proof"},
    "exercise": {"exercise"},
    "summary": {"chapter_summary", "summary"},
    "knowledge_point": {"paragraph", "heading"},
    "method": {"paragraph", "heading"},
    "other": {"paragraph", "heading", "other"},
}

# A small adapter: keep the asset lookup available via the builder.
def _inject_assets(builder: BookViewBuilder, by_id: dict[str, AssetRef]) -> None:
    builder._assets_by_id = by_id  # type: ignore[attr-defined]


# Patch __init__ so we attach the assets registry to every builder.
_orig_init = BookViewBuilder.__init__


def _patched_init(self, workspace: ProjectWorkspace) -> None:  # type: ignore[no-redef]
    _orig_init(self, workspace)
    assets_path = workspace.normalized_dir / "assets_registry.json"
    by_id: dict[str, AssetRef] = {}
    if assets_path.is_file():
        by_id, _ = _load_assets_registry(assets_path)
    self._assets_by_id = by_id
    structure_context = _load_structure_context(
        workspace.normalized_dir / "document_structure.json"
    )
    self._attachments_by_target = structure_context.attachments_by_target
    self._explicit_attachment_sources = frozenset(
        source_id
        for source_ids in structure_context.attachments_by_target.values()
        for source_id in source_ids
    )
    self._structure_synthetic_blocks = structure_context.synthetic_blocks


BookViewBuilder.__init__ = _patched_init  # type: ignore[assignment]


# ---------------------------------------------------------------------- #
# Convenience wrapper
# ---------------------------------------------------------------------- #


def _block_id_sort_key(block_id: str) -> tuple[int, int, str]:
    """Sort ``p001-b007`` → (1, 7, 'p001-b007')."""
    m = re.match(r"^p(\d+)-b(\d+)$", block_id)
    if m:
        return (int(m.group(1)), int(m.group(2)), block_id)
    return (10**9, 10**9, block_id)


def _block_order_with_chapter(
    block_index: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    """Stable ordering of blocks by (chapter_path, block_id)."""
    return sorted(
        (
            (bid, e["chapter_path"][0] if e["chapter_path"] else "")
            for bid, e in block_index.items()
            if e.get("source") != "markdown_fallback"
        ),
        key=lambda pair: (
            pair[1],
            _block_id_sort_key(pair[0]),
        ),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_book_view(
    workspace: ProjectWorkspace,
    *,
    markdown_path: Path | str | None = None,
    layout_path: Path | str | None = None,
    assets_path: Path | str | None = None,
    assignments_path: Path | str | None = None,
) -> BookView:
    """Build a :class:`BookView` and persist it to the workspace."""
    builder = BookViewBuilder(workspace)
    book = builder.build(
        markdown_path=markdown_path,
        layout_path=layout_path,
        assets_path=assets_path,
        assignments_path=assignments_path,
    )

    out_dir = workspace.book_view_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "book_view.json"
    payload = book.to_dict()
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Quick validation: count items across chapters.
    total_items = sum(
        len(sec.items) for chap in book.chapters for sec in chap.sections
    )
    total_assets = sum(
        len(item.asset_refs)
        for chap in book.chapters
        for sec in chap.sections
        for item in sec.items
    )
    record_stage(
        workspace,
        "stage3_book_view",
        status=StageStatus.COMPLETED,
        input_fingerprint=book.normalized_fingerprint,
        output_fingerprint=hashlib.sha256(out_path.read_bytes()).hexdigest(),
        metadata={
            "book_id": book.book_id,
            "chapters": len(book.chapters),
            "sections": sum(len(c.sections) for c in book.chapters),
            "items": total_items,
            "asset_refs": total_assets,
            "book_view_path": str(out_path.relative_to(workspace.root)),
            "fingerprints": payload["fingerprints"],
        },
    )
    return book
