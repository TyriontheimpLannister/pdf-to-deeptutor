"""Tests for the pre-flight checker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.pipeline import PreFlightFailureError
from pdf2dt.preflight import (
    CheckResult,
    CheckSeverity,
    PreFlightChecker,
    check_task,
)
from pdf2dt.preflight.checker import PreFlightError

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "demos/inbox-sample"
TASK_DIR = FIXTURE_DIR / "g8-triangle-ch03"


def _make_task_dir(tmp_path: Path, name: str = "test-task") -> Path:
    """Create a minimal valid task directory for testing."""
    task = tmp_path / name
    task.mkdir()
    (task / "meta.json").write_text(
        json.dumps(
            {
                "task_id": name,
                "source": {
                    "original_filename": "source.pdf",
                    "sha256": "a" * 64,
                },
                "minerU": {"version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
                "products": {"markdown": "full.md", "layout_json": "layout.json"},
            }
        ),
        encoding="utf-8",
    )
    return task


def _write_md(task: Path, content: str) -> None:
    (task / "full.md").write_text(content, encoding="utf-8")


def _write_layout(task: Path, data: dict) -> None:
    (task / "layout.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestPreFlightReport:
    def test_ok_report_proceeds(self) -> None:
        from pdf2dt.preflight.report import PreFlightReport

        report = PreFlightReport(task_id="t", task_dir="/tmp")
        report.add(CheckResult(
            name="x", severity=CheckSeverity.OK, message="fine"
        ))
        assert report.should_proceed is True
        assert report.overall_severity == CheckSeverity.OK
        assert len(report.errors) == 0

    def test_error_blocks_proceed(self) -> None:
        from pdf2dt.preflight.report import PreFlightReport

        report = PreFlightReport(task_id="t", task_dir="/tmp")
        report.add(CheckResult(
            name="x", severity=CheckSeverity.ERROR, message="bad"
        ))
        assert report.should_proceed is False
        assert report.overall_severity == CheckSeverity.ERROR
        assert len(report.errors) == 1

    def test_warning_does_not_block(self) -> None:
        from pdf2dt.preflight.report import PreFlightReport

        report = PreFlightReport(task_id="t", task_dir="/tmp")
        report.add(CheckResult(
            name="x", severity=CheckSeverity.WARNING, message="hmm"
        ))
        assert report.should_proceed is True
        assert report.overall_severity == CheckSeverity.WARNING

    def test_summary_and_detail_strings(self) -> None:
        from pdf2dt.preflight.report import PreFlightReport

        report = PreFlightReport(task_id="t", task_dir="/tmp")
        report.add(CheckResult(name="ok1", severity=CheckSeverity.OK, message="good"))
        report.add(CheckResult(name="warn1", severity=CheckSeverity.WARNING, message="hmm"))
        s = report.to_summary()
        d = report.to_detail()
        assert "1 warning(s)" in s
        assert "1 ok" in s
        assert "WARN" in d
        assert "Should proceed: True" in d


class TestStructureCheck:
    def test_missing_meta_json(self, tmp_path: Path) -> None:
        task = tmp_path / "no-meta"
        task.mkdir()
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        assert not report.should_proceed
        struct = [r for r in report.results if r.name == "structure"]
        assert len(struct) == 1
        assert struct[0].severity == CheckSeverity.ERROR
        assert "meta.json" in struct[0].message

    def test_invalid_meta_json(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "bad-json")
        (task / "meta.json").write_text("not json {{{", encoding="utf-8")
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        assert not report.should_proceed
        struct = [r for r in report.results if r.name == "structure"]
        assert struct[0].severity == CheckSeverity.ERROR

    def test_missing_markdown_file(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "no-md")
        # Don't write full.md
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        assert not report.should_proceed
        struct = [r for r in report.results if r.name == "structure"]
        assert struct[0].severity == CheckSeverity.ERROR
        assert "missing" in struct[0].message.lower() or "not" in struct[0].message.lower()

    def test_valid_structure(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "good")
        _write_md(task, "# Chapter 1\n\nSome content here.")
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        struct = [r for r in report.results if r.name == "structure"]
        assert struct[0].severity == CheckSeverity.OK


class TestContentCheck:
    def test_empty_markdown(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "empty")
        _write_md(task, "   \n\n   \n")
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        content = [r for r in report.results if r.name == "content"]
        assert content[0].severity == CheckSeverity.ERROR
        assert "empty" in content[0].message.lower()

    def test_no_headings_warning(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "no-headings")
        _write_md(task, "This is a paragraph without headings.\n" * 10)
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        content = [r for r in report.results if r.name == "content"]
        assert content[0].severity == CheckSeverity.WARNING
        assert "heading" in content[0].message.lower()

    def test_good_content(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "good-content")
        _write_md(
            task,
            "# 第三章 三角形\n\n## 3.1 定义\n\n"
            "三角形是由三条线段围成的封闭图形。\n" * 5,
        )
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        content = [r for r in report.results if r.name == "content"]
        assert content[0].severity == CheckSeverity.OK

    def test_ocr_fragmentation_warning(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "fragmented")
        # Many very short lines
        _write_md(task, "\n".join(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]))
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        content = [r for r in report.results if r.name == "content"]
        assert content[0].severity == CheckSeverity.WARNING


class TestImageRefCheck:
    def test_no_images_ok(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "text-only")
        _write_md(task, "# Title\n\nText content without images.")
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        img = [r for r in report.results if r.name == "image_refs"]
        assert img[0].severity == CheckSeverity.OK
        assert "text-only" in img[0].message.lower()

    def test_local_image_resolvable(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "local-img")
        _write_md(task, "# Title\n\n![fig](images/fig1.png)\n\nSome text here.")
        (task / "images").mkdir()
        (task / "images" / "fig1.png").write_bytes(b"fake-png-data")
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        img = [r for r in report.results if r.name == "image_refs"]
        assert img[0].severity == CheckSeverity.OK

    def test_missing_local_image_error(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "missing-img")
        _write_md(task, "# Title\n\n![fig](images/nonexistent.png)\n\nSome text.")
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        assert not report.should_proceed
        img = [r for r in report.results if r.name == "image_refs"]
        assert img[0].severity == CheckSeverity.ERROR
        assert "1/1" in img[0].message

    def test_remote_url_accepted(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "remote-img")
        _write_md(
            task,
            "# Title\n\n![fig](https://example.com/img.jpg)\n\nSome text content.",
        )
        _write_layout(task, {"pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        img = [r for r in report.results if r.name == "image_refs"]
        assert img[0].severity == CheckSeverity.OK


class TestLayoutCheck:
    def test_no_layout_info(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "no-layout")
        # Remove layout_json from products
        meta = json.loads((task / "meta.json").read_text(encoding="utf-8"))
        meta["products"]["layout_json"] = None
        (task / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        _write_md(task, "# Title\n\nContent here.")
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        layout = [r for r in report.results if r.name == "layout"]
        assert layout[0].severity == CheckSeverity.INFO

    def test_valid_layout(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "good-layout")
        _write_md(task, "# Title\n\n![fig](images/fig1.png)\n\nContent.")
        (task / "images").mkdir()
        (task / "images" / "fig1.png").write_bytes(b"data")
        _write_layout(task, {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [
                        {"type": "text", "content": "Title"},
                        {"type": "image", "image_url": "images/fig1.png"},
                    ],
                },
            ]
        })
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        layout = [r for r in report.results if r.name == "layout"]
        assert layout[0].severity == CheckSeverity.OK
        assert layout[0].details["page_count"] == 1

    def test_layout_missing_pages_error(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "bad-layout")
        _write_md(task, "# Title\n\nContent here.")
        _write_layout(task, {"not_pages": []})
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        layout = [r for r in report.results if r.name == "layout"]
        assert layout[0].severity == CheckSeverity.ERROR

    def test_layout_page_missing_blocks_warning(self, tmp_path: Path) -> None:
        task = _make_task_dir(tmp_path, "partial-layout")
        _write_md(task, "# Title\n\nContent here.")
        _write_layout(task, {
            "pages": [
                {"page_idx": 0, "blocks": []},
                {"page_idx": 1},  # missing blocks
            ]
        })
        checker = PreFlightChecker(tmp_path)
        report = checker.check(task)
        layout = [r for r in report.results if r.name == "layout"]
        assert layout[0].severity == CheckSeverity.WARNING


class TestFixtureIntegration:
    def test_sample_fixture_passes(self) -> None:
        """The shipped sample fixture should pass pre-flight checks."""
        checker = PreFlightChecker(FIXTURE_DIR)
        report = checker.check(TASK_DIR)
        assert report.should_proceed, (
            "Expected fixture to pass, but got errors:\n"
            + "\n".join(f"  [{e.name}] {e.message}" for e in report.errors)
        )

    def test_check_task_convenience(self) -> None:
        report = check_task(TASK_DIR)
        assert report.task_id == "g8-triangle-ch03"
        assert len(report.results) >= 4  # structure, content_load, content, image_refs, layout

    def test_nonexistent_dir_raises(self, tmp_path: Path) -> None:
        checker = PreFlightChecker(tmp_path)
        with pytest.raises(PreFlightError):
            checker.check(tmp_path / "does-not-exist")


class TestPipelineIntegration:
    """Verify that PipelineRunner.run() invokes pre-flight checks."""

    def test_preflight_blocks_bad_task(self, tmp_path: Path) -> None:
        """A task with missing markdown should be caught before workspace creation."""
        from pdf2dt.assets import LocalMirrorDownloader

        task = tmp_path / "inbox" / "bad-task"
        task.mkdir(parents=True)
        (task / "meta.json").write_text(
            json.dumps(
                {
                    "task_id": "bad-task",
                    "source": {"original_filename": "x.pdf", "sha256": "0" * 64},
                    "minerU": {"version": "1", "exported_at": "2026-01-01"},
                    "products": {"markdown": "missing.md"},
                }
            ),
            encoding="utf-8",
        )
        project_root = tmp_path / "projects"

        from pdf2dt.pipeline import PipelineRunner

        runner = PipelineRunner(LocalMirrorDownloader(tmp_path / "mirror"))
        with pytest.raises(PreFlightFailureError) as exc_info:
            runner.run(
                project_root=project_root,
                inbox_task_dir=task,
                project_id="test-pf",
                title="test",
            )
        # Verify no workspace was created
        assert not (project_root / "test-pf").exists()
        # Verify the report is accessible
        assert exc_info.value.report is not None
        assert not exc_info.value.report.should_proceed

    def test_preflight_can_be_disabled(self, tmp_path: Path) -> None:
        """Setting preflight=False skips the check (for testing/debugging)."""
        from pdf2dt.assets import LocalMirrorDownloader

        task = tmp_path / "inbox" / "no-preflight"
        task.mkdir(parents=True)
        # No meta.json at all — would fail preflight
        project_root = tmp_path / "projects"

        from pdf2dt.pipeline import PipelineRunner

        runner = PipelineRunner(LocalMirrorDownloader(tmp_path / "mirror"))
        # With preflight=False, it should get past preflight and fail at Stage 0/1
        # with a different error (InboxLoader/ValidationError), not PreFlightFailureError.
        with pytest.raises(Exception) as exc_info:
            runner.run(
                project_root=project_root,
                inbox_task_dir=task,
                project_id="test-nopf",
                title="test",
                preflight=False,
            )
        assert not isinstance(exc_info.value, PreFlightFailureError)
