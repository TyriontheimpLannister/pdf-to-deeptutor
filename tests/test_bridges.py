"""Unit tests for the Mode C BridgeProvider abstraction.

Domain-neutral port of the upstream ``test_bridges.py``: the example
titles use generic section labels rather than subject-specific terms.
"""
from __future__ import annotations

import pytest

from pdf2dt.export.bridges import (
    BridgeContext,
    MockBridgeProvider,
    NoOpBridgeProvider,
    known_bridge_providers,
    register_bridge_provider,
    resolve_bridge_provider,
)


def test_mock_provider_returns_named_bridge() -> None:
    """MockBridgeProvider must always return a non-empty Bridge
    with ``name='mock'`` and the previous / next titles baked
    into the text."""
    ctx = BridgeContext(
        follows_plan_id="plan-a-001",
        follows_topic_id="topic-a",
        follows_title="Section A",
        follows_item_count=4,
        next_plan_id="plan-b-002",
        next_topic_id="topic-b",
        next_title="Section B",
        next_item_count=3,
    )
    bridge = MockBridgeProvider().generate_bridge(ctx)
    assert bridge is not None
    assert bridge.provider == "mock"
    assert "Section A" in bridge.text
    assert "Section B" in bridge.text
    # Bridge remembers the previous plan / topic for provenance.
    assert bridge.follows_plan_id == "plan-a-001"
    assert bridge.follows_topic_id == "topic-a"


def test_noop_provider_returns_none() -> None:
    """NoOpBridgeProvider must always return ``None`` so the
    planner can short-circuit without inserting anything."""
    ctx = BridgeContext(
        follows_plan_id="x",
        follows_topic_id="y",
        follows_title="A",
        follows_item_count=0,
        next_plan_id="z",
        next_topic_id="w",
        next_title="B",
        next_item_count=0,
    )
    assert NoOpBridgeProvider().generate_bridge(ctx) is None


def test_resolve_bridge_provider_default_is_mock() -> None:
    """``None`` must resolve to the default mock provider so the
    default CLI UX needs no extra flag."""
    p = resolve_bridge_provider(None)
    assert p.name == "mock"


def test_resolve_bridge_provider_by_name() -> None:
    assert resolve_bridge_provider("mock").name == "mock"
    assert resolve_bridge_provider("noop").name == "noop"


def test_resolve_bridge_provider_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown bridge provider"):
        resolve_bridge_provider("does-not-exist")


def test_resolve_bridge_provider_passes_instances_through() -> None:
    """Passing a BridgeProvider instance directly is supported
    so test doubles don't have to register themselves."""
    sentinel = NoOpBridgeProvider()
    assert resolve_bridge_provider(sentinel) is sentinel


def test_register_bridge_provider_appends() -> None:
    """A registered provider becomes the resolution target by name."""

    class OneOffProvider:
        name = "unit-test-oneoff"

        def generate_bridge(self, context: BridgeContext) -> str:  # noqa: ARG002 - sanity
            return "always-this-string"

    try:
        register_bridge_provider(OneOffProvider())
        assert "unit-test-oneoff" in known_bridge_providers()
        resolved = resolve_bridge_provider("unit-test-oneoff")
        assert resolved.name == "unit-test-oneoff"
    finally:
        # Clean up so the global registry does not leak across tests.
        from pdf2dt.export.bridges import _PROVIDERS

        _PROVIDERS.pop("unit-test-oneoff", None)


def test_register_rejects_non_provider() -> None:
    class NotAProvider:
        name = "fake"

    with pytest.raises(TypeError, match="BridgeProvider"):
        register_bridge_provider(NotAProvider())  # type: ignore[arg-type]
