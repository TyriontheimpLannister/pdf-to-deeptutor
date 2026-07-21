"""Tests for deterministic Markdown-to-layout fallback alignment."""

from __future__ import annotations

from pdf2dt.document_structure.alignment import (
    LayoutVisual,
    align_markdown_to_layout,
)


def _visual(
    block_id: str,
    page_index: int,
    path: str,
    asset_id: str,
) -> LayoutVisual:
    return LayoutVisual(
        block_id=block_id,
        page_index=page_index,
        page_number=page_index + 1,
        image_path=path,
        asset_id=asset_id,
    )


def test_low_text_coverage_activates_and_preserves_provenance() -> None:
    markdown = "# 第一章\n\n" + ("观察下图。" * 40) + "\n\n![任意 alt](assets/a.png)\n"

    result = align_markdown_to_layout(
        markdown,
        layout_text_chars=0,
        layout_text_blocks=0,
        visuals=(_visual("p000-b002", 0, "assets/a.png", "a"),),
    )

    assert result.summary.status == "active"
    assert result.summary.matched_images == 1
    assert [(block.block_type, block.source_line_start) for block in result.blocks] == [
        ("heading", 1),
        ("paragraph", 3),
    ]
    assert result.blocks[1].anchor_block_id == "p000-b002"
    assert result.blocks[1].bbox is None
    relation_keys = {
        (relation.kind, relation.source_id, relation.target_id)
        for relation in result.relations
    }
    assert relation_keys == {
        ("parent_heading", "md-b000002", "md-b000001"),
        ("attached_to", "p000-b002", "md-b000002"),
    }


def test_rich_layout_does_not_activate() -> None:
    markdown = "正文" * 100

    result = align_markdown_to_layout(
        markdown, layout_text_chars=200, layout_text_blocks=1, visuals=()
    )

    assert result.summary.status == "not_needed"
    assert result.summary.layout_text_coverage == 1.0
    assert result.blocks == ()
    assert result.relations == ()


def test_missing_markdown_and_missing_anchor_fail_safe() -> None:
    missing = align_markdown_to_layout(
        None, layout_text_chars=0, layout_text_blocks=0, visuals=()
    )
    assert missing.summary.status == "missing_markdown"

    no_match_markdown = "# 第一章\n\n" + ("没有匹配图片的正文。" * 30) + (
        "\n\n![图](assets/missing.png)\n"
    )
    no_match = align_markdown_to_layout(
        no_match_markdown,
        layout_text_chars=0,
        layout_text_blocks=0,
        visuals=(_visual("p000-b000", 0, "assets/other.png", "other"),),
    )
    assert no_match.summary.status == "unavailable_no_image_matches"
    assert no_match.summary.markdown_only_images == 1
    assert no_match.summary.layout_only_images == 1
    assert no_match.blocks == ()
    assert no_match.relations == ()


def test_matches_windows_paths_and_duplicate_occurrences() -> None:
    markdown = (
        "# 第一章\n\n"
        + ("第一道题。" * 40)
        + "\n\n![甲](assets\\a.png)\n\n"
        + ("第二道题。" * 40)
        + "\n\n![乙](assets/a.png)\n\n"
        + "![缺失](assets/missing.png)\n"
    )
    visuals = (
        _visual("p000-b001", 0, "assets/a.png", "a"),
        _visual("p001-b001", 1, "assets/a.png", "a"),
        _visual("p002-b001", 2, "assets/layout-only.png", "layout-only"),
    )

    result = align_markdown_to_layout(
        markdown,
        layout_text_chars=0,
        layout_text_blocks=0,
        visuals=visuals,
    )

    paragraphs = [block for block in result.blocks if block.block_type == "paragraph"]
    assert [block.anchor_block_id for block in paragraphs] == [
        "p000-b001",
        "p001-b001",
    ]
    assert result.summary.matched_images == 2
    assert result.summary.markdown_only_images == 1
    assert result.summary.layout_only_images == 1


def test_parses_lists_and_inline_images_without_cross_page_relations() -> None:
    markdown = (
        "# 第一章\n\n"
        + "1. "
        + ("观察规律" * 60)
        + " ![示意图](<assets/a.png>) 继续作答\n"
    )

    result = align_markdown_to_layout(
        markdown,
        layout_text_chars=0,
        layout_text_blocks=0,
        visuals=(_visual("p000-b001", 0, "assets/a.png", "a"),),
    )

    list_block = next(block for block in result.blocks if block.block_type == "list")
    assert "![" not in list_block.text
    assert "继续作答" in list_block.text
    assert list_block.heading_level is None
    assert all(relation.kind != "continues_to" for relation in result.relations)


def test_alignment_is_deterministic() -> None:
    markdown = "# 第一章\n\n" + ("确定性正文。" * 40) + "\n\n![图](assets/a.png)\n"
    visuals = (_visual("p000-b001", 0, "assets/a.png", "a"),)

    first = align_markdown_to_layout(
        markdown, layout_text_chars=0, layout_text_blocks=0, visuals=visuals
    )
    second = align_markdown_to_layout(
        markdown, layout_text_chars=0, layout_text_blocks=0, visuals=visuals
    )

    assert first == second


def test_text_block_density_prevents_fallback_on_partial_but_structured_layout() -> None:
    markdown = "正文" * 614
    visuals = tuple(
        _visual(f"p000-b{index:03d}", 0, f"assets/{index}.png", str(index))
        for index in range(4)
    )

    result = align_markdown_to_layout(
        markdown,
        layout_text_chars=179,
        layout_text_blocks=11,
        visuals=visuals,
    )

    assert result.summary.layout_text_coverage < 0.20
    assert result.summary.layout_text_block_share > 0.10
    assert result.summary.status == "not_needed"


def test_image_immediately_after_heading_attaches_to_heading() -> None:
    markdown = (
        "# 第一章\n\n![章首图](assets/a.png)\n\n" + ("后续正文。" * 45) + "\n"
    )

    result = align_markdown_to_layout(
        markdown,
        layout_text_chars=0,
        layout_text_blocks=0,
        visuals=(_visual("p000-b001", 0, "assets/a.png", "a"),),
    )

    assert any(
        relation.kind == "attached_to"
        and relation.source_id == "p000-b001"
        and relation.target_id == "md-b000001"
        for relation in result.relations
    )
