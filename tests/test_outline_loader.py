"""Tests for ``OutlineLoader`` against the generic sample outline.

These tests verify the contract advertised in
``src/pdf2dt/outlining/outline.py``:

* Outline loading succeeds against the canonical outline file.
* Leaf counts and identifiers match the frozen v1.0.0 sample state.
* Validation raises :class:`OutlineLoadError` for every documented
  failure mode.
* The SHA-256 captured at load time matches the raw bytes of the file.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from pdf2dt.outlining.outline import (
    SLUG_RE,
    Outline,
    OutlineLoadError,
    OutlineLoader,
    Topic,
    VocabularyEntry,
)

ROOT = Path(__file__).resolve().parents[1]
OUTLINE_PATH = ROOT / "outlines" / "sample-outline-v1.yaml"


@pytest.fixture
def outline() -> Outline:
    return OutlineLoader().load(OUTLINE_PATH)


def test_loads_sample_outline(outline: Outline) -> None:
    assert outline.outline_id == "sample-outline"
    assert outline.version == "1.0.0"
    assert outline.strategy_default == "B"


def test_leaf_count_and_ids(outline: Outline) -> None:
    leaves = outline.leaves()
    assert [leaf.id for leaf in leaves] == [
        "intro",
        "definitions",
        "theorems",
        "methods",
        "examples",
        "exercises",
        "summary",
        "knowledge-points",
    ]
    # Every leaf id must be a valid slug.
    assert all(SLUG_RE.match(leaf.id) for leaf in leaves)


def test_empty_vocabulary_leaf_never_matches(outline: Outline) -> None:
    # ``intro`` is a leaf with no vocabulary entry => it is intentionally
    # unmatchable (the matcher skips empty vocab).
    assert outline.vocabulary_for("intro").is_empty()


def test_sha256_matches_raw_file_bytes(outline: Outline) -> None:
    expected = hashlib.sha256(OUTLINE_PATH.read_bytes()).hexdigest()
    assert outline.sha256 == expected


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "outline.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_missing_required_key_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        name: No Topics
        version: 1.0.0
        topics:
          - id: a
            label: A
        """,
    )
    with pytest.raises(OutlineLoadError, match="missing required key"):
        OutlineLoader().load(p)


def test_non_slug_topic_id_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        outline_id: bad
        name: Bad
        version: 1.0.0
        topics:
          - id: "Bad Id"
            label: Bad
        """,
    )
    with pytest.raises(OutlineLoadError, match="not a slug"):
        OutlineLoader().load(p)


def test_invalid_regex_pattern_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        outline_id: rx
        name: Rx
        version: 1.0.0
        topics:
          - id: leaf
            label: Leaf
        vocabulary:
          leaf:
            patterns: ["("]   # unterminated group
        """,
    )
    with pytest.raises(OutlineLoadError, match="invalid regex"):
        OutlineLoader().load(p)


def test_duplicate_topic_id_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        outline_id: dup
        name: Dup
        version: 1.0.0
        topics:
          - id: same
            label: One
          - id: same
            label: Two
        """,
    )
    with pytest.raises(OutlineLoadError, match="duplicate topic id"):
        OutlineLoader().load(p)


def test_invalid_strategy_default_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        outline_id: strat
        name: Strat
        version: 1.0.0
        topics:
          - id: leaf
            label: Leaf
        strategy:
          default: Z
        """,
    )
    with pytest.raises(OutlineLoadError, match="strategy.default"):
        OutlineLoader().load(p)


def test_vocabulary_for_unknown_leaf_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        outline_id: v
        name: V
        version: 1.0.0
        topics:
          - id: leaf
            label: Leaf
        vocabulary:
          not-a-leaf:
            keywords: ["x"]
        """,
    )
    with pytest.raises(OutlineLoadError, match="does not match any leaf"):
        OutlineLoader().load(p)
