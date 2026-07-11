# Geometry evidence and review pipeline (Stages 5/6)

## Decision

Implement the two remaining MVP stages around one minimal, deterministic
geometry model:

- **Stage 5 — geometry analysis.** A new `pdf2dt.geometry` package
  extracts points, segments, and typed relations from the
  figure-bound `BookItem` text plus its layout-captioned figure. The
  extractor is rule-based (no LLM call). Every relation carries an
  explicit `evidence` value from
  `docs/decisions/2026-07-08-evidence-typing.md` and a `confidence`
  between 0 and 1. A `GeometryFigure` is emitted per `BookItem` that
  binds to a real figure block; items without a figure produce no
  figure record.
- **Stage 6 — review.** A `pdf2dt.review` package persists a
  `review_queue.json` and a per-project `review_state.json` keyed by
  `figure_id` and `relation_key`. The export renderer refuses to
  include a relation whose `review_state` is not `confirmed` or
  `corrected` when the evidence is `visual_inference` or `unknown`.

Both stages live in the `review/` workspace directory, which is
already reserved by `ProjectWorkspace.STANDARD_DIRS`.

## Why this shape

- The schema (`schemas/geometry-item.schema.json`) and the evidence
  rules already exist. The remaining work is *enforcement* — making
  it impossible for the exporter to silently promote an
  unconfirmed `visual_inference`.
- A rule-based extractor fits the project principles: deterministic
  before model-based, model outputs reviewable and replaceable.
  Real VLM-based extraction can be added later as a provider behind
  the same `GeometryAnalyzer` interface, leaving the review state
  contract intact.
- Putting review state in a separate `review/` directory keeps the
  geometry model clean and lets the user iterate on review edits
  without re-running Stage 5.
- We deliberately keep the relation vocabulary small
  (point_on_segment, midpoint, collinear, parallel, perpendicular,
  equal_length, equal_angle). The schema is extensible, and the
  renderer is evidence-agnostic, so adding new relation types is a
  pure analyzer change.

## How

### Stage 5 — `pdf2dt.geometry`

Public surface:

* `Evidence` (str enum) — the five allowed values.
* `RelationType` (str enum) — closed set of supported geometry
  relation types.
* `GeometryRelation` (dataclass) — type, entities, evidence, source
  reference, confidence, review_state.
* `GeometryFigure` (dataclass) — figure_id, asset_id,
  associated_item_id, points, segments, relations, visual
  observations, review_state. Serializable to / from JSON against
  `schemas/geometry-item.schema.json`.
* `GeometryAnalyzer` — analyze one `BookItem` plus its caption /
  figure metadata, returning a `GeometryFigure` (or `None` when the
  item has no figure).
* `GeometryExtractor` (the dispatcher) — walk every figure-bound
  `BookItem` in the BookView, call the analyzer, and persist the
  collection to `review/geometry_figures.json`.
* `analyze_geometry(workspace, ...)` — top-level pipeline entry that
  writes the artifact and records `stage5_geometry` in the manifest.

Extraction rules (rule-based):

* `points` are any single ASCII / Greek / Chinese-math letter
  tokens preceded by a `$` (LaTeX) or appearing as a bare
  identifier inside a `\triangle …`, `\angle …`, `\overline …` or
  segment expression.
* `segments` are `XY` pairs where `X` and `Y` are extracted points
  and the pair appears together in the text or caption.
* Relations are detected by textual patterns keyed by the natural
  language. The analyzer also inspects the layout's `caption` /
  `labels` to look for explicit marks (tick marks encoded as
  `AB = CD` ⇒ equal_length; `∠ABC = ∠DEF` ⇒ equal_angle; parallel
  via `∥` or `平行`; perpendicular via `⊥` or `垂直`; midpoint via
  `中点`; collinearity via `共线` or `在…上`).
* `evidence` is assigned by the rule: textual match ⇒
  `problem_text`; figure-only presence ⇒ `visual_inference`;
  neither ⇒ `unknown`. When the item's text and figure caption both
  contain the relation, the evidence is
  `problem_text_and_diagram_mark`. The mark side is recorded so
  future VLM providers can refine the rule.

### Stage 6 — `pdf2dt.review`

Public surface:

* `ReviewDecision` (dataclass) — relation_key, action (`confirm` |
  `correct` | `reject`), corrected entities (optional), reviewer
  note (optional), applied_at.
* `ReviewQueueEntry` — figure_id, asset_id, associated_item_id,
  relations (with current review_state), created_at.
* `ReviewStateStore` — loads / saves
  `review/geometry_figures.json` (the queue) and
  `review/review_state.json` (the applied decisions). `apply()`
  mutates queue entries' relation review_state and writes the file.
* `apply_review(workspace, decisions, ...)` — top-level pipeline
  entry that records `stage6_review` in the manifest.

Promotion rule:

* The `Evidence` enum is split into:
  * `promotable` — `{problem_text, diagram_mark,
    problem_text_and_diagram_mark}`. Allowed to be `confirmed` by
    the user. `visual_inference` and `unknown` may not be
    `confirmed`; they may only be `corrected` (textual evidence
    added) or `rejected`.
  * `non_promotable` — `{visual_inference, unknown}`. Cannot
    reach `confirmed`.

The promotion rule is enforced in `ReviewStateStore.apply()` and
also surfaced as a validation report at
`reports/geometry_review_report.json`.

### Renderer integration

`ExportPlanCollection` already carries `items`; each item is a
serialized `BookItem` dict. We add a `figure_ids` lookup table to
`ExportPlan` (already present) and a `review_figure_id_to_path`
helper in the renderer that:

* Loads `review/geometry_figures.json` once per render.
* For each figure, computes an `effective_relations` list: any
  relation whose `review_state` is `confirmed` or `corrected` is
  kept; anything else is excluded.
* If the figure loses all relations and the export was previously
  relying on it, the figure's `missing_relations` count is added
  to the render warnings.
* When *every* relation on a figure is dropped because the user
  has not reviewed them, the figure is rendered with a "geometry
  relations pending review" caption instead of a clean caption.
  This keeps the figure itself visible (an asset that the user
  confirmed exists) while making the missing review state
  explicit.
* The renderer's `validation_status` is escalated to `blocked` if
  *any* figure on the export has at least one relation that
  carries `visual_inference` or `unknown` evidence and is still
  in the `unreviewed` state. `confirmed`, `corrected`, and
  `rejected` do not block.

### Resumability

* `_run_ingest`-style helpers wrap each stage. When the manifest
  marks the stage `completed`, the runner records `SKIPPED` and
  does not re-run. Failed runs fall through to retry.
* `apply_review` is always re-runnable; the second invocation
  overlays the previous decisions (later decisions with the same
  `relation_key` win).

## Trade-offs

* The rule-based extractor is conservative and will miss some
  relations a VLM would catch. Accepted because the alternative is
  a flaky LLM call inside the deterministic pipeline. The model
  is small and reviewable, and a VLM adapter can replace it
  later behind the same `GeometryAnalyzer` interface.
* `visual_inference` and `unknown` relations are excluded from
  exports, which may surprise users who expected to see a
  relation "the VLM guessed". The alternative — silently promoting
  them — is explicitly forbidden by the project's evidence rules.
* We do not yet emit figure descriptions (Stage 5 produces
  relations; the renderer renders an "evidence under review"
  caption). Concise natural-language descriptions remain a future
  provider; the existing figure caption is preserved either way.
