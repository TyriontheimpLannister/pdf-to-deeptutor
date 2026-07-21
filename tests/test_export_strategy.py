from pdf2dt.export import ExportPlanner, ReorgMode
from pdf2dt.outlining import Outline, Topic


def _book_with_topic_items() -> dict:
    return {
        "chapters": [
            {
                "sections": [
                    {
                        "items": [
                            {
                                "item_id": "item-1",
                                "title": "item 1 body",
                                "text": "item 1 body content",
                                "topic_ids": ["topic-a"],
                            },
                            {
                                "item_id": "item-2",
                                "title": "item 2 body",
                                "text": "item 2 body content",
                                "topic_ids": ["topic-b"],
                            },
                            {
                                "item_id": "item-3",
                                "title": "item 3 body",
                                "text": "item 3 body content",
                                "topic_ids": ["topic-c"],
                            },
                        ]
                    }
                ]
            }
        ]
    }


def test_outline_strategy_overrides_apply_per_topic_and_only_c_gets_bridge() -> None:
    outline = Outline(
        outline_id="mixed-modes",
        name="Mixed modes",
        version="1.0.0",
        applies_to={},
        topics=(
            Topic(id="topic-a", label="A"),
            Topic(id="topic-b", label="B"),
            Topic(id="topic-c", label="C"),
        ),
        vocabulary={},
        strategy_default="B",
        strategy_overrides={"topic-a": "A", "topic-c": "C"},
    )

    collection = ExportPlanner(
        _book_with_topic_items(), mode=ReorgMode.B, outline=outline
    ).plan()

    plans = {plan.topic_id: plan for plan in collection.plans}
    assert {topic_id: plan.mode for topic_id, plan in plans.items()} == {
        "topic-a": ReorgMode.A,
        "topic-b": ReorgMode.B,
        "topic-c": ReorgMode.C,
    }
    assert plans["topic-a"].bridges == []
    assert plans["topic-b"].bridges == []
    assert len(plans["topic-c"].bridges) == 1
    assert plans["topic-c"].bridges[0].follows_topic_id == "topic-b"


def test_explicit_cli_mode_overrides_outline_strategy() -> None:
    outline = Outline(
        outline_id="mixed-modes",
        name="Mixed modes",
        version="1.0.0",
        applies_to={},
        topics=(
            Topic(id="topic-a", label="A"),
            Topic(id="topic-b", label="B"),
            Topic(id="topic-c", label="C"),
        ),
        vocabulary={},
        strategy_default="B",
        strategy_overrides={"topic-a": "A", "topic-c": "C"},
    )

    collection = ExportPlanner(
        _book_with_topic_items(), mode=ReorgMode.C, outline=outline
    ).plan()

    assert {plan.mode for plan in collection.plans} == {ReorgMode.C}
    assert sum(len(plan.bridges) for plan in collection.plans) == len(collection.plans) - 1


def test_force_mode_b_blocks_outline_override() -> None:
    """When force_mode=True, even ReorgMode.B is honoured for every topic."""
    outline = Outline(
        outline_id="mixed-modes",
        name="Mixed modes",
        version="1.0.0",
        applies_to={},
        topics=(
            Topic(id="topic-a", label="A"),
            Topic(id="topic-b", label="B"),
            Topic(id="topic-c", label="C"),
        ),
        vocabulary={},
        strategy_default="B",
        strategy_overrides={"topic-a": "A", "topic-c": "C"},
    )

    collection = ExportPlanner(
        _book_with_topic_items(),
        mode=ReorgMode.B,
        force_mode=True,
        outline=outline,
    ).plan()

    assert {plan.mode for plan in collection.plans} == {ReorgMode.B}
    assert sum(len(plan.bridges) for plan in collection.plans) == 0
