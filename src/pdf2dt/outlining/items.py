"""BookView item extraction from normalized markdown.

This is a deliberately small, deterministic splitter: it walks the
markdown line by line, recognizes heading levels, definition/theorem/
example/solution/exercise/summary markers, and emits one ``Item`` per
content block. Image references and inline code spans are kept on the
item as plain text so that the matcher can use them as additional
context.

It does **not** aim to reproduce the full Markdown AST — only the
shape the outline matcher needs. Later stages (geometry, review) may
replace this with a richer representation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# Recognized item-type markers as Chinese textbook conventions use them.
# Each marker is a bold lead-in like "**定义 12.1**" or "**例题 12.2**".
_MARKER_RE = re.compile(
    r"^\*\*\s*("
    r"定义|定理|性质|推论|命题|引理|公理"
    r"|例题|例|习题|练习|解|证明|证明:|小结|总结|归纳|方法|思路"
    r"|考点|知识点"
    r")\b"
)
# Headings: ## / ### only for now (## chapter / ### section).
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
# Image: ![alt](url) — alt kept as plain text context.
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Inline image alt plain-text fallback for the searchable buffer.
_PLAIN_IMG_ALT_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

# Section dividers used in Chinese math textbooks (一、二、… / 第X节).
_SECTION_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千0-9]+节\b")


@dataclass
class Item:
    """One content block extracted from the normalized markdown."""

    item_id: str
    item_type: str  # chapter | section | definition | theorem | example | solution | exercise | summary | other
    title: str
    text: str  # full text including the title (used by the matcher)
    chapter_path: tuple[str, ...] = ()  # ordered heading titles above this item
    source_block_ids: tuple[str, ...] = ()  # placeholder; Stage 3 will fill
    asset_ids: tuple[str, ...] = ()
    review_state: str = "unreviewed"
    topic_ids: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def searchable(self) -> str:
        """Lower-cased plain-text buffer used by the matcher."""
        # Strip markdown image syntax — the alt text is what matters
        # for vocabulary matching.
        text = _PLAIN_IMG_ALT_RE.sub(lambda m: m.group(0)[2:-2].split("](")[0], self.text)
        return text.lower()


def extract_items(markdown_text: str) -> list[Item]:
    """Split a normalized markdown document into content items.

    The splitter is line-based and assumes well-formed UTF-8 text. It
    walks the document, accumulating paragraphs into the current
    "bucket" item. When a heading or new marker appears, the current
    item is finalized and a new one starts.
    """
    items: list[Item] = []
    chapter_path: list[str] = []
    section_path: list[str] = []

    cur_type = "other"
    cur_title = ""
    cur_lines: list[str] = []
    cur_chapter: tuple[str, ...] = ()

    def _flush() -> None:
        nonlocal cur_type, cur_title, cur_lines, cur_chapter
        if not cur_lines and not cur_title:
            return
        body = "\n".join(cur_lines).strip()
        text = (cur_title + "\n" + body).strip() if body else cur_title.strip()
        if text:
            idx = len(items) + 1
            items.append(
                Item(
                    item_id=f"item-{idx:04d}",
                    item_type=cur_type,
                    title=cur_title.strip(),
                    text=text,
                    chapter_path=cur_chapter,
                )
            )
        cur_type = "other"
        cur_title = ""
        cur_lines = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()

        heading = _HEADING_RE.match(line)
        if heading:
            _flush()
            hashes, title = heading.group(1), heading.group(2).strip()
            level = len(hashes)
            if level == 1:
                chapter_path = [title]
                section_path = []
                cur_type = "chapter"
                cur_title = title
                cur_chapter = tuple(chapter_path)
                cur_lines = []
            elif level == 2:
                if _SECTION_PREFIX_RE.match(title):
                    section_path = [title]
                    cur_type = "section"
                else:
                    section_path = [title]
                    cur_type = "section"
                cur_title = title
                cur_chapter = tuple(chapter_path + section_path)
                cur_lines = []
            else:
                # Treat as a sub-heading; roll into current section.
                cur_title = title
                cur_lines = []
            continue

        marker = _MARKER_RE.match(line)
        if marker:
            _flush()
            kind = marker.group(1)
            cur_type = _ITEM_TYPE_BY_MARKER.get(kind, "other")
            cur_title = line.strip().strip("*").strip()
            cur_chapter = tuple(chapter_path + section_path)
            cur_lines = []
            continue

        cur_lines.append(line)

    _flush()
    return items


_ITEM_TYPE_BY_MARKER = {
    "定义": "definition",
    "定理": "theorem",
    "性质": "theorem",
    "推论": "theorem",
    "命题": "theorem",
    "引理": "theorem",
    "公理": "theorem",
    "例题": "example",
    "例": "example",
    "习题": "exercise",
    "练习": "exercise",
    "解": "solution",
    "证明": "solution",
    "证明:": "solution",
    "小结": "summary",
    "总结": "summary",
    "归纳": "summary",
    "方法": "method",
    "思路": "method",
    "考点": "knowledge_point",
    "知识点": "knowledge_point",
}


def extract_items_from_file(path: Path | str) -> list[Item]:
    """Convenience wrapper that reads a markdown file."""
    return extract_items(Path(path).read_text(encoding="utf-8"))


__all__ = ["Item", "extract_items", "extract_items_from_file"]


def iter_chapters(items: Iterable[Item]) -> Iterable[tuple[str, list[Item]]]:
    """Group items under their chapter heading for diagnostic output."""
    by_chapter: dict[str, list[Item]] = {}
    order: list[str] = []
    for item in items:
        chapter_title = item.chapter_path[0] if item.chapter_path else ""
        if chapter_title not in by_chapter:
            by_chapter[chapter_title] = []
            order.append(chapter_title)
        by_chapter[chapter_title].append(item)
    for title in order:
        yield title, by_chapter[title]