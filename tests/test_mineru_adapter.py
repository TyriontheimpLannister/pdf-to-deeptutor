"""Tests for the MinerU pdf_info[] → pages[] adapter.

Domain-neutral port of the upstream ``test_mineru_adapter.py``. The
synthetic layout test uses generic sample text; the real-project test
is skipped when the private MinerU export is not present in this fork.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.bookview.mineru_adapter import (
    adapt_mineru_layout,
    is_mineru_layout,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_MINERU_LAYOUT = (
    PROJECT_ROOT / "projects" / "学之舟-总复习" / "normalized" / "layout.localized.json"
)


def test_is_mineru_layout_detects_pdf_info() -> None:
    assert is_mineru_layout({"pdf_info": []})
    assert not is_mineru_layout({"pages": []})
    assert not is_mineru_layout({})


def test_adapt_mineru_layout_handles_synthetic_mineru() -> None:
    """A synthetic MinerU layout with mixed text/title/image para_blocks."""
    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [595, 842],
                "para_blocks": [
                    {
                        "type": "title",
                        "bbox": [100, 200, 300, 250],
                        "blocks": [
                            {
                                "lines": [
                                    {
                                        "spans": [
                                            {"type": "text", "content": "Chapter One"}
                                        ]
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "bbox": [100, 260, 400, 400],
                        "blocks": [
                            {
                                "lines": [
                                    {
                                        "spans": [
                                            {"type": "text", "content": "Integers include positive, zero, negative."}
                                        ]
                                    },
                                    {
                                        "spans": [
                                            {"type": "text", "content": "Naturals start at 0."}
                                        ]
                                    },
                                ]
                            }
                        ],
                    },
                    {
                        "type": "image",
                        "bbox": [50, 500, 200, 700],
                        "blocks": [
                            {
                                "lines": [
                                    {
                                        "spans": [
                                            {"type": "image", "image_path": "abc123hash"}
                                        ]
                                    }
                                ]
                            }
                        ],
                    },
                ],
            }
        ]
    }
    pages = adapt_mineru_layout(layout)
    assert len(pages) == 1
    page = pages[0]
    assert page["page_index"] == 0
    assert page["page_number"] == 1
    assert len(page["blocks"]) == 3
    assert page["blocks"][0]["type"] == "heading"
    assert page["blocks"][0]["text"] == "Chapter One"
    assert page["blocks"][1]["type"] == "paragraph"
    assert "Integers include" in page["blocks"][1]["text"]
    assert "Naturals start at 0." in page["blocks"][1]["text"]
    assert page["blocks"][2]["type"] == "figure"
    assert page["blocks"][2]["image_url"] == "abc123hash"


@pytest.mark.skipif(
    not REAL_MINERU_LAYOUT.is_file(),
    reason="private 学之舟-总复习 MinerU layout is not shipped in this fork",
)
def test_adapt_mineru_layout_real_project() -> None:
    """The real 学之舟-总复习 layout has 11 pages with image-dominant content."""
    raw = json.loads(REAL_MINERU_LAYOUT.read_text(encoding="utf-8"))
    pages = adapt_mineru_layout(raw)
    assert len(pages) == 11
    total_images = sum(
        1 for page in pages for block in page["blocks"] if block.get("image_url")
    )
    assert total_images >= 10, f"expected at least 10 images, got {total_images}"
    for page in pages:
        for block in page["blocks"]:
            assert "block_id" in block
            assert block["block_id"].startswith("p")
            assert block["type"] in {
                "figure",
                "heading",
                "paragraph",
                "table",
                "equation",
                "list",
            }
            assert isinstance(block["bbox"], list) and len(block["bbox"]) == 4


def test_adapt_mineru_layout_raises_on_bad_input() -> None:
    with pytest.raises(ValueError, match="pdf_info"):
        adapt_mineru_layout({"pages": []})
