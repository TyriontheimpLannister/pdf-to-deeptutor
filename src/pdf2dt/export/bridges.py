"""Mode C bridge generation.

A *bridge* is one transition paragraph that the planner inserts
between two adjacent export plans in Mode C, so a reader moving
from one topic's PDF to the next gets a short connective sentence
instead of an abrupt topic jump.

This module isolates the bridge-generation policy behind a small
:mod:`BridgeProvider` Protocol so:

* tests and the default project-wide UX run on
  :class:`MockBridgeProvider` (deterministic, no external deps),
* a future v2 may register a real LLM-backed implementation in
  this same package without touching the planner or renderer.

Bridges are deliberately typed as plain text + provenance: the
planner does not care which provider was used, only that the
output is a non-empty string and which preceding plan it
``follows``. The renderer typesets them as Markdown-flavoured
paragraphs (italicised, one line of vertical breathing room).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Bridge:
    """One transition paragraph between two adjacent plans.

    A bridge *belongs* to the plan it introduces: ``plans[i].bridges``
    contains the bridge(s) to render at the top of plan ``i``.
    Concretely, a bridge is positioned in the PDF stream **before**
    the first item of its owning plan, after a small spacer.
    """

    text: str
    """The transition text exactly as the renderer should typeset."""

    follows_plan_id: str | None = None
    """The plan_id of the topic this bridge comes *from*. ``None``
    for the very first plan in the collection (it has no
    predecessor)."""

    follows_topic_id: str | None = None
    """The topic_id of the preceding plan, when available. Used
    by MockBridgeProvider to produce a deterministic placeholder
    and by the renderer / review tools to surface provenance."""

    provider: str = "mock"
    """Identifier of the provider that produced this bridge. Lets
    reviews distinguish ``mock`` placeholder bridges from real
    LLM-generated ones."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Free-form provider-specific metadata (model name, prompt id,
    seed, …). Kept on the dataclass for round-trip safety; the
    planner persists it in the bridge dict but does not depend on
    its keys."""


@dataclass(frozen=True)
class BridgeContext:
    """Caller-supplied information for a single bridge generation
    call.

    The planner passes only the **preceding** plan here; the
    provider is expected to know which plan is being opened
    because :meth:`ExportPlanner.plan` invokes it in plan order
    and the bridge lives on the *next* plan.
    """

    follows_plan_id: str | None
    follows_topic_id: str | None
    follows_title: str | None
    follows_item_count: int
    next_plan_id: str
    next_topic_id: str | None
    next_title: str
    next_item_count: int


@runtime_checkable
class BridgeProvider(Protocol):
    """Pluggable bridge generator.

    Implementations must be deterministic given the same inputs
    and free of side effects (or fully reproduce them in the
    persisted :attr:`Bridge.metadata`). The planner calls
    :meth:`generate_bridge` exactly once per (previous, next)
    pair. Returning ``None`` is permitted and means "no bridge";
    the planner will skip insertion.
    """

    name: str

    def generate_bridge(self, context: BridgeContext) -> Bridge | None: ...


# ---------------------------------------------------------------------- #
# Built-in providers
# ---------------------------------------------------------------------- #


class MockBridgeProvider:
    """Deterministic placeholder bridge.

    Renders a clearly-marked placeholder so human review can
    immediately tell, in the PDF, which transitions are awaiting a
    real provider. Never raises; never returns ``None``. This is
    the default provider so the test suite and the default CLI
    UX never require external LLM access.
    """

    name = "mock"

    TEMPLATE = (
        "[Mock bridge] 本节承接自《{prev_title}》，进入《{next_title}》。"
        "（占位提示——后续可接入真实生成式过渡；Mode B 不会写入此句。）"
    )

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:
        prev_title = context.follows_title or "上文"
        next_title = context.next_title or "下一节"
        text = self.TEMPLATE.format(prev_title=prev_title, next_title=next_title)
        return Bridge(
            text=text,
            follows_plan_id=context.follows_plan_id,
            follows_topic_id=context.follows_topic_id,
            provider=self.name,
            metadata={"template": "placeholder"},
        )


class NoOpBridgeProvider:
    """Provider that never inserts anything.

    Useful for tests that want to assert "with bridges disabled,
    the rendered PDF contains zero transition paragraphs", and as
    a documented escape hatch for projects that opt out of Mode C
    while keeping the same code path.
    """

    name = "noop"

    def generate_bridge(self, context: BridgeContext) -> Bridge | None:  # noqa: ARG002 - parity with Protocol
        return None


# ---------------------------------------------------------------------- #
# Registry
# ---------------------------------------------------------------------- #


_PROVIDERS: dict[str, BridgeProvider] = {
    MockBridgeProvider.name: MockBridgeProvider(),
    NoOpBridgeProvider.name: NoOpBridgeProvider(),
}

DEFAULT_BRIDGE_PROVIDER: BridgeProvider = _PROVIDERS[MockBridgeProvider.name]


def resolve_bridge_provider(name: str | BridgeProvider | None) -> BridgeProvider:
    """Look up a provider by name, pass through instances, or
    fall back to the default.

    Raises :class:`KeyError` (via dict lookup) if the name is
    unknown so the CLI can surface a clean error to the user.
    """
    if name is None:
        return DEFAULT_BRIDGE_PROVIDER
    if isinstance(name, BridgeProvider):
        return name
    if name in _PROVIDERS:
        return _PROVIDERS[name]
    raise KeyError(
        f"Unknown bridge provider {name!r}. "
        f"Known providers: {sorted(_PROVIDERS)}"
    )


def register_bridge_provider(provider: BridgeProvider) -> None:
    """Register a custom provider.

    Used by future LLM-backed implementations to inject themselves
    into the planner. Tests can also use this to swap in a
    deterministic stub without monkey-patching.
    """
    if not isinstance(provider, BridgeProvider):
        raise TypeError(
            f"provider must satisfy BridgeProvider, got {type(provider).__name__}"
        )
    _PROVIDERS[provider.name] = provider


def known_bridge_providers() -> list[str]:
    """Snapshot of currently-registered provider names."""
    return sorted(_PROVIDERS)
