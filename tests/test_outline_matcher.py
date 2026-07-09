"""Tests for :class:`OutlineMatcher` and ``match_project``.

The matcher takes the items extracted by ``extract_items`` and assigns
each one to one or more outline leaves using vocabulary scoring.
Items that match nothing fall into the synthetic ``_misc`` topic; the
report surfaces them as ``unclassified_items``.

This file covers both the end-to-end path (real sample outline + real
sample markdown) and focused unit cases (priority tie-breaking,
``max_topics_per_item`` capping, and the ``_misc`` fallback) built
from synthetic :class:`Item` / :class:`Outline` objects.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2dt.outlining.items import Item, extract_items
from pdf2dt.outlining.matcher import OutlineMatcher, match_project
from pdf2dt.outlining.outline import Outline, OutlineLoader, Topic, VocabularyEntry
from pdf2dt.project import create_workspace

ROOT = Path(__file__).resolve().parents[1]
OUTLINE_PATH = ROOT / "outlines" / "sample-outline-v1.yaml"
FIXTURE_MD = ROOT / "demos" / "inbox-sample" / "sample-chapter" / "full.md"


@pytest.fixture
def outline() -> Outline:
    return OutlineLoader().load(OUTLINE_PATH)


@pytest.fixture
def items() -> list[Item]:
    return extract_items(FIXTURE_MD.read_text(encoding="utf-8"))


@pytest.fixture
def matcher(outline: Outline) -> OutlineMatcher:
    return OutlineMatcher(outline, min_score=1, max_topics_per_item=4)


@pytest.fixture
def assignments(matcher: OutlineMatcher, items: list[Item]):
    # ``match`` returns ``(list[TopicAssignment], MatchReport)``; the
    # tests below only need the assignment list.
    return matcher.match(items)[0]


# ---------------------------------------------------------------------- #
# Fixture-level (end-to-end) coverage
# ---------------------------------------------------------------------- #


def test_total_items_matches_input(assignments: list, items: list[Item]) -> None:
    assert len(assignments) == len(items) == 14


def test_unmatched_items_fall_to_misc(
    assignments: list, matcher: OutlineMatcher
) -> None:
    unclassified = [
        a.item_id for a in assignments if a.topic_ids == [matcher.MISC_TOPIC]
    ]
    assert set(unclassified) == {"item-0002", "item-0005", "item-0007"}
    report = matcher.match(extract_items(FIXTURE_MD.read_text(encoding="utf-8")))[1]
    assert set(report.unclassified_items) == set(unclassified)


def test_definition_maps_to_definitions(assignments: list) -> None:
    definition = next(a for a in assignments if a.item_id == "item-0003")
    assert definition.topic_ids == ["definitions"]
    assert definition.review_state == "confirmed"


def test_exercises_map_to_exercises(assignments: list) -> None:
    ex_ids = {"item-0010", "item-0011"}
    matched = [a for a in assignments if a.item_id in ex_ids]
    # ``topic_ids`` is a list, so compare as tuples inside the set.
    assert {tuple(a.topic_ids) for a in matched} == {("exercises",)}


def test_knowledge_point_marker_is_recognized(assignments: list) -> None:
    kp = next(a for a in assignments if a.item_id == "item-0014")
    assert kp.topic_ids == ["knowledge-points"]


def test_multi_topic_item_gets_several_assignments(assignments: list) -> None:
    summary = next(a for a in assignments if a.item_id == "item-0013")
    assert set(summary.topic_ids) == {"summary", "methods", "definitions"}
    # Every chosen topic must carry match evidence.
    assert all(d.score >= 1 for d in summary.match_details)


# ---------------------------------------------------------------------- #
# Synthetic unit coverage
# ---------------------------------------------------------------------- #


def _make_outline(leaves: list[Topic], vocab: dict) -> Outline:
    return Outline(
        outline_id="synthetic",
        name="Synthetic",
        version="0.0.0",
        applies_to={},
        topics=tuple(leaves),
        vocabulary=vocab,
    )


def test_higher_priority_wins_tie() -> None:
    outline = _make_outline(
        [Topic(id="low", label="Low"), Topic(id="high", label="High")],
        {
            "low": VocabularyEntry(keywords=("苹果",), priority=1),
            "high": VocabularyEntry(keywords=("苹果",), priority=5),
        },
    )
    item = Item(item_id="i1", item_type="other", title="苹果", text="苹果")
    # Cap at 1 topic so the priority tie-break decides the sole winner.
    result = OutlineMatcher(outline, max_topics_per_item=1).match([item])
    assert result[0][0].topic_ids == ["high"]


def test_max_topics_per_item_is_capped() -> None:
    leaves = [Topic(id=f"t{i}", label=f"T{i}") for i in range(6)]
    vocab = {f"t{i}": VocabularyEntry(keywords=("共同",), priority=i) for i in range(6)}
    outline = _make_outline(leaves, vocab)
    item = Item(item_id="i", item_type="other", title="共同", text="共同")
    result = OutlineMatcher(outline, max_topics_per_item=3).match([item])
    assert len(result[0][0].topic_ids) == 3
    # The three highest-priority leaves win.
    assert result[0][0].topic_ids == ["t5", "t4", "t3"]


def test_no_vocabulary_match_falls_to_misc() -> None:
    outline = _make_outline(
        [Topic(id="x", label="X")],
        {"x": VocabularyEntry(keywords=("不存在的词",))},
    )
    item = Item(item_id="i", item_type="other", title="无关", text="无关内容")
    assignments, report = OutlineMatcher(outline).match([item])
    assert assignments[0].topic_ids == ["_misc"]
    assert report.unclassified_items == ["i"]


def test_empty_vocabulary_leaf_is_skipped() -> None:
    # A leaf with empty vocabulary must never claim an item even when
    # another leaf matches.
    outline = _make_outline(
        [Topic(id="empty", label="Empty"), Topic(id="hit", label="Hit")],
        {"hit": VocabularyEntry(keywords=("命中",))},
    )
    item = Item(item_id="i", item_type="other", title="命中", text="命中")
    result = OutlineMatcher(outline).match([item])
    assert result[0][0].topic_ids == ["hit"]


# ---------------------------------------------------------------------- #
# match_project integration (writes artifacts + records manifest stage)
# ---------------------------------------------------------------------- #


def test_match_project_writes_artifacts_and_records_stage(tmp_path: Path) -> None:
    ws = create_workspace(
        tmp_path / "proj",
        project_id="p",
        title="t",
        subject="general",
        stage="any",
    )
    (ws.normalized_dir / "full.md").write_text(
        FIXTURE_MD.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assignments, report = match_project(ws, OUTLINE_PATH)

    assert (ws.topic_assignments_dir / "assignments.json").is_file()
    assert (ws.reports_dir / "topic_assignment_report.json").is_file()

    payload = json.loads(
        (ws.topic_assignments_dir / "assignments.json").read_text(encoding="utf-8")
    )
    assert payload["outline_id"] == "sample-outline"
    assert len(payload["assignments"]) == len(assignments)

    manifest = ws.load_manifest()
    stage = manifest["stages"]["stage4b_outline"]
    assert stage["status"] == "completed"
    assert stage["metadata"]["outline_id"] == "sample-outline"
    assert report.total_items == 14
