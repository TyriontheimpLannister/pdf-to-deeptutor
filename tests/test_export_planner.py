"""Tests for the planner's text-noise defence."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pdf2dt.export.planner import (
    ExportPlanner,
    ReorgMode,
    _basename,
    _load_assets_basename_index,
    backfill_inline_asset_refs,
    is_intro_item,
)


def _book_view_with(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of item dicts in a minimal BookView shape."""
    return {
        "chapters": [
            {
                "chapter_id": "ch-1",
                "title": "Chapter 1",
                "sections": [{"section_id": "s-1", "title": "Section 1", "items": items}],
            }
        ]
    }


def test_planner_drops_text_noise_items() -> None:
    """The planner must apply the same noise filter as the matcher
    and the renderer so a noise item never lands in a plan — even if
    a user runs the planner without first re-running Stage 4b.
    """
    items = [
        {
            "item_id": "keep",
            "item_type": "example",
            "title": "例题 1",
            "text": "求证: 三角形 ABC 的内角和为 180 度.",
            "asset_refs": [],
        },
        {
            "item_id": "watermark",
            "item_type": "section",
            "title": "微信公众号 教辅资料站",
            "text": "微信公众号 教辅资料站",
            "asset_refs": [],
        },
        {
            "item_id": "page-num",
            "item_type": "section",
            "title": "118",
            "text": "",
            "asset_refs": [],
        },
        {
            "item_id": "symbol",
            "item_type": "section",
            "title": "#",
            "text": "#",
            "asset_refs": [],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()

    # All non-noise items should be present in the plan that the
    # planner produced (everything routes to one plan when the
    # outline is None).
    assert len(collection.plans) == 1
    plan = collection.plans[0]
    kept_ids = [it["item_id"] for it in plan.items]
    assert kept_ids == ["keep"]


def test_planner_keeps_short_titled_math_items() -> None:
    """Regression: a single-character Chinese title whose body is
    real math must NOT be filtered by the planner either.
    """
    items = [
        {
            "item_id": "xi",
            "item_type": "example",
            "title": "习",
            "text": (
                "5. 观察下图中的规律, 请按照这种规律, "
                "填出空格中的图形."
            ),
            "asset_refs": [],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()
    assert len(collection.plans) == 1
    assert [it["item_id"] for it in collection.plans[0].items] == ["xi"]


def test_deduplicate_drops_proper_subset_plan() -> None:
    """When one plan's item set is a proper subset of another, the
    subset plan is dropped and recorded in dropped_plans.
    """
    items = [
        {
            "item_id": "shared-1",
            "item_type": "example",
            "title": "Example 1",
            "text": "Content 1",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b"],
        },
        {
            "item_id": "only-b",
            "item_type": "example",
            "title": "Example 2",
            "text": "Content 2",
            "asset_refs": [],
            "topic_ids": ["topic-b"],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()

    # topic-a has only shared-1 (1 item); topic-b has shared-1 + only-b (2 items).
    # topic-a is a proper subset of topic-b and should be dropped.
    assert len(collection.plans) == 1
    assert collection.plans[0].topic_id == "topic-b"
    assert len(collection.dropped_plans) == 1
    dropped = collection.dropped_plans[0]
    assert dropped["plan_id"] == "plan-topic-a-001"
    assert dropped["superseded_by_plan_id"] == "plan-topic-b-002"
    assert dropped["reason"] == "single_item_proper_subset"


def test_deduplicate_keeps_equal_sets() -> None:
    """Two plans with identical item sets are NOT dropped — only
    *proper* subsets are removed.
    """
    items = [
        {
            "item_id": "shared-1",
            "item_type": "example",
            "title": "Example 1",
            "text": "Content 1",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b"],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()

    # Both topics have exactly the same single item.
    assert len(collection.plans) == 2
    assert len(collection.dropped_plans) == 0


def test_planner_prunes_secondary_topics_only_for_a_unique_complete_winner() -> None:
    """Stage 4c may remove duplicate exports only with decisive Stage 4b scores."""
    items = [
        {
            "item_id": "unique-winner",
            "item_type": "example",
            "title": "Example 1",
            "text": "Content 1",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b"],
            "topic_match_scores": {"topic-a": 8, "topic-b": 4},
        },
        {
            "item_id": "tied-winner",
            "item_type": "example",
            "title": "Example 2",
            "text": "Content 2",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b"],
            "topic_match_scores": {"topic-a": 8, "topic-b": 8},
        },
        {
            "item_id": "incomplete-scores",
            "item_type": "example",
            "title": "Example 3",
            "text": "Content 3",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b"],
            "topic_match_scores": {"topic-a": 8},
        },
    ]

    collection = ExportPlanner(_book_view_with(items), mode=ReorgMode.A).plan()
    by_topic = {
        plan.topic_id: {item["item_id"] for item in plan.items}
        for plan in collection.plans
    }

    assert by_topic["topic-a"] == {"unique-winner", "tied-winner", "incomplete-scores"}
    assert by_topic["topic-b"] == {"tied-winner", "incomplete-scores"}
    assert collection.topic_pruning == [
        {
            "item_id": "unique-winner",
            "kept_topic_id": "topic-a",
            "removed_topic_ids": ["topic-b"],
            "scores": {"topic-a": 8, "topic-b": 4},
            "reason": "unique_highest_match_score",
        }
    ]


def test_deduplicate_keeps_disjoint_plans() -> None:
    """Plans with no overlapping items are never dropped."""
    items = [
        {
            "item_id": "only-a",
            "item_type": "example",
            "title": "Example A",
            "text": "Content A",
            "asset_refs": [],
            "topic_ids": ["topic-a"],
        },
        {
            "item_id": "only-b",
            "item_type": "example",
            "title": "Example B",
            "text": "Content B",
            "asset_refs": [],
            "topic_ids": ["topic-b"],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()

    assert len(collection.plans) == 2
    assert len(collection.dropped_plans) == 0


def test_deduplicate_chooses_smallest_superseder() -> None:
    """When a plan is a proper subset of multiple others, the smallest
    superseding plan (by item count) is chosen.
    """
    items = [
        {
            "item_id": "shared-1",
            "item_type": "example",
            "title": "Example 1",
            "text": "Content 1",
            "asset_refs": [],
            "topic_ids": ["topic-a", "topic-b", "topic-c"],
        },
        {
            "item_id": "only-b",
            "item_type": "example",
            "title": "Example 2",
            "text": "Content 2",
            "asset_refs": [],
            "topic_ids": ["topic-b"],
        },
        {
            "item_id": "only-c-1",
            "item_type": "example",
            "title": "Example 3",
            "text": "Content 3",
            "asset_refs": [],
            "topic_ids": ["topic-c"],
        },
        {
            "item_id": "only-c-2",
            "item_type": "example",
            "title": "Example 4",
            "text": "Content 4",
            "asset_refs": [],
            "topic_ids": ["topic-c"],
        },
    ]
    planner = ExportPlanner(_book_view_with(items), mode=ReorgMode.A)
    collection = planner.plan()

    # topic-a: 1 item (shared-1)
    # topic-b: 2 items (shared-1, only-b)
    # topic-c: 3 items (shared-1, only-c-1, only-c-2)
    # topic-a is subset of both topic-b and topic-c; should choose topic-b (smaller).
    assert len(collection.plans) == 2
    kept_ids = {p.topic_id for p in collection.plans}
    assert kept_ids == {"topic-b", "topic-c"}
    assert len(collection.dropped_plans) == 1
    assert collection.dropped_plans[0]["superseded_by_plan_id"] == "plan-topic-b-002"


# -------------------------------------------------------------------- #
# Problem B — inline-asset-ref back-fill
# -------------------------------------------------------------------- #


def test_basename_strips_queries_and_urls() -> None:
    """``_basename`` must return the final filename regardless of
    whether the input is a local path, a CDN URL, or carries query /
    fragment noise from upstream Markdown renderers.
    """
    assert _basename("assets/e9f0f8fee83f.jpg") == "e9f0f8fee83f.jpg"
    assert (
        _basename("https://cdn.example.com/r/e9f0f8fee83f.jpg?x=1")
        == "e9f0f8fee83f.jpg"
    )
    assert _basename("projects/foo/assets/cc64ba3200ac.jpg") == "cc64ba3200ac.jpg"
    # Windows-style ``local_path`` from the registry: the path is
    # backslash-separated on Windows but should still resolve to the
    # filename.
    assert (
        _basename(r"projects\高思竞赛数学课本三年级\assets\bb5b02c8d103.jpg")
        == "bb5b02c8d103.jpg"
    )
    assert _basename("") == ""


def test_backfill_inline_asset_refs_resolves_marker_to_asset_id() -> None:
    """An item whose ``text`` carries an inline ``![image](...)`` marker
    must have its ``asset_refs`` populated with the resolved
    ``asset_id`` so the planner and renderer can both see it.

    Regression: Stage 3 only collected layout-block-level refs, so
    items with inline-only figures (the MinerU default for worked
    examples) were dropped from the export. See
    ``docs/handoffs/2026-07-14-problem-b-inline-refs.md``.
    """
    items = [
        {
            "item_id": "ex-001",
            "item_type": "example",
            "title": "例题 1",
            "text": (
                "观察下图。\n\n"
                "![image](assets/e9f0f8fee83f.jpg)\n\n"
                "由图可知..."
            ),
            "asset_refs": [],
            "topic_ids": ["topic-a"],
        }
    ]
    book = _book_view_with(items)
    index = {"e9f0f8fee83f.jpg": "aid-e9f0"}

    audit = backfill_inline_asset_refs(book, index)

    assert audit["items_touched"] == 1
    assert audit["refs_added"] == 1
    assert audit["markers_missing"] == 0
    asset_ids = [r["asset_id"] for r in items[0]["asset_refs"]]
    assert asset_ids == ["aid-e9f0"]
    assert items[0]["asset_refs"][0]["origin"] == "inline_marker"


def test_backfill_preserves_existing_asset_refs() -> None:
    """An item that already lists a layout-derived asset_ref must
    KEEP that entry; back-fill only adds what is missing, never
    overwrites.
    """
    items = [
        {
            "item_id": "ex-002",
            "item_type": "example",
            "title": "例题 2",
            "text": "![image](assets/e9f0f8fee83f.jpg)",
            "asset_refs": [
                {"asset_id": "aid-existing", "figure_id": "fig-existing"}
            ],
            "topic_ids": ["topic-a"],
        }
    ]
    book = _book_view_with(items)
    index = {"e9f0f8fee83f.jpg": "aid-new"}

    backfill_inline_asset_refs(book, index)

    asset_ids = [r["asset_id"] for r in items[0]["asset_refs"]]
    assert asset_ids == ["aid-existing", "aid-new"]


def test_backfill_records_unresolved_markers() -> None:
    """A marker whose filename is not in the registry must be counted
    as missing rather than silently swallowed — the audit dict lets
    operators see when MinerU emitted references that the localizer
    failed to keep.
    """
    items = [
        {
            "item_id": "ex-003",
            "item_type": "example",
            "title": "例题 3",
            "text": (
                "![image](assets/known.jpg)\n\n"
                "![image](assets/missing.jpg)\n\n"
                "![image](https://cdn.example.com/remote.jpg)"
            ),
            "asset_refs": [],
            "topic_ids": ["topic-a"],
        }
    ]
    book = _book_view_with(items)
    index = {"known.jpg": "aid-known"}

    audit = backfill_inline_asset_refs(book, index)

    assert audit["refs_added"] == 1
    assert audit["markers_missing"] == 2
    asset_ids = [r["asset_id"] for r in items[0]["asset_refs"]]
    assert asset_ids == ["aid-known"]
    # Audit surfaces an example of each kind of miss.
    blob = " ".join(audit["missing_examples"])
    assert "missing.jpg" in blob
    assert "remote.jpg" in blob


def test_backfill_is_noop_without_registry_index() -> None:
    """When the registry is unavailable (empty index), the back-fill
    must do nothing rather than crash — the planner audit metadata
    simply records a zero count and the absence of asset_refs is left
    for downstream layers to flag.
    """
    items = [
        {
            "item_id": "ex-004",
            "item_type": "example",
            "title": "例题 4",
            "text": "![image](assets/foo.jpg)",
            "asset_refs": [],
            "topic_ids": ["topic-a"],
        }
    ]
    book = _book_view_with(items)

    audit = backfill_inline_asset_refs(book, {})

    assert audit == {
        "items_touched": 0,
        "refs_added": 0,
        "markers_missing": 0,
        "missing_examples": [],
    }
    assert items[0]["asset_refs"] == []


def test_load_assets_basename_index_from_registry_file(
    tmp_path: Path,
) -> None:
    """Round-trip: a registry JSON file written to a tmp path is
    parsed into a basename → asset_id index by ``_load_assets_basename_
    index``. The cache is bypassed so the test stays hermetic.
    """
    from pdf2dt.export import planner as _planner_module

    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        (
            '{"schema_version":"registry/v1","assets":['
            '{"asset_id":"aid-1","local_path":'
            '"proj/assets/aabbccdd1122.jpg"}'
            ']}'
        ),
        encoding="utf-8",
    )
    _planner_module._REGISTRY_BASENAME_INDEX.pop(str(reg_path), None)
    try:
        idx = _load_assets_basename_index(reg_path)
    finally:
        _planner_module._REGISTRY_BASENAME_INDEX.pop(str(reg_path), None)
    assert idx == {"aabbccdd1122.jpg": "aid-1"}


# -------------------------------------------------------------------- #
# Problem C — drop chapter-type intro items from plans
# -------------------------------------------------------------------- #


def test_is_intro_item_drops_chapter_type() -> None:
    """A chapter-type item must be excluded by ``is_intro_item``.

    Regression: without this filter every plan whose outline matches
    a single keyword in the chapter-opening paragraph (e.g. "正方体"
    matches both ``geometry-plane-quads`` and ``geometry-solid-cube``)
    received the same banner image on page 1. See the comment above
    ``_INTRO_ITEM_TYPES`` in planner.py.
    """
    chapter_intro = {
        "item_id": "item-0188",
        "item_type": "chapter",
        "title": "17 立体图形认知",
        "text": "下面请大家欣赏魔术:大变活人!\n![image](assets/5d5c3c737972.jpg)",
        "asset_refs": [],
        "topic_ids": [
            "geometry-plane-quads",
            "geometry-plane-mixed",
            "geometry-solid-cube",
        ],
    }
    assert is_intro_item(chapter_intro) is True


def test_is_intro_item_keeps_section_items() -> None:
    """Section-level items carry the actual examples and exercises —
    they must NOT be filtered out by ``is_intro_item``.
    """
    examples = [
        {
            "item_id": "item-0001",
            "item_type": "section",
            "title": "例题 1",
            "text": "1+2=?\nA. 2  B. 3",
            "topic_ids": ["num-and-ops-arithmetic-order"],
        },
        {
            "item_id": "item-0002",
            "item_type": "section",
            "title": "练习 1",
            "text": "Compute 9*7.",
            "topic_ids": ["num-and-ops-arithmetic-shortcuts"],
        },
        {
            "item_id": "item-0003",
            "item_type": "section",
            "title": "本讲知识点汇总",
            "text": "要点: 加法交换律、结合律.",
            "topic_ids": ["num-and-ops-arithmetic-rules"],
        },
    ]
    for it in examples:
        assert is_intro_item(it) is False, f"section item leaked: {it['item_id']!r}"


def test_is_intro_item_handles_missing_type_gracefully() -> None:
    """Non-dict items and items without ``item_type`` must return
    False — the planner should never crash on a malformed item.
    """
    assert is_intro_item(None) is False
    assert is_intro_item("not a dict") is False
    assert is_intro_item({}) is False
    assert is_intro_item({"item_type": ""}) is False
    assert is_intro_item({"item_id": "x", "item_type": "chapter"}) is True


def test_planner_excludes_chapter_intros_from_plan_items(
    tmp_path: Path,
) -> None:
    """End-to-end: a minimal book_view with one chapter intro item
    and one section example must produce a plan whose ``items``
    contains only the section example and ``figure_ids`` does NOT
    contain the banner figure attached to the chapter intro.
    """

    book = {
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "17 立体图形认知",
                "sections": [
                    {
                        "section_id": "sec-001",
                        "title": "立体图形",
                        "items": [
                            {
                                "item_id": "item-intro",
                                "item_type": "chapter",
                                "title": "17 立体图形认知",
                                "text": (
                                    "下面请大家欣赏魔术.\n"
                                    "![image](assets/aabbccdd1122.jpg)"
                                ),
                                "asset_refs": [],
                                "topic_ids": ["topic-a", "topic-b"],
                            },
                            {
                                "item_id": "item-ex-1",
                                "item_type": "section",
                                "title": "例题 1",
                                "text": "1+1=?",
                                "asset_refs": [
                                    {
                                        "asset_id": "aid-real",
                                        "figure_id": "fig-real",
                                    }
                                ],
                                "topic_ids": ["topic-a"],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    # Pick a topic for the plan; both items are linked to topic-a so
    # the chapter intro would otherwise end up on the plan's items list.
    planner = ExportPlanner(book, mode=ReorgMode.B)
    coll = planner.plan()

    assert len(coll.plans) >= 1
    target = next(p for p in coll.plans if p.topic_id == "topic-a")
    item_ids = [it["item_id"] for it in target.items]
    assert "item-ex-1" in item_ids
    # The chapter intro must NOT appear, even though the planner
    # originally collected it from the section.
    assert "item-intro" not in item_ids


def test_planner_rescues_filtered_items_with_confirmed_content_figures() -> None:
    """Confirmed content figures survive noise and intro filtering once."""
    book = _book_view_with(
        [
            {
                "item_id": "normal",
                "item_type": "section",
                "title": "例题 1",
                "text": "计算 1+1.",
                "asset_refs": [],
                "topic_ids": ["topic-a"],
            },
            {
                "item_id": "watermark-content",
                "item_type": "section",
                "title": "微信公众号 教辅资料站",
                "text": "微信公众号 教辅资料站\n观察图形并回答问题。",
                "asset_refs": [
                    {"asset_id": "content-noise", "figure_id": "content-noise"}
                ],
                "page_refs": [8],
                "topic_ids": [],
            },
            {
                "item_id": "chapter-content",
                "item_type": "chapter",
                "title": "间隔问题",
                "text": "间隔问题\n![image](assets/content-intro.png)",
                "asset_refs": [
                    {"asset_id": "content-intro", "figure_id": "content-intro"}
                ],
                "topic_ids": ["topic-a", "topic-b"],
            },
        ]
    )

    collection = ExportPlanner(
        book,
        mode=ReorgMode.B,
        content_figure_ids={"content-noise", "content-intro"},
    ).plan()

    misc = next(plan for plan in collection.plans if plan.topic_id == "_misc")
    rescued = {item["item_id"]: item for item in misc.items}
    assert set(rescued) == {"watermark-content", "chapter-content"}
    assert rescued["watermark-content"]["title"] == "内容图（源页 8）"
    assert "微信公众号" not in rescued["watermark-content"]["text"]
    assert rescued["chapter-content"]["title"] == "间隔问题"
    assert all(item["topic_ids"] == ["_misc"] for item in rescued.values())
    assert set(misc.figure_ids) == {"content-noise", "content-intro"}

    topic_a = next(plan for plan in collection.plans if plan.topic_id == "topic-a")
    assert [item["item_id"] for item in topic_a.items] == ["normal"]
