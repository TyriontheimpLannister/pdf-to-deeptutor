"""Tests for the MinerU inbox loader using the synthetic fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.inbox import InboxLoader, InboxTask, LoadedMinerU, load_inbox_task
from pdf2dt.inbox.loader import ValidationError

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "demos" / "inbox-sample"
TASK_DIR = FIXTURE_DIR / "g8-triangle-ch03"
META_JSON = TASK_DIR / "meta.json"


@pytest.fixture
def loader() -> InboxLoader:
    return InboxLoader(FIXTURE_DIR)


class TestScan:
    def test_finds_fixture_task(self, loader: InboxLoader) -> None:
        tasks = loader.scan()
        assert len(tasks) == 1
        assert tasks[0].task_id == "g8-triangle-ch03-sample"

    def test_skips_reserved_dirs(self) -> None:
        # Create fake reserved dirs inside a temporary inbox root
        root = Path(__file__).resolve().parent / "_tmp_inbox"
        root.mkdir(exist_ok=True)
        (root / "_processing").mkdir(exist_ok=True)
        (root / "_archive").mkdir(exist_ok=True)
        (root / "real-task").mkdir(exist_ok=True)
        (root / "real-task" / "meta.json").write_text(
            json.dumps(
                {
                    "task_id": "real-task",
                    "source": {
                        "original_filename": "x.pdf",
                        "sha256": "0" * 64,
                    },
                    "minerU": {"version": "1", "exported_at": "2026-01-01T00:00:00Z"},
                    "products": {"markdown": "full.md"},
                }
            ),
            encoding="utf-8",
        )
        (root / "real-task" / "full.md").write_text("# hello", encoding="utf-8")

        ldr = InboxLoader(root)
        tasks = ldr.scan()
        assert len(tasks) == 1
        assert tasks[0].task_id == "real-task"


class TestValidateTaskDir:
    def test_valid_fixture_passes(self) -> None:
        task = InboxLoader(FIXTURE_DIR)._validate_task_dir(TASK_DIR)
        assert task.task_id == "g8-triangle-ch03-sample"
        assert task.meta.source.original_filename == "八年级数学-全等三角形-习题集.pdf"
        assert task.meta.products.markdown == "full.md"
        assert task.meta.products.layout_json == "layout.json"
        assert task.images_dir is not None
        assert task.images_dir.exists()
        # source.pdf is a placeholder, not a real file
        assert task.source_pdf_path is None

    def test_missing_meta_json_raises(self) -> None:
        bad_dir = TASK_DIR.parent / "no-meta"
        bad_dir.mkdir(exist_ok=True)
        with pytest.raises(ValidationError, match="missing meta.json"):
            InboxLoader(FIXTURE_DIR)._validate_task_dir(bad_dir)

    def test_missing_markdown_raises(self) -> None:
        bad_dir = TASK_DIR.parent / "no-md"
        bad_dir.mkdir(exist_ok=True)
        (bad_dir / "meta.json").write_text(
            json.dumps(
                {
                    "task_id": "no-md",
                    "source": {
                        "original_filename": "x.pdf",
                        "sha256": "0" * 64,
                    },
                    "minerU": {"version": "1", "exported_at": "2026-01-01T00:00:00Z"},
                    "products": {"markdown": "full.md"},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError, match="missing markdown product"):
            InboxLoader(FIXTURE_DIR)._validate_task_dir(bad_dir)


class TestLoadTask:
    def test_loads_markdown(self) -> None:
        loaded = load_inbox_task(TASK_DIR)
        assert "第十二章 全等三角形" in loaded.markdown_text
        assert "边角边定理" in loaded.markdown_text

    def test_loads_layout(self) -> None:
        loaded = load_inbox_task(TASK_DIR)
        assert loaded.layout_data is not None
        assert "pages" in loaded.layout_data
        assert len(loaded.layout_data["pages"]) == 4

    def test_collects_image_urls(self) -> None:
        loaded = load_inbox_task(TASK_DIR)
        assert len(loaded.image_references) == 4
        for url in loaded.image_references:
            assert url.startswith("https://mineru.example/tmp/")

    def test_image_urls_are_unique(self) -> None:
        loaded = load_inbox_task(TASK_DIR)
        assert len(loaded.image_references) == len(set(loaded.image_references))

    def test_convenience_function(self) -> None:
        loaded = load_inbox_task(TASK_DIR)
        assert isinstance(loaded, LoadedMinerU)
        assert loaded.task_id == "g8-triangle-ch03-sample"

