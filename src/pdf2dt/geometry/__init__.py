"""Geometry analysis — Stage 5.

The package provides a deterministic, rule-based analyzer that turns
one figure-bound :class:`~pdf2dt.bookview.builder.BookItem` into a
:class:`GeometryFigure` carrying typed points, segments, and
relations.  Every relation is tagged with an :class:`Evidence` value
so that downstream review (Stage 6) and the exporter can refuse to
treat visually inferred or unknown relations as confirmed givens.

Public surface
--------------

* :class:`Evidence` — the five allowed evidence values.
* :class:`RelationType` — closed set of supported geometry relation
  types.
* :class:`ReviewState` — the four-state review model.
* :class:`GeometryRelation` and :class:`GeometryFigure` — the JSON
  dataclasses that match ``schemas/geometry-item.schema.json``.
* :class:`GeometryAnalyzer` — single-figure analyzer.
* :func:`analyze_geometry` — pipeline entry point that walks the
  BookView and writes ``review/geometry_figures.json``.

The module is intentionally small and side-effect free except for
:func:`analyze_geometry`.  The analyzer is rule-based; a future
VLM-backed provider can live behind the same
:class:`GeometryAnalyzer` interface without breaking the review or
exporter contract.
"""
from .analyzer import (
    GeometryAnalyzer,
    analyze_geometry,
)
from .describe import (
    describe_figure,
    describe_figure_block,
    detect_locale,
    format_relation_bullets,
)
from .evidence import (
    INCLUDABLE_REVIEW_STATES,
    NON_PROMOTABLE_EVIDENCE,
    PROMOTABLE_EVIDENCE,
    Evidence,
    ReviewState,
)
from .models import (
    GeometryFigure,
    GeometryRelation,
    RelationType,
    relation_key,
)
from .resource_gate import VlmGateResult, check_vlm_asset
from .vlm import (
    HybridGeometryAnalyzer,
    MiniMaxM3Provider,
    SenseNovaProvider,
    build_geometry_analyzer,
)

__all__ = [
    "Evidence",
    "GeometryAnalyzer",
    "GeometryFigure",
    "GeometryRelation",
    "HybridGeometryAnalyzer",
    "INCLUDABLE_REVIEW_STATES",
    "NON_PROMOTABLE_EVIDENCE",
    "PROMOTABLE_EVIDENCE",
    "RelationType",
    "MiniMaxM3Provider",
    "ReviewState",
    "SenseNovaProvider",
    "VlmGateResult",
    "analyze_geometry",
    "build_geometry_analyzer",
    "check_vlm_asset",
    "describe_figure",
    "describe_figure_block",
    "detect_locale",
    "format_relation_bullets",
    "relation_key",
]
