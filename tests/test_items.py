"""Tests for ``extract_items`` against the generic sample fixture.

The fixture ``demos/inbox-sample/sample-chapter/full.md`` is a small,
subject-agnostic chapter: one ``#`` chapter heading, three ``##``
section headings, one ``###`` sub-heading carrying body text, plus
definition / theorem / example / solution / method / exercise / summary
/ knowledge-point markers. The splitter emits 14 items in total.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from pdf2dt.outlining.items import Item, extract_items, iter_chapters

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MD = ROOT / "demos" / "inbox-sample" / "sample-chapter" / "full.md"


@pytest.fixture
def items() -> list[Item]:
    return extract_items(FIXTURE_MD.read_text(encoding="utf-8"))


def test_extracts_exactly_fourteen_items(items: list[Item]) -> None:
    assert len(items) == 14


def test_item_type_distribution(items: list[Item]) -> None:
    counts = Counter(it.item_type for it in items)
    assert dict(counts) == {
        "chapter": 1,
        "section": 3,
        "definition": 1,
        "theorem": 1,
        "other": 1,
        "example": 1,
        "solution": 1,
        "method": 1,
        "exercise": 2,
        "summary": 1,
        "knowledge_point": 1,
    }


def test_chapter_path_records_heading_hierarchy(items: list[Item]) -> None:
    definition = next(it for it in items if it.item_type == "definition")
    assert definition.chapter_path == ("第一章 导论", "第1节 基础概念")


def test_searchable_is_lowercased_plain_text(items: list[Item]) -> None:
    definition = next(it for it in items if it.item_type == "definition")
    assert definition.searchable == definition.text.lower()


def test_searchable_strips_image_markdown_to_alt_text(items: list[Item]) -> None:
    # The image reference lives inside the solution item; its alt text
    # should survive in ``searchable`` while the remote URL is dropped.
    solution = next(it for it in items if it.item_type == "solution")
    assert "mineru.example" not in solution.searchable
    assert "集合 a 的韦恩图" in solution.searchable


def test_iter_chapters_groups_under_single_chapter(items: list[Item]) -> None:
    groups = list(iter_chapters(items))
    assert len(groups) == 1
    title, grouped = groups[0]
    assert title == "第一章 导论"
    assert len(grouped) == 14
