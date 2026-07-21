"""Tests for deterministic Stage 2.5 document-structure recovery."""

from __future__ import annotations

from pdf2dt.document_structure import recover_document_structure


def _pages(*page_blocks: list[dict]) -> dict:
    return {
        "pages": [
            {
                "page_index": index,
                "page_number": index + 1,
                "blocks": blocks,
            }
            for index, blocks in enumerate(page_blocks)
        ]
    }


def _relation_pairs(structure, kind: str) -> set[tuple[str, str, str]]:
    return {
        (relation.source_id, relation.target_id, relation.review_state)
        for relation in structure.relations
        if relation.kind == kind
    }


def test_recovers_heading_ownership_and_stable_serialization() -> None:
    layout = _pages(
        [
            {"block_id": "p000-b000", "type": "heading", "level": 1, "text": "第一章"},
            {"block_id": "p000-b001", "type": "paragraph", "text": "第一段内容。"},
            {"block_id": "p000-b002", "type": "heading", "level": 2, "text": "第一节"},
            {"block_id": "p000-b003", "type": "paragraph", "text": "第二段内容。"},
        ]
    )

    first = recover_document_structure(layout)
    second = recover_document_structure(layout)

    assert _relation_pairs(first, "parent_heading") == {
        ("p000-b001", "p000-b000", "confirmed"),
        ("p000-b002", "p000-b000", "confirmed"),
        ("p000-b003", "p000-b002", "confirmed"),
    }
    assert first.to_dict() == second.to_dict()


def test_recovers_caption_and_figure_attachment() -> None:
    layout = _pages(
        [
            {
                "block_id": "p000-b000",
                "type": "paragraph",
                "text": "观察下面的三角形。",
                "bbox": [0, 0, 100, 30],
            },
            {
                "block_id": "p000-b001",
                "type": "figure",
                "image_url": "assets/a.png",
                "bbox": [10, 40, 90, 160],
            },
            {
                "block_id": "p000-b002",
                "type": "image_caption",
                "text": "图 1 三角形",
                "bbox": [10, 165, 90, 180],
            },
        ]
    )

    structure = recover_document_structure(layout)

    assert _relation_pairs(structure, "caption_for") == {("p000-b002", "p000-b001", "confirmed")}
    assert _relation_pairs(structure, "attached_to") == {("p000-b001", "p000-b000", "suggested")}


def test_recovers_only_safe_cross_page_continuation() -> None:
    layout = _pages(
        [
            {
                "block_id": "p000-b000",
                "type": "paragraph",
                "text": "这是跨页继续的段落文字",
                "bbox": [0, 100, 100, 150],
            }
        ],
        [
            {
                "block_id": "p001-b000",
                "type": "paragraph",
                "text": "后半段仍然属于同一段。",
                "bbox": [0, 50, 100, 100],
            }
        ],
    )

    structure = recover_document_structure(layout)

    assert _relation_pairs(structure, "continues_to") == {("p000-b000", "p001-b000", "confirmed")}


def test_does_not_continue_after_terminal_punctuation_or_into_list() -> None:
    layout = _pages(
        [{"block_id": "p000-b000", "type": "paragraph", "text": "这是完整的一段内容。"}],
        [{"block_id": "p001-b000", "type": "list", "text": "1. 这是新的题目"}],
    )

    structure = recover_document_structure(layout)

    assert _relation_pairs(structure, "continues_to") == set()


def test_ambiguous_figure_does_not_receive_attachment() -> None:
    layout = _pages(
        [
            {
                "block_id": "p000-b000",
                "type": "paragraph",
                "text": "左侧说明",
                "bbox": [0, 0, 40, 30],
            },
            {
                "block_id": "p000-b001",
                "type": "paragraph",
                "text": "右侧说明",
                "bbox": [60, 0, 100, 30],
            },
            {
                "block_id": "p000-b002",
                "type": "figure",
                "image_url": "assets/a.png",
                "bbox": [40, 40, 60, 80],
            },
        ]
    )

    structure = recover_document_structure(layout)

    assert _relation_pairs(structure, "attached_to") == set()


def test_recovery_merges_markdown_fallback_without_cross_page_guess() -> None:
    layout = _pages(
        [
            {
                "block_id": "p000-b000",
                "type": "figure",
                "image_url": "assets/a.png",
                "asset_id": "a",
                "bbox": [0, 10, 100, 100],
            }
        ]
    )
    markdown = (
        "# 第一章\n\n## 例题 1\n\n"
        + ("观察这个图形。" * 35)
        + "\n\n![图](assets/a.png)\n"
    )

    structure = recover_document_structure(layout, markdown_text=markdown)
    payload = structure.to_dict()

    assert payload["alignment"]["status"] == "active"
    assert any(block["source"] == "markdown_fallback" for block in payload["blocks"])
    assert any(
        relation["kind"] == "attached_to"
        and relation["source_id"] == "p000-b000"
        and relation["target_id"].startswith("md-b")
        for relation in payload["relations"]
    )
    assert not any(
        relation["kind"] == "continues_to"
        and relation["source_id"].startswith("md-b")
        for relation in payload["relations"]
    )
    layout_block = next(block for block in payload["blocks"] if block["source"] == "layout")
    assert layout_block["bbox"] == [0.0, 10.0, 100.0, 100.0]


def test_recovery_keeps_rich_layout_authoritative() -> None:
    text = "正文" * 100
    layout = _pages(
        [
            {
                "block_id": "p000-b000",
                "type": "paragraph",
                "text": text,
            }
        ]
    )

    structure = recover_document_structure(layout, markdown_text=text)
    payload = structure.to_dict()

    assert payload["alignment"]["status"] == "not_needed"
    assert all(block["source"] == "layout" for block in payload["blocks"])
