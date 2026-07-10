"""Tests for :mod:`pdf2dt.bookview` (Stage 3 — BookView builder).

Domain-neutral port of the upstream ``test_bookview.py``. The upstream
suite runs against a pre-built ``projects/demo-g8-triangle`` workspace
plus the private ``elementary-math-v1.yaml`` outline; here we build the
workspace end-to-end from the in-repo ``demos/inbox-sample/g8-triangle-ch03``
MinerU fixture and the domain-neutral ``outlines/sample-outline-v1.yaml``,
and assert on the builder's generic contracts (tree shape, fingerprints,
manifest, topic inheritance) rather than subject-specific counts.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pdf2dt.bookview import (
    BookItem,
    BookView,
    BookViewBuildError,
    build_book_view,
)
from pdf2dt.outlining.items import extract_items
from pdf2dt.outlining.matcher import match_project
from pdf2dt.project import ProjectWorkspace, create_workspace, load_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INBOX = ROOT / "demos" / "inbox-sample" / "g8-triangle-ch03"
FIXTURE_MD = FIXTURE_INBOX / "full.md"
OUTLINE = ROOT / "outlines" / "sample-outline-v1.yaml"


def _build_demo_workspace(tmp_path: Path, *, with_outline: bool = True) -> Path:
    """Run Stages 0-2-4b-3 on the in-repo fixture and return the workspace root."""
    ws_root = tmp_path / "demo-bookview"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_pipeline.py"),
        "--project-root",
        str(ws_root),
        "--inbox",
        str(FIXTURE_INBOX),
        "--project-id",
        "demo-bookview",
        "--title",
        "BookView Demo",
        "--downloader",
        "local",
        "--mirror",
        str(FIXTURE_INBOX / "images"),
        "--book-view",
    ]
    if with_outline:
        cmd += ["--outline", str(OUTLINE)]
    subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    return ws_root


def _iter_items(book: BookView):
    for chapter in book.chapters:
        for section in chapter.sections:
            yield from section.items


def _all_items(book: BookView) -> list[BookItem]:
    return list(_iter_items(book))


# ---------------------------------------------------------------------- #
# Pure unit test — missing-input guard
# ---------------------------------------------------------------------- #


def test_bookview_build_error_on_missing_inputs(tmp_path: Path) -> None:
    """``build_book_view`` raises ``BookViewBuildError`` when
    ``normalized/full.md`` is absent."""
    ws = create_workspace(
        tmp_path / "demo-empty",
        project_id="demo-empty",
        title="Empty",
        subject="general",
        stage="any",
    )
    md = ws.normalized_dir / "full.md"
    if md.exists():
        md.unlink()

    with pytest.raises(BookViewBuildError) as excinfo:
        build_book_view(ws)
    assert "missing required input: normalized/full.md" in str(excinfo.value)


# ---------------------------------------------------------------------- #
# End-to-end: build against the in-repo fixture (with outline)
# ---------------------------------------------------------------------- #


def test_bookview_builds_on_fixture(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path, with_outline=True)
    ws = load_workspace(ws_root)
    book = build_book_view(ws)

    # 1. At least one chapter is produced.
    assert len(book.chapters) >= 1, "expected at least 1 chapter"

    # 2. Total item count matches extract_items() on the normalized markdown.
    items_in_view = _all_items(book)
    items_from_extractor = extract_items(FIXTURE_MD.read_text(encoding="utf-8"))
    assert len(items_in_view) == len(items_from_extractor), (
        f"item count mismatch: view={len(items_in_view)} "
        f"extractor={len(items_from_extractor)}"
    )

    # 3. Serialization contract.
    payload = book.to_dict()
    assert payload["schema_version"] == "book_view/v1"
    assert payload["book_id"] == "demo-bookview"
    fingerprints = payload["fingerprints"]
    assert set(fingerprints) == {
        "normalized",
        "layout",
        "assets",
        "assignments",
    }
    for value in fingerprints.values():
        assert isinstance(value, str) and len(value) == 64


def test_bookview_persists_and_manifest(tmp_path: Path) -> None:
    ws_root = _build_demo_workspace(tmp_path, with_outline=True)
    ws = load_workspace(ws_root)
    build_book_view(ws)

    book_view_path = ws.book_view_dir / "book_view.json"
    assert book_view_path.is_file(), f"missing {book_view_path}"

    payload = json.loads(book_view_path.read_text(encoding="utf-8"))
    assert payload["book_id"] == ws.root.name
    assert payload["schema_version"] == "book_view/v1"

    manifest = ws.load_manifest()
    stage = manifest["stages"].get("stage3_book_view")
    assert stage is not None, (
        f"manifest missing stage3_book_view; stages={sorted(manifest.get('stages', {}))}"
    )
    assert stage["status"] == "completed"
    metadata = stage.get("metadata") or {}
    expected_items = len(extract_items(FIXTURE_MD.read_text(encoding="utf-8")))
    assert metadata.get("items") == expected_items, (
        f"stage3 items={metadata.get('items')} expected {expected_items}"
    )
    assert metadata.get("book_id") == "demo-bookview"


# ---------------------------------------------------------------------- #
# Topic inheritance: Stage 4b -> Stage 3
# ---------------------------------------------------------------------- #


def test_bookview_inherits_topic_assignments(tmp_path: Path) -> None:
    """Run ``match_project`` (Stage 4b) then ``build_book_view`` (Stage 3)
    against a synthetic workspace and verify that BookItems carry the
    Stage 4b topic routing. The specific topic is discovered at runtime
    so the test does not hard-code a subject-specific id.
    """
    project_root = tmp_path / "demo-stage4b"
    ws = create_workspace(
        project_root,
        project_id="demo-stage4b",
        title="Stage 4b + Stage 3 smoke",
        subject="general",
        stage="any",
    )
    ws.normalized_dir.mkdir(parents=True, exist_ok=True)
    target_md = ws.normalized_dir / "full.md"
    target_md.write_text(FIXTURE_MD.read_text(encoding="utf-8"), encoding="utf-8")

    # Discover which item_ids route to a non-misc topic under the outline.
    from pdf2dt.outlining.outline import OutlineLoader
    from pdf2dt.outlining.matcher import OutlineMatcher

    items_inline = extract_items(target_md.read_text(encoding="utf-8"))
    outline = OutlineLoader().load(OUTLINE)
    matcher = OutlineMatcher(outline, min_score=1, max_topics_per_item=4)
    asgs_inline, _ = matcher.match(items_inline)
    routed_topics = {
        tid for a in asgs_inline for tid in a.topic_ids if tid != "_misc"
    }
    assert routed_topics, (
        "smoke run found no items routed to a non-misc topic; "
        "the outline / fixture may have drifted"
    )

    # Persist Stage 4b assignments, then build minimal layout + assets.
    match_project(ws, str(OUTLINE), markdown_path=target_md)
    layout_payload = {
        "task_id": "stage4b-smoke",
        "pages": [{"page_index": 0, "page_number": 1, "blocks": []}],
    }
    (ws.normalized_dir / "layout.localized.json").write_text(
        json.dumps(layout_payload, ensure_ascii=False), encoding="utf-8"
    )
    (ws.normalized_dir / "assets_registry.json").write_text(
        json.dumps({"count": 0, "by_url": {}, "assets": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    book = build_book_view(ws)
    topic_hits = [item for item in _all_items(book) if set(item.topic_ids) & routed_topics]
    assert topic_hits, (
        "expected at least one BookItem to inherit a Stage 4b topic; "
        f"observed item topic_ids={[i.topic_ids for i in _all_items(book)]}"
    )


# ---------------------------------------------------------------------- #
# Robustness when Stage 4b has not run
# ---------------------------------------------------------------------- #


def test_bookview_handles_missing_topic_assignments(tmp_path: Path) -> None:
    """Without ``topic_assignments/assignments.json`` (no --outline) every
    item must carry ``topic_ids == []`` and ``review_state == 'unassigned'``."""
    ws_root = _build_demo_workspace(tmp_path, with_outline=False)
    ws = load_workspace(ws_root)
    book = build_book_view(ws)
    assert book.chapters, "expected at least one chapter"
    for item in _all_items(book):
        assert item.topic_ids == [], (
            f"{item.item_id} expected empty topic_ids (no Stage 4b), got {item.topic_ids!r}"
        )
        assert item.assignment_review_state == "unassigned", (
            f"{item.item_id} expected review_state 'unassigned', "
            f"got {item.assignment_review_state!r}"
        )
