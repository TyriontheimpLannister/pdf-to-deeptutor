"""Stage 4c export planning and Stage 7 PDF rendering.

Public surface:

* :class:`ExportPlan` — one planned PDF export (topic or `_misc`).
* :class:`ExportPlanner` — plans exports from a :class:`BookView`.
* :func:`plan_exports` — convenience wrapper that loads a project
  workspace, builds plans, persists them under ``export_plan/``, and
  records the stage in the project manifest.
* :func:`render_exports` — renders every persisted plan to a self-contained
  PDF in ``exports/`` and records Stage 7.

The default reorganization mode is **B**: items are regrouped by topic
but wording is preserved verbatim (no generative rewriting).
"""
from .bridges import (
    DEFAULT_BRIDGE_PROVIDER,
    Bridge,
    BridgeContext,
    BridgeProvider,
    BridgeProviderContext,
    GeometryBridgeProvider,
    MockBridgeProvider,
    NoOpBridgeProvider,
    OutlineBridgeProvider,
    PlanAccessor,
    known_bridge_providers,
    register_bridge_provider,
    resolve_bridge_provider,
)
from .planner import (
    ExportPlan,
    ExportPlanCollection,
    ExportPlanner,
    PlanError,
    ReorgMode,
    plan_exports,
)
from .renderer import PdfRenderer, render_exports

__all__ = [
    "ExportPlan",
    "ExportPlanCollection",
    "ExportPlanner",
    "PdfRenderer",
    "PlanError",
    "ReorgMode",
    "Bridge",
    "BridgeContext",
    "BridgeProvider",
    "BridgeProviderContext",
    "DEFAULT_BRIDGE_PROVIDER",
    "GeometryBridgeProvider",
    "MockBridgeProvider",
    "NoOpBridgeProvider",
    "OutlineBridgeProvider",
    "PlanAccessor",
    "plan_exports",
    "render_exports",
    "known_bridge_providers",
    "register_bridge_provider",
    "resolve_bridge_provider",
]
