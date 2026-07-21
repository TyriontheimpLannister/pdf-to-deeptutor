"""OutlineMatcher — assigns BookView items to outline leaves.

Pipeline integration:

1. Read ``normalized/full.md``.
2. Run :func:`extract_items` to produce a list of ``Item``.
3. For each item, score it against every leaf topic in the outline:
   - +1 per distinct keyword hit (case-insensitive substring),
   - +1 per regex pattern hit.
4. Keep topics with score >= min_score (default 1) and ordered by
   score desc, then by priority desc, then by leaf declaration order.
5. Items with no match fall into the synthetic ``_misc`` topic; the
   validator reports them under ``unclassified_items``.
6. Persist ``topic_assignments/assignments.json`` and
   ``reports/topic_assignment_report.json``, and record the stage in
   the project manifest.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..project import ProjectWorkspace, StageStatus, record_stage
from .items import Item, extract_items
from .outline import Outline, OutlineLoader, VocabularyEntry


@dataclass
class MatchDetail:
    """Per-topic match evidence for one item."""

    topic_id: str
    score: int
    keyword_hits: list[str] = field(default_factory=list)
    pattern_hits: list[str] = field(default_factory=list)
    negative_keyword_hits: list[str] = field(default_factory=list)
    negative_pattern_hits: list[str] = field(default_factory=list)
    chapter_stopwords_applied: list[str] = field(default_factory=list)

    @property
    def vetoed(self) -> bool:
        """True when negative-context rules suppressed this match."""
        return bool(self.negative_keyword_hits) or bool(self.negative_pattern_hits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "score": self.score,
            "keyword_hits": sorted(self.keyword_hits),
            "pattern_hits": sorted(self.pattern_hits),
            "negative_keyword_hits": sorted(self.negative_keyword_hits),
            "negative_pattern_hits": sorted(self.negative_pattern_hits),
            "chapter_stopwords_applied": list(self.chapter_stopwords_applied),
        }


@dataclass
class TopicAssignment:
    """Result of matching one item against an outline."""

    item_id: str
    item_type: str
    title: str
    topic_ids: list[str]
    match_details: list[MatchDetail] = field(default_factory=list)
    review_state: str = "unreviewed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "topic_ids": list(self.topic_ids),
            "match_details": [d.to_dict() for d in self.match_details],
            "review_state": self.review_state,
        }


@dataclass
class MatchReport:
    """Aggregate stats and unclassified list for a matching run."""

    outline_id: str
    outline_version: str
    outline_sha256: str
    total_items: int
    unclassified_items: list[str]
    topics_used: dict[str, int]  # topic_id -> count of assigned items
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline_id": self.outline_id,
            "outline_version": self.outline_version,
            "outline_sha256": self.outline_sha256,
            "total_items": self.total_items,
            "unclassified_items": list(self.unclassified_items),
            "topics_used": dict(self.topics_used),
            "generated_at": self.generated_at,
        }


class OutlineMatcher:
    """Assign items to outline leaves using vocabulary scoring."""

    MISC_TOPIC = "_misc"

    def __init__(
        self,
        outline: Outline,
        *,
        min_score: int = 1,
        max_topics_per_item: int = 4,
    ) -> None:
        self._outline = outline
        self._min_score = min_score
        self._max_topics_per_item = max_topics_per_item
        self._patterns, self._negative_patterns = self._compile_patterns(outline)

    def match(self, items: Iterable[Item]) -> tuple[list[TopicAssignment], MatchReport]:
        leaves = self._outline.leaves()
        assignments: list[TopicAssignment] = []
        topics_used: dict[str, int] = {}
        unclassified: list[str] = []

        for item in items:
            details: list[MatchDetail] = []
            for leaf in leaves:
                vocab = self._outline.vocabulary_for(leaf.id)
                if vocab.is_empty():
                    continue
                detail = self._score(item, leaf.id, vocab)
                if detail.score >= self._min_score:
                    details.append(detail)
            details.sort(
                key=lambda d: (
                    -d.score,
                    -self._outline.vocabulary_for(d.topic_id).priority,
                    leaves_and_order(self._outline, d.topic_id),
                )
            )
            chosen = details[: self._max_topics_per_item]
            topic_ids = [d.topic_id for d in chosen]
            if not topic_ids:
                topic_ids = [self.MISC_TOPIC]
                unclassified.append(item.item_id)
            for tid in topic_ids:
                topics_used[tid] = topics_used.get(tid, 0) + 1

            # Stamp the chosen topics back onto the item so downstream
            # stages (export planner, review) can read it without a
            # second pass.
            item.topic_ids = tuple(topic_ids)
            assignments.append(
                TopicAssignment(
                    item_id=item.item_id,
                    item_type=item.item_type,
                    title=item.title,
                    topic_ids=topic_ids,
                    match_details=chosen,
                    review_state="confirmed" if topic_ids != [self.MISC_TOPIC] else "unreviewed",
                )
            )

        report = MatchReport(
            outline_id=self._outline.outline_id,
            outline_version=self._outline.version,
            outline_sha256=self._outline.sha256,
            total_items=len(assignments),
            unclassified_items=unclassified,
            topics_used=topics_used,
            generated_at=_now(),
        )
        return assignments, report

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _score(self, item: Item, leaf_id: str, vocab: VocabularyEntry) -> MatchDetail:
        searchable = item.searchable

        # Chapter-scoped stopwords: scrub the word-from-share noise that
        # appears in every item of a given chapter so that weak-positive
        # keywords never accumulate into a false multi-topic match.
        # Patterns keep running on the original searchable; only the
        # keyword substring check sees the scrubbed text. This keeps
        # ``chapter_stopwords`` low-risk: it can only *remove* a hit,
        # never add one.
        stopwords = self._outline.chapter_stopwords_for(leaf_id)
        scrubbed, applied = self._scrub_stopwords(searchable, stopwords)

        keyword_hits: list[str] = []
        for kw in vocab.keywords:
            if not kw:
                continue
            if kw.lower() in scrubbed:
                keyword_hits.append(kw)
        pattern_hits: list[str] = []
        for pat in self._patterns.get(leaf_id, ()):
            if pat.search(searchable):
                pattern_hits.append(pat.pattern)
        # Deduplicate identical patterns (shouldn't happen post-parse,
        # but cheap insurance).
        pattern_hits = sorted(set(pattern_hits))

        # Negative-context veto: if any negative rule hits, this leaf
        # is suppressed below ``min_score`` so it never enters the
        # candidate set. We still record the negative hits so the
        # review report can surface "tried X, vetoed by Y".
        negative_keyword_hits = [
            kw for kw in vocab.negative_keywords if kw and kw.lower() in searchable
        ]
        negative_pattern_hits = [
            pat.pattern
            for pat in self._negative_patterns.get(leaf_id, ())
            if pat.search(searchable)
        ]
        vetoed = bool(negative_keyword_hits) or bool(negative_pattern_hits)

        base_score = len(keyword_hits) + len(pattern_hits)
        score = self._min_score - 1 if vetoed else base_score
        return MatchDetail(
            topic_id=leaf_id,
            score=score,
            keyword_hits=keyword_hits,
            pattern_hits=pattern_hits,
            negative_keyword_hits=negative_keyword_hits,
            negative_pattern_hits=negative_pattern_hits,
            chapter_stopwords_applied=applied,
        )

    @staticmethod
    def _scrub_stopwords(
        text: str, stopwords: tuple[str, ...]
    ) -> tuple[str, list[str]]:
        """Replace each chapter-stopword occurrence with the same
        number of spaces so keyword substring checks no longer see it,
        but byte offsets in the original text stay meaningful.
        Returns the scrubbed text and the (deduplicated, order-stable)
        list of stopwords that actually appeared at least once.
        """
        if not stopwords:
            return text, []
        scrubbed = text
        applied: list[str] = []
        for sw in stopwords:
            sw_low = sw.lower()
            if not sw_low or sw_low not in scrubbed:
                continue
            scrubbed = scrubbed.replace(sw_low, " " * len(sw_low))
            applied.append(sw)
        return scrubbed, applied

    def _compile_patterns(
        self, outline: Outline
    ) -> tuple[
        dict[str, tuple[re.Pattern[str], ...]],
        dict[str, tuple[re.Pattern[str], ...]],
    ]:
        """Pre-compile the positive and negative regex patterns per leaf."""
        compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
        negative: dict[str, tuple[re.Pattern[str], ...]] = {}
        for leaf_id, vocab in outline.vocabulary.items():
            pos = tuple(re.compile(p) for p in vocab.patterns)
            if pos:
                compiled[leaf_id] = pos
            neg = tuple(re.compile(p) for p in vocab.negative_patterns)
            if neg:
                negative[leaf_id] = neg
        return compiled, negative


def leaves_and_order(outline: Outline, leaf_id: str) -> int:
    """Stable ordering index for a leaf, used as the final tiebreaker."""
    for idx, leaf in enumerate(outline.leaves()):
        if leaf.id == leaf_id:
            return idx
    return len(outline.leaves())


# ---------------------------------------------------------------------- #
# Pipeline glue
# ---------------------------------------------------------------------- #


def match_project(
    workspace: ProjectWorkspace,
    outline_path: Path | str,
    *,
    markdown_path: Path | str | None = None,
    min_score: int = 1,
    max_topics_per_item: int = 4,
) -> tuple[list[TopicAssignment], MatchReport]:
    """Run Stage 4b against the given workspace and persist artifacts.

    Parameters
    ----------
    workspace
        Existing project workspace (manifest already written).
    outline_path
        Path to the outline YAML. The file is loaded via
        :class:`OutlineLoader`.
    markdown_path
        Override for the markdown source. Defaults to
        ``workspace.normalized_dir / "full.md"``.
    """
    outline = OutlineLoader().load(outline_path)
    md_path = Path(markdown_path) if markdown_path else (workspace.normalized_dir / "full.md")
    if not md_path.is_file():
        raise FileNotFoundError(f"normalized markdown not found: {md_path}")
    items = extract_items(md_path.read_text(encoding="utf-8"))

    # Drop OCR noise items before matching. This keeps watermark
    # banners, page-number noise, and single-character ornaments out
    # of every downstream plan (Stage 4c, Stage 7, _misc fallback).
    from .noise import classify_noise  # local import to avoid cycles  # noqa: PLC0415

    def _item_id(it) -> str:
        if isinstance(it, dict):
            return str(it.get("item_id", ""))
        return str(getattr(it, "item_id", ""))

    kept_items: list = []
    dropped_items: list[dict] = []
    for item in items:
        verdict = classify_noise(item)
        if verdict.is_noise:
            dropped_items.append(
                {"item_id": _item_id(item), "reason": verdict.reason}
            )
        else:
            kept_items.append(item)

    matcher = OutlineMatcher(
        outline, min_score=min_score, max_topics_per_item=max_topics_per_item
    )
    assignments, report = matcher.match(kept_items)

    # Persist assignments.
    workspace.topic_assignments_dir.mkdir(parents=True, exist_ok=True)
    assignments_path = workspace.topic_assignments_dir / "assignments.json"
    assignments_payload = {
        "outline_id": outline.outline_id,
        "outline_version": outline.version,
        "outline_sha256": outline.sha256,
        "assignments": [a.to_dict() for a in assignments],
    }
    assignments_path.write_text(
        json.dumps(assignments_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Persist report.
    workspace.reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace.reports_dir / "topic_assignment_report.json"
    report_payload = report.to_dict()
    report_payload["noise_items"] = [
        {"item_id": d["item_id"], "reason": d["reason"]}
        for d in dropped_items
    ]
    report_payload["noise_filter"] = {
        "dropped_count": len(dropped_items),
        "kept_count": len(kept_items),
        "ruleset": "outline.noise.classify_noise",
    }
    report_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Record stage in manifest.
    record_stage(
        workspace,
        "stage4b_outline",
        status=StageStatus.COMPLETED,
        input_fingerprint=outline.sha256,
        output_fingerprint=_sha256_file(assignments_path),
        metadata={
            "outline_path": str(Path(outline_path).resolve()),
            "outline_id": outline.outline_id,
            "outline_version": outline.version,
            "total_items": report.total_items,
            "unclassified_count": len(report.unclassified_items),
            "noise_dropped_count": len(dropped_items),
            "assignments_path": str(assignments_path.relative_to(workspace.root)),
            "report_path": str(report_path.relative_to(workspace.root)),
        },
    )
    return assignments, report


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
