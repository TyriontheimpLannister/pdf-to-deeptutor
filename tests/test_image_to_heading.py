"""Tests for the markdown image-to-preceding-heading helper."""
from __future__ import annotations

from pdf2dt.project import ProjectWorkspace
from pdf2dt.review.figure_roles import (
    _iter_figure_candidates,
    build_image_to_local_contexts,
    build_image_to_preceding_heading,
)


def test_build_image_to_preceding_heading(tmp_path) -> None:
    ws_root = tmp_path
    (ws_root / "normalized").mkdir(parents=True)
    md = (
        "# 加减法巧算\n\n"
        "## 例题 1\n\n"
        "![image](assets/p1.jpg)\n\n"
        "## 练习\n\n"
        "![image](assets/p2.jpg)\n\n"
        "![image](assets/p3.jpg)\n\n"
        "## 本讲知识点汇总\n\n"
        "![image](assets/p4.jpg)\n"
    )
    (ws_root / "normalized" / "full.md").write_text(md, encoding="utf-8")
    ws = ProjectWorkspace(ws_root)
    result = build_image_to_preceding_heading(ws)
    assert result["assets/p1.jpg"] == "例题 1"
    assert result["assets/p2.jpg"] == "练习"
    assert result["assets/p3.jpg"] == "练习"
    assert result["assets/p4.jpg"] == "本讲知识点汇总"


def test_build_image_to_preceding_heading_handles_missing_md(tmp_path) -> None:
    ws = ProjectWorkspace(tmp_path)
    assert build_image_to_preceding_heading(ws) == {}


def test_build_image_to_preceding_heading_ignores_lines_without_image(
    tmp_path,
) -> None:
    ws_root = tmp_path
    (ws_root / "normalized").mkdir(parents=True)
    md = (
        "## 例题 1\n"
        "Some text with no image marker.\n"
        "![image](assets/p1.jpg)\n"
    )
    (ws_root / "normalized" / "full.md").write_text(md, encoding="utf-8")
    ws = ProjectWorkspace(ws_root)
    result = build_image_to_preceding_heading(ws)
    assert result == {"assets/p1.jpg": "例题 1"}


def test_build_image_to_preceding_heading_keeps_first_heading(tmp_path) -> None:
    """A duplicated image marker (e.g. a re-render artefact) must
    keep its first observed heading, not be overwritten.
    """
    ws_root = tmp_path
    (ws_root / "normalized").mkdir(parents=True)
    md = (
        "## 例题 1\n"
        "![image](assets/p1.jpg)\n"
        "## 练习\n"
        "![image](assets/p1.jpg)\n"
    )
    (ws_root / "normalized" / "full.md").write_text(md, encoding="utf-8")
    ws = ProjectWorkspace(ws_root)
    result = build_image_to_preceding_heading(ws)
    assert result["assets/p1.jpg"] == "例题 1"


def test_local_contexts_fall_back_to_normalized_full_markdown(tmp_path) -> None:
    ws_root = tmp_path
    (ws_root / "normalized").mkdir(parents=True)
    unrelated = "UNRELATED " * 100
    md = f"题目前文。\n![image](assets/p1.jpg)\n题目后文。\n{unrelated}\n"
    (ws_root / "normalized" / "full.md").write_text(md, encoding="utf-8")
    ws = ProjectWorkspace(ws_root)
    contexts = build_image_to_local_contexts(ws)
    book_view = {
        "items": [
            {
                "item_id": "item-1",
                "text": "章节标题",
                "asset_refs": [
                    {
                        "asset_id": "p1",
                        "local_path": r"projects\book\assets\p1.jpg",
                    }
                ],
            }
        ]
    }

    candidate = next(iter(_iter_figure_candidates(book_view, image_local_contexts=contexts)))

    assert "题目前文" in candidate.local_context
    assert "题目后文" in candidate.local_context
    assert "无关的后续内容" not in candidate.local_context
