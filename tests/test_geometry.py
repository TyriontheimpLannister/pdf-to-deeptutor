"""Tests for Stage 5 — geometry analysis and Stage 6 — review."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pdf2dt.assets import LocalMirrorDownloader
from pdf2dt.bookview.builder import BookItem
from pdf2dt.geometry import (
    INCLUDABLE_REVIEW_STATES,
    NON_PROMOTABLE_EVIDENCE,
    PROMOTABLE_EVIDENCE,
    Evidence,
    GeometryAnalyzer,
    GeometryFigure,
    GeometryRelation,
    HybridGeometryAnalyzer,
    MiniMaxM3Provider,
    RelationType,
    ReviewState,
    analyze_geometry,
    relation_key,
)
from pdf2dt.geometry.analyzer import _extract_points, _extract_segments
from pdf2dt.geometry.vlm import (
    VlmCallRecord,
    VlmRelationCandidate,
    VlmResponse,
    should_call_vlm,
)
from pdf2dt.pipeline import PipelineRunner
from pdf2dt.project import ProjectWorkspace, is_stage_completed
from pdf2dt.review import (
    PromotionError,
    ReviewAction,
    ReviewDecision,
    ReviewStateStore,
    apply_review,
)
from pdf2dt.review.store import _ACTION_TO_STATE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INBOX = PROJECT_ROOT / "demos/inbox-sample" / "g8-triangle-ch03"
OUTLINE = PROJECT_ROOT / "outlines" / "elementary-math-v1.yaml"


# ---------------------------------------------------------------------- #
# Pure model / evidence tests
# ---------------------------------------------------------------------- #


def test_relation_key_stable_and_case_insensitive() -> None:
    k1 = relation_key(RelationType.PARALLEL, ["AB", "CD"])
    k2 = relation_key(RelationType.PARALLEL, ["cd", "ab"])
    # The case-folded form is the canonical key.
    assert k1 == k2 == "parallel::ab+cd"


def test_evidence_partition_disjoint() -> None:
    assert PROMOTABLE_EVIDENCE.isdisjoint(NON_PROMOTABLE_EVIDENCE)
    # Every evidence is in exactly one set.
    from pdf2dt.geometry.evidence import Evidence

    union = PROMOTABLE_EVIDENCE | NON_PROMOTABLE_EVIDENCE
    assert {e.value for e in Evidence} == {e.value for e in union}


def test_includable_review_states_are_confirmed_or_corrected() -> None:
    expected = {ReviewState.CONFIRMED, ReviewState.CORRECTED}
    assert frozenset(expected) == INCLUDABLE_REVIEW_STATES


def test_geometry_relation_round_trip() -> None:
    rel = GeometryRelation(
        type=RelationType.PARALLEL,
        entities=["AB", "CD"],
        evidence=Evidence.PROBLEM_TEXT,
        source_reference="item-1",
        confidence=0.9,
    )
    restored = GeometryRelation.from_dict(rel.to_dict())
    assert restored.type == rel.type
    assert restored.entities == rel.entities
    assert restored.evidence == rel.evidence
    assert restored.confidence == rel.confidence


def test_geometry_figure_round_trip() -> None:
    fig = GeometryFigure(
        figure_id="fig-abc",
        asset_id="asset-1",
        associated_item_id="item-1",
        points=["A", "B", "C"],
        segments=["AB", "BC", "AC"],
        relations=[
            GeometryRelation(
                type=RelationType.PARALLEL,
                entities=["AB", "CD"],
                evidence=Evidence.PROBLEM_TEXT,
            )
        ],
    )
    restored = GeometryFigure.from_dict(fig.to_dict())
    assert restored.figure_id == fig.figure_id
    assert restored.asset_id == fig.asset_id
    assert restored.points == fig.points
    assert restored.segments == fig.segments
    assert len(restored.relations) == 1
    assert restored.relations[0].key == fig.relations[0].key


# ---------------------------------------------------------------------- #
# Point / segment extraction
# ---------------------------------------------------------------------- #


def test_extract_points_picks_triangle_labels() -> None:
    text = r"在 $\triangle ABC$ 中, $D$ 在线段 $AB$ 上"
    points = _extract_points(text)
    for p in ("A", "B", "C", "D"):
        assert p in points, points


def test_extract_points_deduplicates_in_source_order() -> None:
    text = "A B C A D B"
    assert _extract_points(text) == ["A", "B", "C", "D"]


def test_extract_segments_only_paired_known_points() -> None:
    text = "AB = CD, EF unknown, AD"
    points = ["A", "B", "C", "D", "E", "F"]
    segs = _extract_segments(text, points)
    # EF is valid (both known points), AB and AD too. CD is valid.
    assert "AB" in segs
    assert "AD" in segs
    assert "CD" in segs
    assert "EF" in segs
    # We should never have segments with identical endpoints.
    for s in segs:
        assert s[0] != s[1]


# ---------------------------------------------------------------------- #
# Analyzer
# ---------------------------------------------------------------------- #


def _item(text: str) -> BookItem:
    return BookItem(
        item_id="i-1",
        item_type="definition",
        title="Test",
        text=text,
        chapter_path=(),
    )


def _fake_png(path: Path, *, width: int = 32, height: int = 32) -> None:
    """Write a tiny real PNG so the resource gate accepts the fixture."""
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    image.save(path, format="PNG")


def test_analyzer_returns_none_without_asset() -> None:
    fig = GeometryAnalyzer().analyze(
        item=_item("AB 平行 CD"), asset_id=""
    )
    assert fig is None


def test_analyzer_extracts_parallel_with_problem_text_evidence() -> None:
    fig = GeometryAnalyzer().analyze(
        item=_item(r"在 $\triangle ABC$ 中, AB $\parallel$ CD"),
        asset_id="asset-1",
    )
    assert fig is not None
    par = [r for r in fig.relations if r.type == RelationType.PARALLEL]
    assert par, fig.relations
    assert all(r.evidence == Evidence.PROBLEM_TEXT for r in par)
    for r in par:
        assert r.review_state == ReviewState.UNREVIEWED


def test_analyzer_marks_caption_only_as_diagram_mark() -> None:
    fig = GeometryAnalyzer().analyze(
        item=_item("说明三角形 ABC"),
        asset_id="asset-1",
        caption="AB ∥ CD",
    )
    assert fig is not None
    par = [r for r in fig.relations if r.type == RelationType.PARALLEL]
    assert par, fig.relations
    for r in par:
        assert r.evidence in {
            Evidence.DIAGRAM_MARK,
            Evidence.PROBLEM_TEXT_AND_DIAGRAM_MARK,
        }


def test_analyzer_marks_text_plus_caption_as_combined_evidence() -> None:
    fig = GeometryAnalyzer().analyze(
        item=_item(r"在 $\triangle ABC$ 中, AB $\parallel$ CD"),
        asset_id="asset-1",
        caption="AB // CD (highlighted)",
    )
    par = [r for r in fig.relations if r.type == RelationType.PARALLEL]
    assert par
    assert any(
        r.evidence == Evidence.PROBLEM_TEXT_AND_DIAGRAM_MARK for r in par
    ), par


def test_analyzer_figure_id_is_stable() -> None:
    item = _item("x")
    a = GeometryAnalyzer().analyze(item=item, asset_id="a1")
    b = GeometryAnalyzer().analyze(item=item, asset_id="a1")
    assert a is not None and b is not None
    assert a.figure_id == b.figure_id


def test_hybrid_analyzer_keeps_rules_and_adds_review_only_vlm_relation(
    tmp_path: Path,
) -> None:
    class FakeProvider:
        name = "fake"

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            return VlmResponse(
                relations=[
                    VlmRelationCandidate(
                        relation_type=RelationType.MIDPOINT,
                        entities=["AB", "D"],
                        confidence=0.88,
                        observation="D is visually marked at the center of AB",
                    )
                ]
            )

    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"not decoded by the fake provider")
    # Rules came back empty so the selection strategy says "call".
    analyzer = HybridGeometryAnalyzer(provider=FakeProvider())
    figure = analyzer.analyze(
        item=_item(""),
        asset_id="asset-1",
        asset_path=image_path,
    )

    assert figure is not None
    midpoint = next(
        relation for relation in figure.relations if relation.type == RelationType.MIDPOINT
    )
    assert midpoint.evidence == Evidence.VISUAL_INFERENCE
    assert midpoint.review_state == ReviewState.UNREVIEWED
    assert midpoint.source_reference == "vlm:fake"
    assert len(analyzer.call_records) == 1


def test_hybrid_analyzer_skips_paid_call_when_rules_sufficient(
    tmp_path: Path,
) -> None:
    class CountingProvider:
        name = "fake"
        calls = 0

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            CountingProvider.calls += 1
            return VlmResponse(relations=[])

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    analyzer = HybridGeometryAnalyzer(provider=CountingProvider())
    figure = analyzer.analyze(
        item=_item(r"在 $\triangle ABC$ 中，AB \parallel CD"),
        asset_id="asset-1",
        asset_path=image_path,
    )

    assert figure is not None
    assert any(relation.evidence == Evidence.PROBLEM_TEXT for relation in figure.relations)
    assert CountingProvider.calls == 0
    assert analyzer.call_records[0].status == "skipped"
    assert analyzer.call_records[0].skip_reason == "rules_sufficient"


def test_hybrid_analyzer_falls_back_to_rules_when_vlm_fails(tmp_path: Path) -> None:
    class FailingProvider:
        name = "fake"

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            return VlmResponse(error="timeout")

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    analyzer = HybridGeometryAnalyzer(provider=FailingProvider())
    # Empty item text means rules come back blank, so the VLM is
    # called. The failure must still produce a clean rules figure.
    figure = analyzer.analyze(
        item=_item(""),
        asset_id="asset-1",
        asset_path=image_path,
    )

    assert figure is not None
    assert "timeout" in " ".join(figure.visual_observations)
    assert analyzer.call_records[0].status == "failed"
    assert analyzer.call_records[0].error == "timeout"


def test_hybrid_analyzer_no_key_falls_back_to_rules(tmp_path: Path) -> None:
    """Codex P1 acceptance: a hybrid run with no API key must not
    contact a provider and must still produce a clean rules-only
    figure whose relations are reviewable.
    """
    # Guarantee the no-key code path: drop both env keys if the
    # host has them set, and construct the provider with no
    # explicit key.
    for name in ("MINIMAX_API_KEY", "SENSENOVA_API_KEY"):
        os.environ.pop(name, None)
    provider = MiniMaxM3Provider()  # api_key=None, env unset
    assert provider._api_key in (None, "")

    # Item text is empty so rules come back blank, which forces
    # should_call_vlm() to say "call" — exercising the no-key
    # branch inside MiniMaxM3Provider.analyze_image.
    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    analyzer = HybridGeometryAnalyzer(provider=provider)
    figure = analyzer.analyze(
        item=_item(""),
        asset_id="asset-1",
        asset_path=image_path,
    )
    assert figure is not None
    # The audit log records the no-key failure and no relation on
    # the figure is VLM-sourced (the provider never returned a
    # parseable response).
    assert analyzer.call_records[0].status == "failed"
    assert "API_KEY" in (analyzer.call_records[0].error or "")
    for rel in figure.relations:
        assert not rel.source_reference.startswith("vlm:"), (
            f"VLM ran without a key: {rel.source_reference}"
        )


# ---------------------------------------------------------------------- #
# P1 safe fallback — malformed VLM output must not abort Stage 5
# ---------------------------------------------------------------------- #


def test_parse_response_handles_nonnumeric_confidence() -> None:
    response = _parse_response_module(
        json.dumps(
            {
                "relations": [
                    {
                        "type": "parallel",
                        "entities": ["AB", "CD"],
                        "confidence": "high",
                        "observation": "model spoke prose",
                    },
                    {
                        "type": "perpendicular",
                        "entities": ["EF", "GH"],
                        "confidence": 0.4,
                    },
                ]
            }
        )
    )
    assert response.error.startswith("discarded 1")
    assert len(response.relations) == 1
    assert response.relations[0].relation_type == RelationType.PERPENDICULAR


def test_parse_response_handles_list_body() -> None:
    response = _parse_response_module(json.dumps([{"type": "parallel"}]))
    assert response.error == "response JSON must be an object"
    assert response.relations == []


def test_parse_response_handles_partial_valid_payload() -> None:
    response = _parse_response_module(
        json.dumps(
            {
                "relations": [
                    {"type": "parallel", "entities": ["AB"], "confidence": 0.9},
                    {"type": "not_a_real_type", "entities": ["CD"], "confidence": 0.5},
                    "not a dict",
                    {"type": "parallel"},
                ]
            }
        )
    )
    # Only the first record is kept; the rest are invalid for various
    # reasons.
    assert len(response.relations) == 1
    assert response.relations[0].entities == ["AB"]


def test_parse_response_handles_empty_text() -> None:
    response = _parse_response_module("")
    assert response.error == "empty response"
    assert response.relations == []


def _parse_response_module(text: str) -> VlmResponse:
    from pdf2dt.geometry.vlm import _parse_response

    return _parse_response(text)


def test_minimax_provider_returns_error_on_malformed_choices(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        # Anthropic-shape but content is a string, not a list.
        return httpx.Response(200, json={"content": "oops"})

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(api_key="test-key", client=client)
    response = provider.analyze_image(image_path, "triangle ABC")
    client.close()

    assert response.error == "MiniMax returned no text block"
    assert response.relations == []


def test_sensenova_provider_returns_error_on_missing_data_field(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    from pdf2dt.geometry.vlm import SenseNovaProvider

    provider = SenseNovaProvider(api_key="test-key", client=client)
    response = provider.analyze_image(image_path, "triangle ABC")
    client.close()

    assert response.error == "SenseNova returned no text"
    assert response.relations == []


def test_should_call_vlm_decisions() -> None:
    blank = GeometryFigure(
        figure_id="fig-blank",
        asset_id="asset-blank",
        associated_item_id="i-1",
    )
    assert should_call_vlm(blank) == (True, "rules_blank")

    only_visual = GeometryFigure(
        figure_id="fig-visual",
        asset_id="asset-visual",
        associated_item_id="i-1",
        visual_observations=["MIDPOINT"],
    )
    assert should_call_vlm(only_visual) == (True, "rules_blank_no_relations")

    confident = GeometryFigure(
        figure_id="fig-confident",
        asset_id="asset-confident",
        associated_item_id="i-1",
        relations=[
            GeometryRelation(
                type=RelationType.PARALLEL,
                entities=["AB", "CD"],
                evidence=Evidence.PROBLEM_TEXT,
                confidence=0.9,
            )
        ],
    )
    assert should_call_vlm(confident) == (False, "rules_sufficient")

    non_promotable = GeometryFigure(
        figure_id="fig-np",
        asset_id="asset-np",
        associated_item_id="i-1",
        relations=[
            GeometryRelation(
                type=RelationType.PARALLEL,
                entities=["AB", "CD"],
                evidence=Evidence.VISUAL_INFERENCE,
                confidence=0.6,
            )
        ],
    )
    assert should_call_vlm(non_promotable) == (True, "rules_only_non_promotable")


def test_hybrid_analyzer_records_per_call_metadata(tmp_path: Path) -> None:
    class FakeProvider:
        name = "fake"
        model = "fake-model-v1"
        endpoint = "https://example.test/v1/anthropic"

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            return VlmResponse(
                relations=[
                    VlmRelationCandidate(
                        relation_type=RelationType.PARALLEL,
                        entities=["AB", "CD"],
                        confidence=0.7,
                        observation="visual hint",
                    )
                ],
                raw_response='{"relations":[{"type":"parallel","entities":["AB","CD"],"confidence":0.7}]}',
            )

    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"png-bytes")
    analyzer = HybridGeometryAnalyzer(
        provider=FakeProvider(),
        raw_responses_dir=tmp_path / "raw",
    )
    figure = analyzer.analyze(
        item=_item(""),
        asset_id="asset-1",
        asset_path=image_path,
    )
    assert figure is not None
    record = analyzer.call_records[0]
    assert isinstance(record, VlmCallRecord)
    assert record.status == "ok"
    assert record.model == "fake-model-v1"
    assert record.endpoint == "https://example.test/v1/anthropic"
    assert record.asset_sha256
    assert record.response_sha256
    assert record.elapsed_ms >= 0
    raw_path = Path(record.raw_response_path)
    assert raw_path.is_file()
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["provider"] == "fake"
    assert payload["raw_response"]


def test_hybrid_analyzer_records_conflict_observation(tmp_path: Path) -> None:
    """P1 #2 regression: when rules and VLM disagree on the same
    entity set (different relation types), the conflict must surface
    as an ``unknown``-evidence relation in the figure's relation
    list, not just as a buried string in ``visual_observations``.
    Without that, the review queue (which iterates
    ``figure.relations``) would never see the disagreement.

    We construct a custom rule whose default evidence is
    ``VISUAL_INFERENCE`` so :func:`should_call_vlm` says "call" even
    though rules produced a relation, and the VLM returns a
    contradictory type for the same entities.
    """
    import re as _re

    custom_rules = [
        (
            RelationType.PARALLEL,
            [
                (
                    _re.compile(r"AB\s*∥\s*CD|AB\s*平行\s*CD"),
                    Evidence.VISUAL_INFERENCE,
                )
            ],
        ),
    ]

    class ConflictingProvider:
        name = "fake"

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            return VlmResponse(
                relations=[
                    VlmRelationCandidate(
                        relation_type=RelationType.PERPENDICULAR,
                        entities=["AB"],
                        confidence=0.6,
                        observation="visual says perpendicular",
                    )
                ]
            )

    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"png-bytes")
    analyzer = HybridGeometryAnalyzer(
        provider=ConflictingProvider(),
        rules=custom_rules,
    )
    figure = analyzer.analyze(
        item=_item("AB 平行 CD"),
        asset_id="asset-1",
        asset_path=image_path,
    )

    assert figure is not None
    assert analyzer.call_records[0].status == "ok"

    # The original rule relation is preserved unchanged.
    rule_parallel = next(
        relation
        for relation in figure.relations
        if relation.type == RelationType.PARALLEL
        and not relation.source_reference.startswith("vlm:")
    )
    assert rule_parallel.evidence == Evidence.VISUAL_INFERENCE
    assert rule_parallel.entities == ["AB"]

    # The conflict candidate is now a real queue entry with
    # ``unknown`` evidence, not just a string in visual_observations.
    conflict = next(
        relation
        for relation in figure.relations
        if relation.evidence == Evidence.UNKNOWN
    )
    assert conflict.type == RelationType.PERPENDICULAR
    assert conflict.entities == ["AB"]
    assert conflict.source_reference == "vlm:fake:conflict"
    assert conflict.review_state == ReviewState.UNREVIEWED
    assert "rules said" in conflict.review_note
    assert "VLM says" in conflict.review_note

    # Codex P1 #2 follow-up: per (type, entities) the figure must
    # hold *exactly one* record.  The earlier two-pass implementation
    # added both a visual_inference and an unknown record with the
    # same key, so the reviewer's decision would shadow one of them
    # and leave the other unreviewed — leaving the figure blocked.
    conflict_key = conflict.key
    same_key_records = [
        rel for rel in figure.relations if rel.key == conflict_key
    ]
    assert len(same_key_records) == 1, (
        f"expected exactly one record for key {conflict_key!r}, "
        f"got {len(same_key_records)}"
    )
    assert same_key_records[0] is conflict
    # And there is no visual_inference record with the same key.
    assert not any(
        rel.evidence == Evidence.VISUAL_INFERENCE and rel.key == conflict_key
        for rel in figure.relations
    )

    # And the audit observation is still there for humans.
    assert any("conflict on" in obs for obs in figure.visual_observations)


def test_hybrid_analyzer_conflict_relation_lands_in_review_queue(
    tmp_path: Path,
) -> None:
    """End-to-end P1 #2: a conflict that survives Stage 5 must
    surface in the Stage 6 review queue, and
    :class:`PromotionError` must block any attempt to auto-confirm
    the ``unknown`` evidence relation."""
    import re as _re

    from pdf2dt.project import create_workspace

    custom_rules = [
        (
            RelationType.PARALLEL,
            [
                (
                    _re.compile(r"AB\s*∥\s*CD|AB\s*平行\s*CD"),
                    Evidence.VISUAL_INFERENCE,
                )
            ],
        ),
    ]

    class ConflictingProvider:
        name = "fake"

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            return VlmResponse(
                relations=[
                    VlmRelationCandidate(
                        relation_type=RelationType.PERPENDICULAR,
                        entities=["AB"],
                        confidence=0.6,
                    )
                ]
            )

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    analyzer = HybridGeometryAnalyzer(
        provider=ConflictingProvider(),
        rules=custom_rules,
    )
    figure = analyzer.analyze(
        item=_item("AB 平行 CD"),
        asset_id="asset-1",
        asset_path=image_path,
    )
    assert figure is not None
    conflict = next(
        rel for rel in figure.relations if rel.evidence == Evidence.UNKNOWN
    )
    assert conflict.review_state == ReviewState.UNREVIEWED

    # Persist to a real workspace so apply_review can read the queue.
    ws = create_workspace(tmp_path, project_id="conflict-e2e", title="t")
    queue_path = ws.review_dir / "geometry_figures.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": "geometry_figures/v1",
                "project_id": "conflict-e2e",
                "generated_at": "2026-07-11T00:00:00+00:00",
                "figures": [figure.to_dict()],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = ReviewStateStore(ws)
    queue = store.load_queue()
    conflict_key = next(
        rel.key
        for rel in queue[0].relations
        if rel.evidence == Evidence.UNKNOWN
    )

    # 1. ``unknown`` cannot be auto-confirmed.
    confirm = ReviewDecision(
        figure_id=queue[0].figure_id,
        relation_key=conflict_key,
        action=ReviewAction.CONFIRM,
    )
    with pytest.raises(PromotionError):
        store.apply([confirm])

    # 2. The review queue still surfaces the conflict after a
    # failed confirm — the on-disk file is unchanged.
    queue_after = store.load_queue()
    rel_after = next(
        rel
        for rel in queue_after[0].relations
        if rel.evidence == Evidence.UNKNOWN
    )
    assert rel_after.review_state == ReviewState.UNREVIEWED

    # 3. The reviewer can resolve the conflict with an explicit
    # ``corrected`` decision (evidence=unknown is still legal to
    # correct or reject — only auto-confirm is blocked).
    correct = ReviewDecision(
        figure_id=queue[0].figure_id,
        relation_key=conflict_key,
        action=ReviewAction.CORRECT,
        corrected_entities=["AB"],
        reviewer_note="manual: parallel, not perpendicular",
    )
    store.apply([correct])
    final = store.load_queue()
    rel_final = next(
        rel
        for rel in final[0].relations
        if rel.key == conflict_key
    )
    assert rel_final.review_state == ReviewState.CORRECTED
    assert rel_final.review_note == "manual: parallel, not perpendicular"

    # 4. Codex P1 #2 follow-up: after CORRECT, the figure no longer
    # holds an unreviewed visual_inference/unknown record with the
    # conflict key.  ``figure.relation(key)`` must return the
    # corrected record (and only that record) so the renderer block
    # list does not see a stale unreviewed entry.
    same_key_after = [
        rel
        for rel in final[0].relations
        if rel.key == conflict_key
    ]
    assert len(same_key_after) == 1
    assert same_key_after[0].review_state == ReviewState.CORRECTED
    assert same_key_after[0].evidence == Evidence.UNKNOWN
    # ``figure.relation(key)`` must resolve to that exact record so
    # future decisions land on the corrected entry.
    resolved = final[0].relation(conflict_key)
    assert resolved is same_key_after[0]
    # And the renderer's blocking predicate would skip this record
    # (review_state is "corrected" → not unreviewed).
    assert resolved.review_state.value in ("corrected", "confirmed")


def test_hybrid_analyzer_skips_vlm_when_rules_sufficient(
    tmp_path: Path,
) -> None:
    """When rules already gave a confident answer the conflict path
    cannot fire (VLM is skipped), and the rule relation survives
    unchanged in the queue.
    """

    class ConflictProvider:
        name = "fake"
        calls = 0

        def analyze_image(self, _image_path: Path, _context: str) -> VlmResponse:
            ConflictProvider.calls += 1
            return VlmResponse(
                relations=[
                    VlmRelationCandidate(
                        relation_type=RelationType.PERPENDICULAR,
                        entities=["AB", "CD"],
                        confidence=0.6,
                    )
                ]
            )

    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"png-bytes")
    analyzer = HybridGeometryAnalyzer(provider=ConflictProvider())
    figure = analyzer.analyze(
        item=_item(r"在 $\triangle ABC$ 中，AB \parallel CD"),
        asset_id="asset-1",
        asset_path=image_path,
    )
    assert figure is not None
    parallel = next(rel for rel in figure.relations if rel.type == RelationType.PARALLEL)
    assert parallel.evidence == Evidence.PROBLEM_TEXT
    assert ConflictProvider.calls == 0
    assert analyzer.call_records[0].status == "skipped"
    assert analyzer.call_records[0].skip_reason == "rules_sufficient"


def test_minimax_provider_uses_anthropic_image_messages(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": '{"relations": [], "observations": ["clear diagram"]}',
                    }
                ]
            },
        )

    image_path = tmp_path / "figure.png"
    _fake_png(image_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(api_key="test-key", client=client)
    response = provider.analyze_image(image_path, "triangle ABC")
    client.close()

    assert not response.error
    assert response.observations == ["clear diagram"]
    assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    image = payload["messages"][0]["content"][0]
    assert image["type"] == "image"
    assert image["source"]["type"] == "base64"


# ---------------------------------------------------------------------- #
# Review state store
# ---------------------------------------------------------------------- #


def _seed_workspace_with_geometry(tmp_path: Path) -> Path:
    """Write a tiny review/geometry_figures.json with three relations.

    Also creates a stub ``project.json`` so the workspace's
    ``exists()`` returns true.  The manifest is intentionally
    minimal: the geometry review state is the only thing the CLI
    and ``ReviewStateStore`` actually read.
    """
    # Stub manifest so ProjectWorkspace.exists() returns True.
    (tmp_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "project/v1",
                "project_id": "demo",
                "title": "Demo",
                "stages": {},
            }
        ),
        encoding="utf-8",
    )
    review_dir = tmp_path / "review"
    review_dir.mkdir(parents=True)
    payload = {
        "schema_version": "geometry_figures/v1",
        "project_id": "demo",
        "generated_at": "2026-07-10T00:00:00+00:00",
        "figures": [
            {
                "figure_id": "fig-1",
                "asset_id": "asset-1",
                "associated_item_id": "i-1",
                "points": ["A", "B", "C"],
                "segments": ["AB", "BC"],
                "relations": [
                    {
                        "type": "parallel",
                        "entities": ["AB", "BC"],
                        "evidence": "problem_text",
                        "source_reference": "i-1",
                        "confidence": 0.9,
                        "review_state": "unreviewed",
                        "review_note": "",
                    },
                    {
                        "type": "parallel",
                        "entities": ["AC"],
                        "evidence": "visual_inference",
                        "source_reference": "i-1",
                        "confidence": 0.4,
                        "review_state": "unreviewed",
                        "review_note": "",
                    },
                    {
                        "type": "parallel",
                        "entities": ["BC"],
                        "evidence": "unknown",
                        "source_reference": "i-1",
                        "confidence": 0.1,
                        "review_state": "unreviewed",
                        "review_note": "",
                    },
                ],
                "visual_observations": [],
                "review_state": "unreviewed",
            }
        ],
    }
    (review_dir / "geometry_figures.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return review_dir


def _stub_workspace(review_dir: Path) -> ProjectWorkspace:
    """Build a ProjectWorkspace rooted at *review_dir*'s parent.

    The workspace is created without going through
    :func:`load_workspace` because the test only needs a
    ProjectWorkspace that resolves paths; the review directory
    already exists on disk.
    """
    return ProjectWorkspace(review_dir.parent)


def test_confirm_promotable_evidence_succeeds(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-1",
        relation_key=relation_key(RelationType.PARALLEL, ["AB", "BC"]),
        action=ReviewAction.CONFIRM,
    )
    store.apply([decision])
    queue = store.load_queue()
    rel = queue[0].relations[0]
    assert rel.review_state == ReviewState.CONFIRMED


def test_confirm_non_promotable_evidence_raises(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-1",
        relation_key=relation_key(RelationType.PARALLEL, ["AC"]),
        action=ReviewAction.CONFIRM,
    )
    with pytest.raises(PromotionError):
        store.apply([decision])
    # The on-disk file must be unchanged.
    queue = store.load_queue()
    rel = next(
        r for r in queue[0].relations if r.key == relation_key(RelationType.PARALLEL, ["AC"])
    )
    assert rel.review_state == ReviewState.UNREVIEWED


def test_correct_non_promotable_evidence_succeeds(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-1",
        relation_key=relation_key(RelationType.PARALLEL, ["AC"]),
        action=ReviewAction.CORRECT,
        corrected_entities=["AD"],
        reviewer_note="changed to AD after manual review",
    )
    store.apply([decision])
    queue = store.load_queue()
    rel = next(
        r for r in queue[0].relations if r.key == relation_key(RelationType.PARALLEL, ["AD"])
    )
    assert rel.review_state == ReviewState.CORRECTED
    assert rel.review_note == "changed to AD after manual review"
    state = store.load_state()
    assert len(state) == 1
    assert state[0].action == ReviewAction.CORRECT


def test_reject_records_state(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-1",
        relation_key=relation_key(RelationType.PARALLEL, ["BC"]),
        action=ReviewAction.REJECT,
    )
    store.apply([decision])
    queue = store.load_queue()
    rel = next(
        r for r in queue[0].relations if r.key == relation_key(RelationType.PARALLEL, ["BC"])
    )
    assert rel.review_state == ReviewState.REJECTED


def test_apply_unknown_figure_raises(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-DOES-NOT-EXIST",
        relation_key="x",
        action=ReviewAction.CONFIRM,
    )
    with pytest.raises(PromotionError):
        store.apply([decision])


def test_apply_unknown_relation_raises(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    decision = ReviewDecision(
        figure_id="fig-1",
        relation_key="parallel::DOES+NOT+EXIST",
        action=ReviewAction.CONFIRM,
    )
    with pytest.raises(PromotionError):
        store.apply([decision])


def test_apply_overrides_later_decision_wins(tmp_path: Path) -> None:
    review_dir = _seed_workspace_with_geometry(tmp_path)
    ws = _stub_workspace(review_dir)
    store = ReviewStateStore(ws)
    key = relation_key(RelationType.PARALLEL, ["AB", "BC"])
    store.apply(
        [ReviewDecision(figure_id="fig-1", relation_key=key,
                        action=ReviewAction.REJECT)]
    )
    store.apply(
        [ReviewDecision(figure_id="fig-1", relation_key=key,
                        action=ReviewAction.CONFIRM)]
    )
    queue = store.load_queue()
    rel = next(r for r in queue[0].relations if r.key == key)
    assert rel.review_state == ReviewState.CONFIRMED
    state = store.load_state()
    assert len(state) == 1
    assert state[0].action == ReviewAction.CONFIRM


def test_action_to_state_mapping() -> None:
    assert _ACTION_TO_STATE[ReviewAction.CONFIRM] == ReviewState.CONFIRMED
    assert _ACTION_TO_STATE[ReviewAction.CORRECT] == ReviewState.CORRECTED
    assert _ACTION_TO_STATE[ReviewAction.REJECT] == ReviewState.REJECTED


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #


def test_review_cli_list_writes_md(tmp_path: Path) -> None:
    _seed_workspace_with_geometry(tmp_path)
    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "review.py"),
            "--project-root",
            str(tmp_path),
            "list",
            "--format",
            "md",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    out = tmp_path / "review" / "review_queue.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "Geometry review queue" in text
    assert "fig-1" in text


def test_review_cli_apply_confirms_promotable(tmp_path: Path) -> None:
    _seed_workspace_with_geometry(tmp_path)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {
                    "figure_id": "fig-1",
                    "relation_key": relation_key(
                        RelationType.PARALLEL, ["AB", "BC"]
                    ),
                    "action": "confirm",
                }
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "review.py"),
            "--project-root",
            str(tmp_path),
            "apply",
            "--decisions",
            str(decisions),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "applied 1 decision" in result.stdout, result.stdout
    state_path = tmp_path / "review" / "review_state.json"
    assert state_path.is_file()


def test_review_cli_apply_rejects_non_promotable_confirmation(tmp_path: Path) -> None:
    _seed_workspace_with_geometry(tmp_path)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {
                    "figure_id": "fig-1",
                    "relation_key": relation_key(
                        RelationType.PARALLEL, ["AC"]
                    ),
                    "action": "confirm",
                }
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "review.py"),
            "--project-root",
            str(tmp_path),
            "apply",
            "--decisions",
            str(decisions),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 5
    assert "cannot confirm" in result.stderr


def test_review_cli_report_lists_blocked(tmp_path: Path) -> None:
    _seed_workspace_with_geometry(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "review.py"),
            "--project-root",
            str(tmp_path),
            "report",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "blocked figures: 2" in result.stdout, result.stdout
    assert "parallel::ac" in result.stdout
    assert "parallel::bc" in result.stdout


# ---------------------------------------------------------------------- #
# Stage 5 resumability — P0: rerunning --geometry must not erase
# human review state. The default path skips Stage 5 when it is
# already completed; --force-geometry re-extracts and clears the
# audit log because the new queue overwrites every review_state.
# ----------------------------------------------------------------------


@pytest.fixture
def e2e_workspace(tmp_path: Path) -> ProjectWorkspace:
    """Run the full pipeline (Stages 0-7) so Stage 5 completes once.

    Returns the workspace so tests can apply a review decision and
    then exercise the rerun / force-geometry paths.
    """
    mirror = LocalMirrorDownloader(FIXTURE_INBOX / "images")
    runner = PipelineRunner(mirror)
    project_root = tmp_path / "projects" / "stage5-e2e"
    result = runner.run(
        project_root=project_root,
        inbox_task_dir=FIXTURE_INBOX,
        project_id="stage5-e2e",
        title="Stage 5 E2E",
        subject="math",
        stage="middle-G8",
        outline_path=OUTLINE,
        mode="B",
        preflight=False,
    )
    return result.workspace


def _first_promotable_relation(ws: ProjectWorkspace) -> tuple[str, str, str]:
    """Return (figure_id, relation_key, evidence) for the first
    problem-text-backed relation in the queue. Promotable evidence
    (problem_text / diagram_mark / problem_text_and_diagram_mark) can
    be confirmed via review.
    """
    store = ReviewStateStore(ws)
    for figure in store.load_queue():
        for rel in figure.relations:
            if rel.evidence in PROMOTABLE_EVIDENCE:
                return figure.figure_id, rel.key, rel.evidence.value
    pytest.skip("fixture produced no promotable relation to confirm")


def test_stage5_rerun_preserves_review_state_by_default(
    e2e_workspace: ProjectWorkspace,
) -> None:
    """Default --geometry rerun skips Stage 5 and keeps review state.

    Reproduces the P0 scenario: apply a decision, then rerun the
    command. The decision must stay effective because Stage 5 is
    already completed.
    """
    ws = e2e_workspace
    figure_id, rel_key, _evidence = _first_promotable_relation(ws)

    # Apply a confirm decision.
    apply_review(
        ws,
        [ReviewDecision(figure_id=figure_id, relation_key=rel_key, action=ReviewAction.CONFIRM)],
    )
    state_path = ws.review_dir / "review_state.json"

    # Sanity: the decision landed.
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state_data["decisions"]) == 1
    queue = ReviewStateStore(ws).load_queue()
    rel = next(
        r for f in queue for r in f.relations if f.figure_id == figure_id and r.key == rel_key
    )
    assert rel.review_state == ReviewState.CONFIRMED

    # Simulate the CLI's default rerun path: Stage 5 is completed, so
    # skip it. This is the exact guard added to run_pipeline.py.
    assert is_stage_completed(ws, "stage5_geometry")
    # NOT calling analyze_geometry() — the CLI would record SKIPPED
    # and leave review/ untouched.

    # After the "rerun", the decision and the queue state survive.
    state_after = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state_after["decisions"]) == 1
    queue_after = ReviewStateStore(ws).load_queue()
    rel_after = next(
        r for f in queue_after for r in f.relations if f.figure_id == figure_id and r.key == rel_key
    )
    assert rel_after.review_state == ReviewState.CONFIRMED


def test_stage5_force_clears_review_state_and_records_reset(
    e2e_workspace: ProjectWorkspace,
) -> None:
    """--force-geometry re-extracts, wipes the audit log, and records
    the reset in the project manifest so the decision is traceable."""
    ws = e2e_workspace
    figure_id, rel_key, _evidence = _first_promotable_relation(ws)

    apply_review(
        ws,
        [ReviewDecision(figure_id=figure_id, relation_key=rel_key, action=ReviewAction.CONFIRM)],
    )
    state_path = ws.review_dir / "review_state.json"
    assert len(json.loads(state_path.read_text(encoding="utf-8"))["decisions"]) == 1

    # Force re-extraction.
    analyze_geometry(ws, force=True)

    # Audit log is wiped because the new queue is all unreviewed.
    state_after = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_after["decisions"] == []

    # Every relation in the new queue is back to unreviewed.
    for figure in ReviewStateStore(ws).load_queue():
        for rel in figure.relations:
            assert rel.review_state == ReviewState.UNREVIEWED

    # Manifest records the reset so the decision is traceable.
    manifest = ws.load_manifest()
    meta = manifest["stages"]["stage5_geometry"]["metadata"]
    assert meta["review_reset"] is True
    assert meta["review_reset_at"]


def test_run_pipeline_cli_exposes_force_geometry_flag() -> None:
    """``run_pipeline.py --help`` lists --force-geometry so users
    can discover the explicit re-extract path."""
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "run_pipeline.py"), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "--force-geometry" in proc.stdout
    assert "Clears review/review_state.json" in proc.stdout
