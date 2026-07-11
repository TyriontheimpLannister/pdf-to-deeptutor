"""Deterministic, template-based figure descriptions.

The module is intentionally small and dependency-free.  The
goal is to satisfy ``AGENTS.md``'s Definition of done by
turning each confirmed/corrected :class:`GeometryRelation` on
a :class:`GeometryFigure` into a single natural-language
sentence, then concatenating them into a paragraph for the
PDF renderer.

Design constraints
------------------

* No model call.  Every output is a static template selected
  by the relation type.
* Review gating: relations whose review state is not
  :data:`INCLUDABLE_REVIEW_STATES` are silently dropped.  The
  user never sees a sentence that depends on an
  ``unreviewed`` relation.
* Bilingual templates (English + Chinese).  The analyzer
  records the source language on each figure (``zh`` when
  Chinese characters appear in the text, ``en`` otherwise);
  the description follows that hint.
* The module is the only piece that touches the description
  contract.  Adding a VLM-backed describer later means
  replacing :func:`describe_figure_block` without changing
  the renderer.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .evidence import INCLUDABLE_REVIEW_STATES
from .models import GeometryFigure, GeometryRelation, RelationType

# ---------------------------------------------------------------------- #
# Locale detection
# ---------------------------------------------------------------------- #

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def detect_locale(text: str) -> str:
    """Return ``"zh"`` when the text contains CJK characters,
    else ``"en"``.

    The detector is deliberately simple.  If the analyzer ever
    records a per-project language hint, swap this for a
    single ``dict.get``.
    """
    if not text:
        return "en"
    if _CJK_RE.search(text):
        return "zh"
    return "en"


# ---------------------------------------------------------------------- #
# Templates
# ---------------------------------------------------------------------- #

# Each entry maps RelationType to (english_template, chinese_template).
# ``{a}`` and ``{b}`` are substituted with entity labels in the
# analyzer's canonical order.  ``{angle}`` is substituted with the
# three-point angle label like ``ABC``.
_TEMPLATES: dict[RelationType, dict[str, str]] = {
    RelationType.PARALLEL: {
        "en": "{a} is parallel to {b}.",
        "zh": "{a} 平行于 {b}。",
    },
    RelationType.PERPENDICULAR: {
        "en": "{a} is perpendicular to {b}.",
        "zh": "{a} 垂直于 {b}。",
    },
    RelationType.EQUAL_LENGTH: {
        "en": "{a} and {b} have equal length.",
        "zh": "{a} 与 {b} 等长。",
    },
    RelationType.EQUAL_ANGLE: {
        "en": "∠{angle} and ∠{angle2} are equal.",
        "zh": "∠{angle} = ∠{angle2}。",
    },
    RelationType.MIDPOINT: {
        "en": "{a} is the midpoint of {b}.",
        "zh": "{a} 是 {b} 的中点。",
    },
    RelationType.COLLINEAR: {
        "en": "{a}, {b}, and {c} are collinear.",
        "zh": "{a}、{b}、{c} 三点共线。",
    },
    RelationType.POINT_ON_SEGMENT: {
        "en": "{a} lies on segment {b}.",
        "zh": "{a} 在线段 {b} 上。",
    },
}


# ---------------------------------------------------------------------- #
# Sentence rendering
# ---------------------------------------------------------------------- #


def _join_entities(entities: list[str]) -> tuple[str, str, str, str, str]:
    """Split a relation's entities into a/b/c and angle/angle2 fields.

    The mapping is deterministic:
    * 1-entity list: ``a = entities[0]``; rest empty.
    * 2-entity list: ``a = entities[0]``, ``b = entities[1]``.
    * 3-entity list (collinear / point_on_segment): ``a, b, c`` set.
    * EQUAL_ANGLE: first three entities are ``angle``; the next three
      (if any) become ``angle2``.
    """
    a = entities[0] if len(entities) >= 1 else ""
    b = entities[1] if len(entities) >= 2 else ""
    c = entities[2] if len(entities) >= 3 else ""
    angle = "".join(entities[:3]) if len(entities) >= 3 else (
        "".join(entities) if entities else ""
    )
    angle2 = "".join(entities[3:6]) if len(entities) >= 6 else ""
    return a, b, c, angle, angle2


def _format(template: str, relation: GeometryRelation) -> str:
    a, b, c, angle, angle2 = _join_entities(list(relation.entities))
    if relation.type == RelationType.EQUAL_ANGLE:
        return template.format(angle=angle, angle2=angle2)
    if relation.type == RelationType.COLLINEAR:
        return template.format(a=a, b=b, c=c)
    return template.format(a=a, b=b)


def _sentence(relation: GeometryRelation, locale: str) -> str | None:
    """Return one sentence for *relation* in the requested locale,
    or ``None`` when the relation cannot be described."""
    templates = _TEMPLATES.get(relation.type)
    if templates is None:
        return None
    template = templates.get(locale) or templates["en"]
    return _format(template, relation)


# ---------------------------------------------------------------------- #
# Public surface
# ---------------------------------------------------------------------- #


def _includable_relations(figure: GeometryFigure) -> list[GeometryRelation]:
    return [
        r for r in figure.relations if r.review_state in INCLUDABLE_REVIEW_STATES
    ]


def describe_figure(
    figure: GeometryFigure,
    *,
    locale: str | None = None,
) -> list[str]:
    """Return one sentence per includable relation on *figure*.

    Relations whose review state is not
    :data:`INCLUDABLE_REVIEW_STATES` are silently dropped.  When
    *locale* is ``None`` the function picks the locale from the
    figure's points (presence of CJK characters in any relation's
    entities).  Pass an explicit locale to override.
    """
    chosen = locale or _detect_locale_for_figure(figure)
    return [
        s
        for s in (
            _sentence(r, chosen)
            for r in _includable_relations(figure)
        )
        if s
    ]


def describe_figure_block(
    figure: GeometryFigure,
    *,
    locale: str | None = None,
) -> str:
    """Return a paragraph for *figure* suitable for an italic caption.

    The block is the sentences from :func:`describe_figure`
    joined by the locale-appropriate sentence separator
    (``" "`` for English, ``""`` for Chinese because each
    sentence already ends in a full-width period).  Returns
    ``""`` when no relation is includable — callers must
    treat that as "no description available, fall back to
    the figure's own caption".
    """
    sentences = describe_figure(figure, locale=locale)
    if not sentences:
        return ""
    chosen = locale or _detect_locale_for_figure(figure)
    sep = "" if chosen == "zh" else " "
    return sep.join(sentences)


def _detect_locale_for_figure(figure: GeometryFigure) -> str:
    for rel in figure.relations:
        for ent in rel.entities:
            if detect_locale(str(ent)) == "zh":
                return "zh"
    return "en"


def format_relation_bullets(
    figure: GeometryFigure,
    *,
    include_reviewed_only: bool = True,
) -> Iterable[str]:
    """Yield one bullet string per relation, mirroring the renderer
    format.

    Kept in this module (instead of the renderer) so the bullet
    and prose forms share the same includability filter.
    """
    relations = (
        _includable_relations(figure)
        if include_reviewed_only
        else list(figure.relations)
    )
    for rel in relations:
        yield f"  • {rel.type.value} ({rel.evidence.value}): {', '.join(rel.entities)}"


__all__ = [
    "describe_figure",
    "describe_figure_block",
    "detect_locale",
    "format_relation_bullets",
]
