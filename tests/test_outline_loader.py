"""Tests for OutlineLoader against the real elementary-math-v1 outline.

These tests verify the contract advertised in ``src/pdf2dt/outlining/outline.py``:

* Outline loading succeeds against the canonical outline file.
* Counts and identifiers match the frozen v1.0.0 outline state.
* Validation raises :class:`OutlineLoadError` for every documented failure mode.
* The SHA-256 captured at load time matches the raw bytes of the file.
* End-to-end smoke for the ``chapter_stopwords`` field, loading the
  shipped fixture file through OutlineLoader and feeding it to
  OutlineMatcher to confirm the scrub path fires.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from pdf2dt.outlining.outline import (
    SLUG_RE,
    Outline,
    OutlineLoader,
    OutlineLoadError,
    VocabularyEntry,
)

ROOT = Path(__file__).resolve().parents[1]
OUTLINE_PATH = ROOT / "outlines" / "elementary-math-v1.yaml"

EXPECTED_KEYWORDS_GEOMETRY_TRIANGLES = (
    "三角形",
    "锐角三角形",
    "直角三角形",
    "钝角三角形",
    "等腰三角形",
    "等边三角形",
    "三角形的高",
    "三角形的中线",
    "三角形的角平分线",
    "三角形的三边关系",
    "三角形的内角和",
)


@pytest.fixture
def outline() -> Outline:
    return OutlineLoader().load(OUTLINE_PATH)


def test_loads_real_outline_metadata(outline: Outline) -> None:
    assert outline.outline_id == "elementary-math-v1"
    assert outline.version == "1.0.1"
    assert outline.name  # non-empty human-readable name


def test_leaf_and_vocab_counts(outline: Outline) -> None:
    leaves = outline.leaves()
    assert len(leaves) == 113
    assert len(outline.vocabulary) == 113
    # Vocabulary must not exceed leaves — every vocabulary key must be a leaf id.
    assert set(outline.vocabulary.keys()).issubset({leaf.id for leaf in leaves})


def test_all_leaf_ids_match_slug_regex(outline: Outline) -> None:
    bad = [leaf.id for leaf in outline.leaves() if not SLUG_RE.match(leaf.id)]
    assert bad == []


def test_no_vocabulary_entry_for_non_leaf(outline: Outline) -> None:
    leaf_ids = {leaf.id for leaf in outline.leaves()}
    non_leaf_vocab = [k for k in outline.vocabulary if k not in leaf_ids]
    assert non_leaf_vocab == []


def test_strategy_overrides_are_the_four_comprehensive_blocks(outline: Outline) -> None:
    expected = {
        "comprehensive-explore",
        "comprehensive-word",
        "comprehensive-typical",
        "comprehensive-strategies",
    }
    assert set(outline.strategy_overrides.keys()) == expected
    assert set(outline.strategy_overrides.values()) == {"A"}


def test_leaves_preserve_declaration_order(outline: Outline) -> None:
    leaves = outline.leaves()
    assert len(leaves) == 113
    # Spot-check ordering: the first leaf is the first leaf of the first top-level topic
    # (num-and-ops → num-and-ops-integers → num-and-ops-integers-classify).
    assert leaves[0].id == "num-and-ops-integers-classify"


def test_vocabulary_for_geometry_plane_triangles(outline: Outline) -> None:
    entry = outline.vocabulary_for("geometry-plane-triangles")
    assert isinstance(entry, VocabularyEntry)
    assert tuple(entry.keywords) == EXPECTED_KEYWORDS_GEOMETRY_TRIANGLES


def test_sha256_matches_raw_bytes(outline: Outline, tmp_path: Path) -> None:
    raw = OUTLINE_PATH.read_text(encoding="utf-8")
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert outline.sha256 == expected


# ---------------------------------------------------------------------- #
# OutlineLoadError coverage
# ---------------------------------------------------------------------- #


def _write_minimal_yaml(
    tmp_path: Path,
    topics_block: str,
    *,
    vocabulary_block: str = "",
    strategy_block: str = "strategy:\n  default: B\n",
    header: str = "outline_id: t\nname: T\nversion: 1.0.0\n",
) -> Path:
    """Write a minimal outline YAML file with controllable sections."""
    body = header
    if topics_block:
        body += "topics:\n" + textwrap.indent(topics_block, "  ")
    if vocabulary_block:
        body += "vocabulary:\n" + textwrap.indent(vocabulary_block, "  ")
    if strategy_block:
        body += strategy_block
    p = tmp_path / "outline.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OutlineLoadError, match="not found"):
        OutlineLoader().load(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(": just: : invalid :: yaml ::\n  - [unterminated", encoding="utf-8")
    with pytest.raises(OutlineLoadError, match="invalid YAML"):
        OutlineLoader().load(p)


def test_missing_required_keys_raises(tmp_path: Path) -> None:
    # No 'outline_id' / 'topics'.
    p = tmp_path / "missing.yaml"
    p.write_text("name: T\nversion: 1.0.0\n", encoding="utf-8")
    with pytest.raises(OutlineLoadError, match="missing required key"):
        OutlineLoader().load(p)


def test_duplicate_topic_id_raises(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: dup
              label: A
            - id: dup
              label: B
            """
        ),
    )
    with pytest.raises(OutlineLoadError, match="duplicate topic id"):
        OutlineLoader().load(p)


def test_non_slug_topic_id_raises(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: "Bad Id With Spaces"
              label: A
            """
        ),
    )
    with pytest.raises(OutlineLoadError, match="not a slug"):
        OutlineLoader().load(p)


def test_vocabulary_key_must_match_leaf(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: known-leaf
              label: Leaf
            """
        ),
        vocabulary_block=textwrap.dedent(
            """\
            unknown-leaf:
              keywords: ["x"]
            """
        ),
    )
    with pytest.raises(OutlineLoadError, match="does not match any leaf"):
        OutlineLoader().load(p)


def test_invalid_regex_in_patterns_raises(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: t-leaf
              label: Leaf
            """
        ),
        vocabulary_block=textwrap.dedent(
            """\
            t-leaf:
              patterns: ["["]
            """
        ),
    )
    with pytest.raises(OutlineLoadError, match="invalid regex"):
        OutlineLoader().load(p)


def test_chapter_stopwords_roundtrip(tmp_path: Path) -> None:
    """``chapter_stopwords`` on a parent topic must surface verbatim
    on ``Outline.chapter_stopwords_for`` for each descendant leaf,
    along the path top-down."""
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: parent-chapter
              label: Parent chapter
              chapter_stopwords: ["本章主要学习", "考点"]
              children:
                - id: middle
                  label: Middle group
                  chapter_stopwords: ["专题"]
                  children:
                    - id: leaf-a
                      label: Leaf A
                    - id: leaf-b
                      label: Leaf B
            """
        ),
    )
    outline = OutlineLoader().load(p)
    # Order is top-down: parent's stopwords first, then middle's.
    assert outline.chapter_stopwords_for("leaf-a") == (
        "本章主要学习",
        "考点",
        "专题",
    )
    assert outline.chapter_stopwords_for("leaf-b") == (
        "本章主要学习",
        "考点",
        "专题",
    )
    # Unknown leaf must return an empty tuple, not raise.
    assert outline.chapter_stopwords_for("does-not-exist") == ()


def test_chapter_stopwords_must_be_list(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: t-leaf
              label: Leaf
              chapter_stopwords: "本章主要学习"
            """
        ),
    )
    with pytest.raises(
        OutlineLoadError,
        match=r"'chapter_stopwords'.*must be a list",
    ):
        OutlineLoader().load(p)


def test_invalid_strategy_mode_raises(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: t-leaf
              label: Leaf
            """
        ),
        strategy_block="strategy:\n  default: B\n  overrides:\n    t-leaf: Z\n",
    )
    with pytest.raises(OutlineLoadError, match="override for 't-leaf' must be A, B, or C"):
        OutlineLoader().load(p)


def test_negative_keywords_load(outline: Outline) -> None:
    """The v1.0.1 outline attaches ``negative_keywords`` to
    ``typical-chickens-rabbits`` so the matcher can veto it on
    geometry-heavy chapter summaries. The loader must surface those
    terms on the vocabulary entry unchanged."""
    entry = outline.vocabulary_for("typical-chickens-rabbits")
    assert entry.negative_keywords, (
        "expected typical-chickens-rabbits to carry negative_keywords "
        "(v1.0.1)"
    )
    # All three document the geometry overlap documented in HANDOFF.
    assert "三角形" in entry.negative_keywords
    assert "全等" in entry.negative_keywords
    assert "直角" in entry.negative_keywords
    # No problem-only terms — they would veto genuine chickens-rabbits
    # problems too.
    for forbidden in ("笼中有", "足", "鸡", "兔", "各几何"):
        assert forbidden not in entry.negative_keywords


def test_invalid_regex_in_negative_patterns_raises(tmp_path: Path) -> None:
    p = _write_minimal_yaml(
        tmp_path,
        topics_block=textwrap.dedent(
            """\
            - id: t-leaf
              label: Leaf
            """
        ),
        vocabulary_block=textwrap.dedent(
            """\
            t-leaf:
              keywords: ["x"]
              negative_patterns: ["["]
            """
        ),
    )
    with pytest.raises(OutlineLoadError, match=r"invalid regex.*negative_patterns"):
        OutlineLoader().load(p)


# ---------------------------------------------------------------------- #
# End-to-end chapter_stopwords smoke against the shipped fixture
# ---------------------------------------------------------------------- #


def test_chapter_stopwords_end_to_end_against_shipped_fixture() -> None:
    """Load the shipped ``chapter-noise-fixture.yaml`` through the
    real OutlineLoader and feed it into OutlineMatcher to confirm
    that the loader → matcher chain delivers the chapter_stopwords
    and the matcher actually scrubs them on real YAML output.

    This is the only e2e pin we have on the contract — it goes from
    raw text → dataclass → matcher → assignment, so any future refactor
    that drops the field, miscomputes top-down order, or fails to
    wire the scrub path breaks here.
    """
    from pdf2dt.outlining.items import Item
    from pdf2dt.outlining.matcher import OutlineMatcher

    fixture = (
        Path(__file__).resolve().parents[1]
        / "outlines"
        / "examples"
        / "chapter-noise-fixture.yaml"
    )
    assert fixture.is_file(), (
        f"fixture file is missing at {fixture}; cannot run e2e smoke"
    )

    outline = OutlineLoader().load(fixture)
    # Loader must round-trip both the parent chapter's stopword list
    # AND make the chapter-scoped leaf reach it through ancestry.
    funcs = next(t for t in outline.topics if t.id == "chapter-functions-and-equations")
    assert funcs.chapter_stopwords == ("本章主要学习", "考点", "专题一"), (
        f"parent chapter stopwords lost in loader: "
        f"{funcs.chapter_stopwords}"
    )
    # Top-down order test — the chapter-functions-and-equations
    # inheritance chain has only one parent, so the union should
    # equal the parent's list verbatim.
    assert outline.chapter_stopwords_for("functions") == ("本章主要学习", "考点", "专题一")
    assert outline.chapter_stopwords_for("equations") == ("本章主要学习", "考点", "专题一")
    # The sibling chapter has none.
    assert outline.chapter_stopwords_for("triangles") == ()

    # Now drive the matcher and confirm the scrub actually drops
    # the chapter stopword from a keyword hit on the equations leaf.
    item = Item(
        item_id="fix-0001",
        item_type="other",
        title="考点讲解",
        text="本章主要学习 考点讲解方程的解法；其中未知数 x 是关键。",
    )
    matcher = OutlineMatcher(outline, min_score=1, max_topics_per_item=4)
    equations_vocab = outline.vocabulary_for("equations")
    detail = matcher._score(item, "equations", equations_vocab)
    # 方程 and 未知数 are non-stopword keywords and must survive.
    assert "方程" in detail.keyword_hits
    assert "未知数" in detail.keyword_hits
    # 考点 is a chapter stopword for the chapter; the scrub path
    # must have removed it from the substring check.
    assert "考点" not in detail.keyword_hits, (
        f"chapter stopword leaked into keyword_hits: {detail.keyword_hits}"
    )
    # And the detail must record the applied scrub for audit.
    assert "考点" in detail.chapter_stopwords_applied
    assert "本章主要学习" in detail.chapter_stopwords_applied
