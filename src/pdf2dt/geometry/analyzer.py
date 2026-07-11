"""Stage 5 — geometry analysis.

The analyzer turns one figure-bound :class:`BookItem` (with its
layout figure block) into a :class:`GeometryFigure`.  It is rule
based and deterministic; the public surface is small so a future
VLM-backed implementation can be swapped in behind the same
interface.

The rule table
--------------

Each rule maps a textual or caption pattern to one
:class:`RelationType` plus a default :class:`Evidence`.  Evidence
is later overridden by the dispatcher when the same relation is
seen in *both* the text and the figure caption: that produces
``problem_text_and_diagram_mark``.

The vocabulary is intentionally small.  Patterns are evaluated
as case-sensitive substrings to keep the rule set auditable.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..bookview.builder import BookItem
from ..project import ProjectWorkspace, StageStatus, record_stage
from .evidence import Evidence, ReviewState
from .models import GeometryFigure, GeometryRelation, RelationType

# ---------------------------------------------------------------------- #
# Pattern table
# ---------------------------------------------------------------------- #

# Each entry: (RelationType, list of (textual_pattern, default_evidence))
# textual_pattern is a substring or a regex; we compile on import.
# A textual match is the default; caption-only matches downgrade to
# ``diagram_mark``; text-only matches keep ``problem_text``.
_RULES: list[tuple[RelationType, list[tuple[re.Pattern[str], Evidence]]]] = [
    (
        RelationType.PARALLEL,
        [
            (re.compile(r"∥|平行|//|\\parallel"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.PERPENDICULAR,
        [
            (re.compile(r"⊥|垂直|\\perp"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.EQUAL_LENGTH,
        [
            (re.compile(r"=\s*[A-Z]{1,3}.*[A-Z]{1,3}|等长|相等.*边"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.EQUAL_ANGLE,
        [
            (re.compile(r"∠|角.*相等|等角"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.MIDPOINT,
        [
            (re.compile(r"中点|midpoint"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.COLLINEAR,
        [
            (re.compile(r"共线|三点.*在.*同.*(直)?线"), Evidence.PROBLEM_TEXT),
        ],
    ),
    (
        RelationType.POINT_ON_SEGMENT,
        [
            (
                re.compile(r"D\s*在\s*[A-Z]{1,3}上|点.*在.*线段.*上|on segment"),
                Evidence.PROBLEM_TEXT,
            ),
        ],
    ),
]


# ---------------------------------------------------------------------- #
# Point / segment extraction
# ---------------------------------------------------------------------- #

# A point token is a single ASCII letter (optionally with a Greek
# suffix), or a Chinese-math point label like 点 A.  We deliberately
# allow only one or two characters to avoid swallowing whole words.
_POINT_TOKEN_RE = re.compile(
    r"\\?(?:[A-Z]|\\[A-Za-z]+|点\s*([A-Z]))"
)
# Segments: AB, AB-bar, \overline{AB}, \overline{BC}.
_SEGMENT_RE = re.compile(
    r"\\(?:overline|overrightarrow)\s*\{\s*([A-Z])\s*([A-Z])\s*\}"
    r"|\b([A-Z])([A-Z])\b"
)
# LaTeX \triangle, \angle, \parallel, \perp context.
_TRIANGLE_RE = re.compile(r"\\triangle\s*\{\s*([A-Z])\s*([A-Z])\s*([A-Z])\s*\}")
_ANGLE_RE = re.compile(r"\\(?:angle|measuredangle)\s*\{\s*([A-Z])\s*([A-Z])\s*([A-Z])\s*\}")


def _extract_points(text: str) -> list[str]:
    """Return the deduplicated, source-order list of point labels."""
    seen: set[str] = set()
    out: list[str] = []
    # Triangles and angles first — they often encode the canonical
    # ordering and downstream relations key off these.
    for m in _TRIANGLE_RE.finditer(text):
        for letter in m.groups():
            if letter and letter not in seen:
                seen.add(letter)
                out.append(letter)
    for m in _ANGLE_RE.finditer(text):
        for letter in m.groups():
            if letter and letter not in seen:
                seen.add(letter)
                out.append(letter)
    # Then bare point tokens.
    for m in _POINT_TOKEN_RE.finditer(text):
        letter = m.group(1) or m.group(0).lstrip("\\").lstrip("点").strip()
        if not letter:
            continue
        # Keep only the leading capital letter.  Tokens like
        # ``点 D`` come back as ``D``.
        letter = letter.strip()[:1]
        if letter.isalpha() and letter.isupper() and letter not in seen:
            seen.add(letter)
            out.append(letter)
    return out


def _extract_segments(text: str, points: list[str]) -> list[str]:
    """Return the deduplicated list of two-letter segments ``XY``."""
    valid = set(points)
    out: list[str] = []
    seen: set[str] = set()
    for m in _SEGMENT_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        if a is None:
            a, b = m.group(3), m.group(4)
        if a is None or b is None:
            continue
        if a not in valid or b not in valid:
            continue
        if a == b:
            continue
        seg = f"{a}{b}"
        if seg in seen:
            continue
        seen.add(seg)
        out.append(seg)
    return out


def _entities_for_rule(
    rule: RelationType, text: str, points: list[str], segments: list[str]
) -> list[list[str]]:
    """Produce candidate entity sets for one rule.

    Return a list of ``entities`` lists.  Each list must contain at
    least one entry, otherwise the rule is skipped.  We use the
    existing points/segments as the entity vocabulary — a relation
    like ``EQUAL_LENGTH`` picks segments, ``EQUAL_ANGLE`` picks
    triples, etc.
    """
    if rule in {RelationType.EQUAL_LENGTH, RelationType.PARALLEL, RelationType.PERPENDICULAR}:
        return [[s] for s in segments]
    if rule == RelationType.EQUAL_ANGLE:
        triples: list[list[str]] = []
        for i, a in enumerate(points):
            for b in points[i + 1 :]:
                for c in points[i + 2 :]:
                    triples.append([a, b, c])
        return triples
    if rule in {RelationType.MIDPOINT, RelationType.POINT_ON_SEGMENT, RelationType.COLLINEAR}:
        # Compose segment + point combinations; the analyzer only
        # emits ones whose text pattern matched.
        combos: list[list[str]] = []
        for seg in segments:
            combos.append([seg])
            for p in points:
                if p in seg:
                    continue
                combos.append([seg, p])
        if not combos and len(points) >= 2:
            for i, a in enumerate(points):
                for b in points[i + 1 :]:
                    combos.append([a, b])
        return combos
    return []


def _resolve_evidence(
    rule_evidence: Evidence, in_text: bool, in_caption: str
) -> Evidence:
    """Combine textual hit and figure caption hit into a single
    :class:`Evidence` value.

    The rule's default evidence is what we would assign on a pure
    text match.  When the figure caption also carries a textual
    mark for the same relation, we promote to
    ``problem_text_and_diagram_mark``.  When only the caption
    matches, we fall back to ``diagram_mark``.  When neither the
    text nor the caption matches but the analyzer found the
    relation by visual inference, we emit ``visual_inference``.
    """
    caption = in_caption or ""
    if in_text and caption:
        return Evidence.PROBLEM_TEXT_AND_DIAGRAM_MARK
    if caption:
        return Evidence.DIAGRAM_MARK
    if in_text:
        return rule_evidence
    return Evidence.VISUAL_INFERENCE


# ---------------------------------------------------------------------- #
# Single-figure analyzer
# ---------------------------------------------------------------------- #


@dataclass
class GeometryAnalyzer:
    """Deterministic rule-based analyzer for one figure-bound item.

    The class is stateless; an instance is created per analyze()
    call so callers can subclass it (e.g. to wrap a VLM provider)
    without sharing mutable state.
    """

    rules: list[tuple[RelationType, list[tuple[re.Pattern[str], Evidence]]]] = field(
        default_factory=lambda: list(_RULES)
    )

    def analyze(
        self,
        *,
        item: BookItem,
        asset_id: str,
        caption: str = "",
        layout_labels: list[str] | None = None,
        asset_path: Path | None = None,
    ) -> GeometryFigure | None:
        """Return a :class:`GeometryFigure` for *item* or ``None``.

        Returns ``None`` when the item has no figure-bound asset so
        the dispatcher can skip it cleanly.
        """
        if not asset_id:
            return None

        text = item.text or ""
        caption = caption or ""
        labels = list(layout_labels or [])

        # Build a single buffer for matching.  Markdown image syntax
        # is reduced to its alt text so caption labels in the alt
        # still count as figure-side evidence.
        combined_caption = " ".join([caption, *labels]).strip()

        points = _extract_points(text) or _extract_points(combined_caption)
        segments = _extract_segments(text, points) or _extract_segments(
            combined_caption, points
        )

        figure_id = _figure_id_for(item.item_id, asset_id)
        relations: list[GeometryRelation] = []
        visual_observations: list[str] = []

        for rule_type, patterns in self.rules:
            compiled = [pat for pat, _ev in patterns]
            default_evidence = patterns[0][1] if patterns else Evidence.PROBLEM_TEXT
            in_text = any(p.search(text) for p in compiled)
            in_caption_match = any(p.search(combined_caption) for p in compiled)
            if not in_text and not in_caption_match:
                continue
            for entities in _entities_for_rule(rule_type, text, points, segments):
                if not entities:
                    continue
                evidence = _resolve_evidence(
                    default_evidence,
                    in_text=in_text,
                    in_caption=combined_caption,
                )
                confidence = _confidence(
                    in_text=in_text,
                    in_caption=bool(in_caption_match),
                    evidence=evidence,
                )
                relations.append(
                    GeometryRelation(
                        type=rule_type,
                        entities=entities,
                        evidence=evidence,
                        source_reference=item.item_id,
                        confidence=confidence,
                    )
                )

        # Unresolved visual observations: patterns that fired but did
        # not yield an entity-resolved relation for this figure.  We
        # keep them so the user can manually promote them during
        # review.  Pattern hits that already produced a relation are
        # not added here; ``should_call_vlm`` reads this list as the
        # "unresolved observations" trigger.
        for rule_type, patterns in self.rules:
            resolved = any(rel.type == rule_type for rel in relations)
            if resolved:
                continue
            for pat, _ev in patterns:
                if pat.search(text) or pat.search(combined_caption):
                    visual_observations.append(
                        f"{rule_type.value}: {pat.pattern}"
                    )
                    break

        return GeometryFigure(
            figure_id=figure_id,
            asset_id=asset_id,
            associated_item_id=item.item_id,
            points=points,
            segments=segments,
            relations=relations,
            visual_observations=visual_observations,
            review_state=ReviewState.UNREVIEWED,
        )


def _confidence(*, in_text: bool, in_caption: bool, evidence: Evidence) -> float:
    """Return a deterministic confidence score for a relation."""
    if evidence == Evidence.PROBLEM_TEXT_AND_DIAGRAM_MARK:
        return 0.95
    if evidence == Evidence.PROBLEM_TEXT:
        return 0.9
    if evidence == Evidence.DIAGRAM_MARK:
        return 0.7
    if evidence == Evidence.VISUAL_INFERENCE:
        return 0.4
    return 0.1


def _figure_id_for(item_id: str, asset_id: str) -> str:
    """Stable figure id derived from item + asset."""
    seed = f"{item_id}|{asset_id}".encode()
    return "fig-" + hashlib.sha1(seed).hexdigest()[:12]


# ---------------------------------------------------------------------- #
# Dispatcher
# ---------------------------------------------------------------------- #


def _iter_figure_items(
    book_view: dict[str, Any],
) -> list[tuple[BookItem, str, str, list[str]]]:
    """Yield (item, asset_id, caption, labels) for every figure-bound item."""
    out: list[tuple[BookItem, str, str, list[str]]] = []
    for chapter in book_view.get("chapters") or []:
        for section in chapter.get("sections") or []:
            for raw in section.get("items") or []:
                asset_refs = raw.get("asset_refs") or []
                if not asset_refs:
                    continue
                first = asset_refs[0]
                asset_id = str(first.get("asset_id") or "")
                if not asset_id:
                    continue
                item = BookItem(
                    item_id=str(raw["item_id"]),
                    item_type=str(raw.get("item_type") or "other"),
                    title=str(raw.get("title") or ""),
                    text=str(raw.get("text") or ""),
                    chapter_path=tuple(raw.get("chapter_path") or ()),
                    source_block_refs=[],
                    asset_refs=[],
                    page_refs=list(raw.get("page_refs") or []),
                )
                caption = str(first.get("caption") or "")
                # The layout doesn't pass labels into BookItem, but
                # we accept them through the function signature for
                # future use.
                out.append((item, asset_id, caption, []))
    return out


@dataclass
class GeometryExtractionReport:
    """Aggregate stats from a Stage 5 run."""

    figures_total: int
    figures_with_relations: int
    relations_total: int
    evidence_counts: dict[str, int]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "figures_total": self.figures_total,
            "figures_with_relations": self.figures_with_relations,
            "relations_total": self.relations_total,
            "evidence_counts": dict(self.evidence_counts),
            "generated_at": self.generated_at,
        }


def analyze_geometry(
    workspace: ProjectWorkspace,
    *,
    book_view_path: Path | str | None = None,
    analyzer: GeometryAnalyzer | None = None,
    force: bool = False,
) -> tuple[list[GeometryFigure], GeometryExtractionReport]:
    """Run Stage 5 against the workspace and persist the result.

    When *force is True*, the caller is deliberately re-extracting
    geometry and accepts that any previously applied review decisions
    become invalid (the queue is overwritten).  Before writing the new
    queue we therefore clear ``review/review_state.json`` so the audit
    log does not contradict a freshly unreviewed queue.  The reset is
    recorded in the ``stage5_geometry`` manifest metadata so the
    decision is traceable.
    """
    bv_path = (
        Path(book_view_path)
        if book_view_path
        else (workspace.book_view_dir / "book_view.json")
    )
    if not bv_path.is_file():
        raise FileNotFoundError(f"BookView not found: {bv_path}")
    book = json.loads(bv_path.read_text(encoding="utf-8"))

    # Map asset_id → layout labels for the figure.  Labels appear
    # only on layout blocks, not on BookItems, so we re-walk the
    # normalized layout to attach them.
    labels_by_asset = _labels_by_asset(workspace)

    analyzer = analyzer or GeometryAnalyzer()
    raw_dir: Path | None = None
    if hasattr(analyzer, "raw_responses_dir"):
        # Allow the dispatcher to set an output directory; default
        # to ``reports/vlm-raw/`` so hybrid runs persist audit data.
        candidate = workspace.reports_dir / "vlm-raw"
        if getattr(analyzer, "raw_responses_dir", None) is None:
            analyzer.raw_responses_dir = candidate
        raw_dir = analyzer.raw_responses_dir
    asset_paths = _asset_paths_by_id(workspace)
    figures: list[GeometryFigure] = []
    evidence_counts: dict[str, int] = {}
    with_relations = 0
    for item, asset_id, caption, _ in _iter_figure_items(book):
        labels = labels_by_asset.get(asset_id, [])
        figure = analyzer.analyze(
            item=item,
            asset_id=asset_id,
            caption=caption,
            layout_labels=labels,
            asset_path=asset_paths.get(asset_id),
        )
        if figure is None:
            continue
        figures.append(figure)
        if figure.relations:
            with_relations += 1
        for rel in figure.relations:
            evidence_counts[rel.evidence.value] = (
                evidence_counts.get(rel.evidence.value, 0) + 1
            )

    payload = {
        "schema_version": "geometry_figures/v1",
        "project_id": workspace.root.name,
        "generated_at": _now(),
        "figures": [f.to_dict() for f in figures],
    }
    out_dir = workspace.review_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Force mode: the caller is deliberately re-extracting geometry.
    # The new queue will overwrite every relation's review_state with
    # ``unreviewed``, so the existing audit log in review_state.json
    # would contradict it.  Wipe the audit log to keep the two files
    # consistent and record the reset in the manifest.
    reset_at: str | None = None
    if force:
        reset_at = _now()
        state_path = out_dir / "review_state.json"
        state_payload = {
            "schema_version": "review_state/v1",
            "project_id": workspace.root.name,
            "updated_at": reset_at,
            "decisions": [],
        }
        state_path.write_text(
            json.dumps(state_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    out_path = out_dir / "geometry_figures.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = GeometryExtractionReport(
        figures_total=len(figures),
        figures_with_relations=with_relations,
        relations_total=sum(len(f.relations) for f in figures),
        evidence_counts=evidence_counts,
        generated_at=_now(),
    )
    report_path = workspace.reports_dir / "geometry_extraction_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    call_records = getattr(analyzer, "call_records", None)
    if call_records:
        vlm_report_path = workspace.reports_dir / "geometry_vlm_report.json"
        vlm_report_path.write_text(
            json.dumps(
                {
                    "generated_at": _now(),
                    "selection_strategy": "should_call_vlm",
                    "calls": [
                        record.to_dict()
                        if hasattr(record, "to_dict")
                        else record
                        for record in call_records
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    vlm_summary: dict[str, Any] = {}
    if call_records:
        statuses = [getattr(c, "status", "") for c in call_records]
        vlm_summary = {
            "total": len(call_records),
            "called": sum(1 for s in statuses if s not in {"skipped", ""}),
            "skipped": sum(1 for s in statuses if s == "skipped"),
            "failed": sum(1 for s in statuses if s == "failed"),
            "rejected": sum(1 for s in statuses if s == "rejected"),
        }
        skip_reasons: dict[str, int] = {}
        for c in call_records:
            reason = getattr(c, "skip_reason", "") or ""
            if reason and getattr(c, "status", "") == "skipped":
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        if skip_reasons:
            vlm_summary["skip_reasons"] = skip_reasons

    record_stage(
        workspace,
        "stage5_geometry",
        status=StageStatus.COMPLETED,
        input_fingerprint=hashlib.sha256(bv_path.read_bytes()).hexdigest(),
        output_fingerprint=hashlib.sha256(out_path.read_bytes()).hexdigest(),
        metadata={
            "figures_path": str(out_path.relative_to(workspace.root)),
            "report_path": str(report_path.relative_to(workspace.root)),
            "figures_total": report.figures_total,
            "figures_with_relations": report.figures_with_relations,
            "relations_total": report.relations_total,
            "evidence_counts": report.evidence_counts,
            "vlm_report_path": (
                "reports/geometry_vlm_report.json" if call_records else None
            ),
            "vlm_raw_dir": (
                str(raw_dir.relative_to(workspace.root)) if raw_dir else None
            ),
            "vlm_summary": vlm_summary,
            "review_reset": bool(reset_at),
            "review_reset_at": reset_at,
        },
    )
    return figures, report


def _labels_by_asset(workspace: ProjectWorkspace) -> dict[str, list[str]]:
    """Re-walk the localized layout to map asset_id → figure labels."""
    layout_path = workspace.normalized_dir / "layout.localized.json"
    if not layout_path.is_file():
        return {}
    data = json.loads(layout_path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for page in data.get("pages") or []:
        for block in page.get("blocks") or []:
            if block.get("type") != "figure":
                continue
            aid = block.get("asset_id")
            if not aid:
                continue
            out[str(aid)] = [str(label) for label in (block.get("labels") or [])]
    return out


def _asset_paths_by_id(workspace: ProjectWorkspace) -> dict[str, Path]:
    """Return the local asset file for each localized asset id."""
    paths: dict[str, Path] = {}
    for path in workspace.assets_dir.iterdir():
        if path.is_file() and path.stem:
            paths[path.stem] = path
    return paths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "GeometryAnalyzer",
    "GeometryExtractionReport",
    "analyze_geometry",
]
