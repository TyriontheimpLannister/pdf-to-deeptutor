# Figure descriptions — deterministic template generation

## Decision

Add a deterministic, template-based figure description generator
to satisfy `AGENTS.md`'s Definition of done:

> The MVP is complete when ... PDFs that ... include figure
> descriptions and evidence-tagged geometry relations.

A new :mod:`pdf2dt.geometry.describe` module turns one
:class:`GeometryFigure` into a list of natural-language
sentences, one sentence per relation, grouped by the figure's
canonical points.  No model call.  No external state.

## Why this shape

* `AGENTS.md` explicitly requires figure descriptions; the
  current renderer only emits bullet relations, which fails
  the deep-tutor-friendly test (and a screen-reader / blind
  student) without prose.
* A VLM-backed description would violate the project principle
  "Prefer deterministic transformations before model-based
  rewriting" and add cost / latency to a stage that already
  runs on the local laptop.  The relations themselves are
  structured; a small template is enough to make them
  human-readable.
* The description is *additional*, not replacement.  The
  bullet relations stay in the PDF for tooling that prefers
  the structured form.

## How

### Public surface

* :func:`describe_figure` — return ``list[str]`` of
  sentences, one per *includable* relation (review state
  ``confirmed`` or ``corrected``).  Empty list when no
  relations survive the review filter.
* :func:`describe_figure_block` — return a single string
  suitable for an italicized figure caption, joining the
  sentences with a Chinese full stop / English period.

### Templates

Each relation type maps to a bilingual template pair.  The
template is selected by the analyzer's locale hint, falling
back to English.  Sample templates:

| Relation            | English                                   | Chinese                                    |
|---------------------|-------------------------------------------|--------------------------------------------|
| parallel            | "AB is parallel to DE."                   | "AB 平行于 DE。"                            |
| perpendicular       | "AB is perpendicular to DE."              | "AB 垂直于 DE。"                            |
| equal_length        | "AB and DE have equal length."            | "AB 与 DE 等长。"                           |
| equal_angle         | "∠ABC equals ∠DEF."                       | "∠ABC = ∠DEF。"                             |
| midpoint            | "D is the midpoint of AB."                | "D 是 AB 的中点。"                          |
| collinear           | "A, B, C are collinear."                  | "A, B, C 三点共线。"                        |
| point_on_segment    | "D lies on segment AB."                   | "D 在线段 AB 上。"                          |

* Entity order is preserved from the analyzer's canonical
  ordering (no alphabetical sort) so the description matches
  the figure's own labeling.
* When ``entities`` is exactly two segments, the template
  inserts them directly.  When the analyzer uses one-segment
  forms (e.g. ``equal_length`` with a single segment list),
  the template degrades to "AB has equal length to the
  side marked with matching tick."  This keeps deterministic
  behaviour even for under-specified relations.

### Review gating

* The generator *only* emits a sentence for relations whose
  ``review_state`` is in :data:`INCLUDABLE_REVIEW_STATES`.
  Unreviewed ``visual_inference`` and ``unknown`` relations
  are *silently* dropped, matching the renderer's
  relation-embedding behaviour.  The user never sees a
  description that depends on a non-confirmed relation.
* When the user later reviews a previously-dropped relation
  (e.g. corrects it), the next pipeline run regenerates the
  description and the description appears in the export.

### Renderer integration

* The renderer's :meth:`PdfRenderer._render_figure` calls
  :func:`describe_figure_block` *after* embedding the
  relation bullets.  The description is written as a
  paragraph (not a caption) so screen readers and DeepTutor
  text extraction pick it up alongside the body text.
* When ``describe_figure_block`` returns an empty string
  (no confirmed relations), the renderer falls back to the
  existing caption-only behaviour — no behaviour change for
  projects without geometry content.

## Trade-offs

* Templates are static.  A future VLM-backed ``describer``
  protocol could replace :func:`describe_figure_block` for
  callers that need richer prose; the public surface stays
  the same.
* The current rule-based analyzer still misses some
  relations (e.g. textual English without keyword matches).
  The description generator inherits this gap — it is
  *not* a way to surface relations the analyzer missed.
  This is documented in :mod:`pdf2dt.geometry.describe`.
* Sentence-level naturalness is "good enough" not
  publication-grade.  A textbook editor would still want to
  hand-edit.  The deterministic generation is a strict
  improvement over bullet-only output without committing to
  a model dependency.
