"""Outline model and YAML loader.

An outline is a user-supplied taxonomy (see ``docs/PRODUCT_SPEC.md``
and ``schemas/outline.schema.json``). The loader:

* parses the YAML body;
* verifies that every topic ``id`` is a slug matching
  ``^[a-z0-9][a-z0-9_-]*$``;
* flattens the topic tree and exposes only the **leaves** to the
  matcher. Vocabulary is keyed by leaf id and an empty vocabulary
  for a leaf means "never matches" (intentional, per outline
  semantics);
* records the file's SHA-256 so Stage 4b can detect drift between
  runs.

Anything that needs to surface a user-facing error should raise
:class:`OutlineLoadError`. Callers in the CLI layer convert that
into a process exit with a message.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _collect_ancestors(node: Topic, target_id: str, out: list[Topic]) -> bool:
    """Walk the tree depth-first; append every node on the path
    to ``target_id`` (excluding the root ancestor we started from
    is not the case here — we keep all parents AND the target leaf
    itself) into ``out``; return True iff ``target_id`` is found
    below ``node``. Used by ``Outline._ancestors_of``.
    """
    out.append(node)
    if node.id == target_id:
        return True
    for child in node.children:
        if _collect_ancestors(child, target_id, out):
            return True
    out.pop()
    return False


class OutlineLoadError(ValueError):
    """Raised when an outline YAML cannot be parsed or fails validation."""


@dataclass(frozen=True)
class Topic:
    """One node in the outline tree."""

    id: str
    label: str
    description: str = ""
    children: tuple[Topic, ...] = ()
    chapter_stopwords: tuple[str, ...] = ()

    def leaves(self) -> list[Topic]:
        """Return every descendant that has no children of its own."""
        if not self.children:
            return [self]
        out: list[Topic] = []
        for child in self.children:
            out.extend(child.leaves())
        return out


@dataclass(frozen=True)
class VocabularyEntry:
    """Vocabulary rules for one leaf topic.

    ``keywords`` / ``patterns`` are positive evidence — each hit adds
    one point to the score. ``negative_keywords`` / ``negative_patterns``
    are *context filters*: if any of them appears in the item's
    searchable text, the leaf is suppressed entirely (the score is
    floored below ``min_score`` so the entry does not enter the
    candidate set). Negative rules never *add* matches on their own;
    they only veto a leaf that already had positive evidence.
    """

    keywords: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()
    negative_patterns: tuple[str, ...] = ()
    priority: int = 0

    def is_empty(self) -> bool:
        return (
            not self.keywords
            and not self.patterns
            and not self.negative_keywords
            and not self.negative_patterns
        )


@dataclass
class Outline:
    """A loaded outline ready for matching."""

    outline_id: str
    name: str
    version: str
    applies_to: dict[str, str]
    topics: tuple[Topic, ...]
    vocabulary: dict[str, VocabularyEntry]
    strategy_default: str = "B"
    strategy_overrides: dict[str, str] = field(default_factory=dict)
    source_path: Path | None = None
    sha256: str = ""

    # ------------------------------------------------------------------ #
    # Tree helpers
    # ------------------------------------------------------------------ #

    def leaves(self) -> list[Topic]:
        """All leaves across the topic tree (preserving declaration order)."""
        out: list[Topic] = []
        for topic in self.topics:
            out.extend(topic.leaves())
        return out

    def leaf_by_id(self, topic_id: str) -> Topic | None:
        for leaf in self.leaves():
            if leaf.id == topic_id:
                return leaf
        return None

    def vocabulary_for(self, leaf_id: str) -> VocabularyEntry:
        """Return the vocabulary for a leaf, or an empty entry."""
        return self.vocabulary.get(leaf_id, VocabularyEntry())

    def chapter_stopwords_for(self, leaf_id: str) -> tuple[str, ...]:
        """Return the union of chapter_stopwords inherited from every
        ancestor of ``leaf_id`` (the parents themselves, and the leaf
        if it also lists stopwords). Used by the matcher to suppress
        weak-positive keywords that would otherwise pollute every leaf
        in the same chapter.
        """
        merged: list[str] = []
        for parent in self._ancestors_of(leaf_id):
            for sw in parent.chapter_stopwords:
                if sw and sw not in merged:
                    merged.append(sw)
        return tuple(merged)

    def _ancestors_of(self, leaf_id: str) -> list[Topic]:
        """Return every ancestor (and the leaf itself if present)
        along the path from a top-level topic to ``leaf_id``, in
        top-down order. Empty list if the leaf is unknown.
        """
        path: list[Topic] = []
        for root in self.topics:
            if _collect_ancestors(root, leaf_id, path):
                # _collect_ancestors already appended nodes in
                # top-down order (root first, leaf last); keep it
                # as-is so callers get a deterministic traversal.
                return list(path)
        return []

    def strategy_for(self, leaf_id: str) -> str:
        """Return the reorganize mode for a leaf, with default fallback."""
        return self.strategy_overrides.get(leaf_id, self.strategy_default)


class OutlineLoader:
    """Load and validate an outline YAML file."""

    def load(self, path: Path | str) -> Outline:
        p = Path(path)
        if not p.is_file():
            raise OutlineLoadError(f"outline not found: {p}")
        raw = p.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise OutlineLoadError(f"invalid YAML in {p}: {exc}") from exc
        if not isinstance(data, dict):
            raise OutlineLoadError(f"{p}: top-level must be a mapping")
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self._build(data, p, sha)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build(self, data: dict[str, Any], path: Path, sha: str) -> Outline:
        for key in ("outline_id", "name", "version", "topics"):
            if key not in data:
                raise OutlineLoadError(f"{path}: missing required key {key!r}")

        topics_raw = data["topics"]
        if not isinstance(topics_raw, list) or not topics_raw:
            raise OutlineLoadError(f"{path}: 'topics' must be a non-empty list")

        topics = tuple(self._parse_topic(t, path) for t in topics_raw)
        leaves = [leaf for t in topics for leaf in t.leaves()]
        leaf_ids = [leaf.id for leaf in leaves]

        # Reject duplicate ids at the leaf level (interior collisions
        # are also caught because walk collects all node ids).
        seen: set[str] = set()
        for node_id in self._walk_ids(topics):
            if node_id in seen:
                raise OutlineLoadError(f"{path}: duplicate topic id {node_id!r}")
            seen.add(node_id)

        vocab_raw = data.get("vocabulary") or {}
        if not isinstance(vocab_raw, dict):
            raise OutlineLoadError(f"{path}: 'vocabulary' must be a mapping")

        vocab: dict[str, VocabularyEntry] = {}
        for leaf_id, raw_entry in vocab_raw.items():
            if leaf_id not in leaf_ids:
                raise OutlineLoadError(
                    f"{path}: vocabulary entry {leaf_id!r} does not match any leaf"
                )
            vocab[leaf_id] = self._parse_vocab(raw_entry, leaf_id, path)

        # Vocabulary entries for interior topics are allowed but ignored
        # by the matcher; warn-style behaviour is to silently drop them.
        vocab = {lid: v for lid, v in vocab.items() if lid in leaf_ids}

        strategy_raw = data.get("strategy") or {}
        if not isinstance(strategy_raw, dict):
            raise OutlineLoadError(f"{path}: 'strategy' must be a mapping")
        default = strategy_raw.get("default", "B")
        if default not in ("A", "B", "C"):
            raise OutlineLoadError(
                f"{path}: strategy.default must be A, B, or C (got {default!r})"
            )
        overrides_raw = strategy_raw.get("overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise OutlineLoadError(f"{path}: 'strategy.overrides' must be a mapping")
        overrides: dict[str, str] = {}
        for leaf_id, mode in overrides_raw.items():
            if mode not in ("A", "B", "C"):
                raise OutlineLoadError(
                    f"{path}: override for {leaf_id!r} must be A, B, or C (got {mode!r})"
                )
            if leaf_id not in leaf_ids:
                # Allow overrides on interior topics (they will apply to
                # descendants when the planner walks the tree). We do
                # not fail here.
                pass
            overrides[leaf_id] = mode

        return Outline(
            outline_id=str(data["outline_id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            applies_to=dict(data.get("applies_to") or {}),
            topics=topics,
            vocabulary=vocab,
            strategy_default=default,
            strategy_overrides=overrides,
            source_path=path,
            sha256=sha,
        )

    def _parse_topic(self, raw: Any, path: Path) -> Topic:
        if not isinstance(raw, dict):
            raise OutlineLoadError(f"{path}: topic entry must be a mapping")
        if "id" not in raw or "label" not in raw:
            raise OutlineLoadError(f"{path}: topic entry missing 'id' or 'label'")
        tid = str(raw["id"])
        if not SLUG_RE.match(tid):
            raise OutlineLoadError(
                f"{path}: topic id {tid!r} is not a slug (^[a-z0-9][a-z0-9_-]*$)"
            )
        children_raw = raw.get("children") or []
        if not isinstance(children_raw, list):
            raise OutlineLoadError(f"{path}: 'children' for {tid!r} must be a list")
        children = tuple(self._parse_topic(c, path) for c in children_raw)
        stopwords_raw = raw.get("chapter_stopwords") or []
        if not isinstance(stopwords_raw, list):
            raise OutlineLoadError(
                f"{path}: 'chapter_stopwords' for {tid!r} must be a list"
            )
        stopwords = tuple(str(s) for s in stopwords_raw if str(s).strip())
        return Topic(
            id=tid,
            label=str(raw["label"]),
            description=str(raw.get("description") or ""),
            children=children,
            chapter_stopwords=stopwords,
        )

    def _parse_vocab(self, raw: Any, leaf_id: str, path: Path) -> VocabularyEntry:
        if not isinstance(raw, dict):
            raise OutlineLoadError(
                f"{path}: vocabulary for {leaf_id!r} must be a mapping"
            )
        keywords = tuple(str(k) for k in (raw.get("keywords") or []))
        negative_keywords = tuple(
            str(k) for k in (raw.get("negative_keywords") or [])
        )
        patterns_raw = raw.get("patterns") or []
        if not isinstance(patterns_raw, list):
            raise OutlineLoadError(
                f"{path}: patterns for {leaf_id!r} must be a list"
            )
        compiled: list[str] = []
        for pat in patterns_raw:
            try:
                re.compile(str(pat))
            except re.error as exc:
                raise OutlineLoadError(
                    f"{path}: invalid regex for {leaf_id!r}: {pat!r} ({exc})"
                ) from exc
            compiled.append(str(pat))
        negative_patterns_raw = raw.get("negative_patterns") or []
        if not isinstance(negative_patterns_raw, list):
            raise OutlineLoadError(
                f"{path}: negative_patterns for {leaf_id!r} must be a list"
            )
        negative_compiled: list[str] = []
        for pat in negative_patterns_raw:
            try:
                re.compile(str(pat))
            except re.error as exc:
                raise OutlineLoadError(
                    f"{path}: invalid regex in negative_patterns for "
                    f"{leaf_id!r}: {pat!r} ({exc})"
                ) from exc
            negative_compiled.append(str(pat))
        priority = int(raw.get("priority") or 0)
        return VocabularyEntry(
            keywords=keywords,
            patterns=tuple(compiled),
            negative_keywords=negative_keywords,
            negative_patterns=tuple(negative_compiled),
            priority=priority,
        )

    def _walk_ids(self, topics: Iterable[Topic]) -> Iterable[str]:
        for t in topics:
            yield t.id
            yield from self._walk_ids(t.children)


def load_outline(path: Path | str) -> Outline:
    """Convenience wrapper around :meth:`OutlineLoader.load`."""
    return OutlineLoader().load(path)
