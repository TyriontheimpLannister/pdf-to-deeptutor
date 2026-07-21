"""Outline-driven content matching for Stage 4b.

Public surface:

* :class:`Outline` / :class:`Topic` / :class:`VocabularyEntry` —
  loaded representation of an outline YAML file.
* :class:`OutlineLoader` — loads and validates an outline YAML.
* :class:`Item` — a piece of normalized content (definition, theorem,
  example, solution, exercise, summary, chapter heading).
* :class:`TopicAssignment` / :class:`MatchDetail` — per-item assignment
  record produced by :class:`OutlineMatcher`.
* :class:`OutlineMatcher` — assigns items to outline leaves.
* :func:`match_project` — convenience entry point that runs the
  pipeline stage and writes artifacts into the project workspace.

The matching is deterministic: each item is scored against every leaf
topic by counting keyword hits plus 1 per regex hit. Ties are broken
by the leaf's ``priority`` (higher wins) and finally by topic id order
for stable output.
"""
from .matcher import (
    Item,
    MatchDetail,
    OutlineMatcher,
    TopicAssignment,
    match_project,
)
from .noise import (
    NOISE_TITLE_PATTERNS,
    NoiseVerdict,
    classify_noise,
    is_noise_item,
    partition_items,
)
from .outline import Outline, OutlineLoader, OutlineLoadError, Topic, VocabularyEntry

__all__ = [
    "Item",
    "MatchDetail",
    "NOISE_TITLE_PATTERNS",
    "NoiseVerdict",
    "Outline",
    "OutlineLoadError",
    "OutlineLoader",
    "OutlineMatcher",
    "Topic",
    "TopicAssignment",
    "VocabularyEntry",
    "classify_noise",
    "is_noise_item",
    "match_project",
    "partition_items",
]
