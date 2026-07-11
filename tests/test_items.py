"""Tests for ``extract_items`` and ``iter_chapters`` against the real fixture.

The fixture ``demos/inbox-sample/g8-triangle-ch03/full.md`` is a normalized
chapter with one chapter heading, five section headings (12.1-12.5),
three ``###`` sub-headings (12.2.1-12.2.3), one definition, three
theorems (SAS/ASA/SSS), two examples, two solutions, and three
exercises. The splitter emits three ``other`` items for the
sub-section headings (per the current ``items.py`` semantics), giving
20 items in total.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdf2dt.outlining.items import Item, extract_items, iter_chapters

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MD = ROOT / "demos/inbox-sample" / "g8-triangle-ch03" / "full.md"


@pytest.fixture
def items() -> list[Item]:
    return extract_items(FIXTURE_MD.read_text(encoding="utf-8"))


def test_extracts_exactly_twenty_items(items: list[Item]) -> None:
    assert len(items) == 20


def test_item_type_distribution(items: list[Item]) -> None:
    counts: dict[str, int] = {}
    for it in items:
        counts[it.item_type] = counts.get(it.item_type, 0) + 1
    # The fixture has exactly one chapter heading, five ``##`` section
    # headings, one definition, three theorems (SAS/ASA/SSS only — the
    # Chinese textbook uses 性质 for SAS/ASA/SSS which the splitter
    # currently routes to "theorem"), two examples, two solutions,
    # three exercises, and three ``other`` items for the
    # ``###`` sub-headings (12.2.1 / 12.2.2 / 12.2.3).
    assert counts == {
        "chapter": 1,
        "section": 5,
        "definition": 1,
        "theorem": 3,
        "example": 2,
        "solution": 2,
        "exercise": 3,
        "other": 3,
    }


def test_chapter_item_has_expected_chapter_path(items: list[Item]) -> None:
    chapter_item = items[0]
    assert chapter_item.item_type == "chapter"
    assert chapter_item.chapter_path == ("第十二章 全等三角形",)
    assert chapter_item.title == "第十二章 全等三角形"


def test_item_ids_are_unique_and_format_compliant(items: list[Item]) -> None:
    ids = [it.item_id for it in items]
    assert len(ids) == len(set(ids))
    pattern = re.compile(r"^item-\d{4}$")
    for iid in ids:
        assert pattern.match(iid), f"bad id: {iid}"


def test_iter_chapters_groups_under_chapter(items: list[Item]) -> None:
    grouped = list(iter_chapters(items))
    assert len(grouped) == 1
    chapter_title, group = grouped[0]
    assert chapter_title == "第十二章 全等三角形"
    assert len(group) == 20
    # Chapter is the first element of its own grouping.
    assert group[0].item_id == "item-0001"
    assert group[-1].item_id == "item-0020"


def test_searchable_strips_markdown_image_syntax() -> None:
    md = (
        "**例题 12.1** 如图所示, "
        "![三角形 ABC](assets/abc.png) "
        "中 $AB = AC$。 "
        "又见 ![外接圆](https://mineru.example/tmp/img_p004_001.png) 提示。"
    )
    [item] = extract_items(md)
    assert "![(" not in item.searchable
    assert "](" not in item.searchable
