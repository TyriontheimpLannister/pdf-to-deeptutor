"""Unit tests for the text-noise classifier."""
from __future__ import annotations

import pytest

from pdf2dt.outlining import (
    NOISE_TITLE_PATTERNS,
    classify_noise,
    is_noise_item,
    partition_items,
)
from pdf2dt.review.figure_roles import DECOR_CONTEXT_PATTERNS

# ---------- watermark ----------


@pytest.mark.parametrize(
    "title",
    [
        "微信公众号 教辅资料站",
        "微信公众号",
        "教辅资料站",
        "学而思网校",
        "新东方在线",
        "高思教育",
        "本资料由微信公众号 教辅资料站发布",
    ],
)
def test_watermark_title_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": "anything"})
    assert v.is_noise, v
    assert "watermark" in v.reason


# ---------- single-character ASCII ----------


@pytest.mark.parametrize("title", ["#", "@", "?", "$", "&", "*"])
def test_single_ascii_punctuation_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": "more text"})
    assert v.is_noise, v
    assert "single ASCII punctuation" in v.reason


@pytest.mark.parametrize("title", ["U", "L", "M", "Z"])
def test_single_ascii_letter_with_empty_body_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": ""})
    assert v.is_noise, v
    assert "single ASCII letter" in v.reason


@pytest.mark.parametrize("title", ["U", "L"])
def test_single_ascii_letter_with_long_body_is_not_noise(title: str) -> None:
    """A single-letter title with real math body must survive.

    Real example: '习' might appear as a stray OCR title for a
    section that contains a real problem; the heuristic must
    keep the item so the math is not silently dropped.
    """
    v = classify_noise(
        {"title": title, "text": "如图所示, 5. 观察下图中的规律请按照规律填出空格中的图形."}
    )
    assert not v.is_noise, v


# ---------- pure-digit page numbers ----------


@pytest.mark.parametrize("title", ["1", "10", "118", "255"])
def test_short_pure_digit_title_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": ""})
    assert v.is_noise, v
    assert "page number" in v.reason


def test_four_digit_title_is_not_pure_page_number() -> None:
    """Year numbers like '2025' are real chapter labels."""
    v = classify_noise({"title": "2025", "text": ""})
    assert not v.is_noise, v


# ---------- short ASCII title with empty body ----------


@pytest.mark.parametrize("title", ["U.", "U1", "u2"])
def test_short_ascii_title_with_empty_body_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": ""})
    assert v.is_noise, v


# ---------- page range ----------


@pytest.mark.parametrize("title", ["8-2", "12-1", "3-15"])
def test_page_range_with_empty_body_is_noise(title: str) -> None:
    v = classify_noise({"title": title, "text": ""})
    assert v.is_noise, v
    assert "page range" in v.reason


# ---------- keep-list ----------


@pytest.mark.parametrize(
    "item",
    [
        {"title": "例题 5", "text": "求证: $AB \\parallel CD$"},
        {"title": "例题 1", "text": "刺猬和松鼠共采了 88 个坚果..."},
        {"title": "思考题", "text": "如果 $x = 2$, 求 $y$ 的值."},
        {"title": "20 旅行中的数学", "text": "与故事中类似, 在生活中经常会需要设计一个旅行计划."},
        {"title": "习", "text": "5. 观察下图中的规律, 请按照这种规律, 填出空格中的图形."},
        {"title": "整数", "text": "本节学习整数的加减法."},
    ],
)
def test_real_math_content_is_not_noise(item: dict) -> None:
    assert not is_noise_item(item), item


# ---------- empty-title edge cases ----------


def test_empty_title_with_empty_body_is_noise() -> None:
    v = classify_noise({"title": "", "text": ""})
    assert v.is_noise
    assert "empty" in v.reason


def test_empty_title_with_meaningful_body_is_not_noise() -> None:
    v = classify_noise({"title": "", "text": "这是一个数学公式 $a + b = c$"})
    assert not v.is_noise


def test_missing_title_with_meaningful_body_is_not_noise() -> None:
    v = classify_noise({"text": "这是没有 title 的段落但内容是数学"})
    assert not v.is_noise


# ---------- partition_items ----------


def test_partition_items_splits_in_order() -> None:
    items = [
        {"item_id": "a", "title": "例题 1", "text": "数学题"},
        {"item_id": "b", "title": "#", "text": ""},
        {"item_id": "c", "title": "整数", "text": "学习整数的加减法"},
        {"item_id": "d", "title": "118", "text": ""},
        {"item_id": "e", "title": "微信公众号 教辅资料站", "text": "..."},
    ]
    kept, dropped = partition_items(items)
    assert [x["item_id"] for x in kept] == ["a", "c"]
    assert [x["item_id"] for x in dropped] == ["b", "d", "e"]


# ---------- watermark constants stay in sync with figure role filter ----------


def test_watermark_lists_stay_in_sync() -> None:
    """The two filter modules must agree on what counts as a
    publisher watermark. Drift here means a publisher banner might
    be classified as a content figure but flagged as a text-noise
    item, which would surface as a weird-looking export.
    """
    noise_set = set(NOISE_TITLE_PATTERNS)
    decor_set = set(DECOR_CONTEXT_PATTERNS)
    missing_in_decor = noise_set - decor_set
    missing_in_noise = decor_set - noise_set
    assert not missing_in_decor, (
        f"NOISE_TITLE_PATTERNS missing from DECOR_CONTEXT_PATTERNS: {missing_in_decor}"
    )
    assert not missing_in_noise, (
        f"DECOR_CONTEXT_PATTERNS missing from NOISE_TITLE_PATTERNS: {missing_in_noise}"
    )
