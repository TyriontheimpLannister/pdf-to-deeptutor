# Figure-relationship evidence typing

## Decision

Every figure-relationship claim emitted by the optional analyzer (Stage
5) carries an explicit `evidence` field. Allowed values:

- `problem_text` — the surrounding text states the relationship
  explicitly. Example: "element A is linked to element B" inside the
  exercise body.
- `diagram_mark` — the figure contains an explicit visual mark
  (tick, double-arc, right-angle square, dashed line convention).
- `problem_text_and_diagram_mark` — both kinds of evidence agree.
- `visual_inference` — the claim is plausible from the figure alone,
  but neither the text nor an explicit mark states it.
- `unknown` — neither the text nor the figure provides enough
  information.

Only the first three may be auto-promoted to confirmed relations in a
final export. `visual_inference` is kept as a warning or a review-only
flag. `unknown` is rejected from final exports until a human resolves
it.

The same field name and value list apply across subject areas:

- For diagrams with structural marks: connections, groupings,
  alignment or equality marks, ordering, adjacency, etc.
- For maps and figures: compass orientation, scale bar, labeled
  landmarks, dashed versus solid borders.
- For reading-comprehension illustrations: character presence and
  position, scene framing.
- For early-grades readers: simply distinguishing "named", "marked",
  "guessed" suffices; an analyzer may run in a lighter mode here.

## Why

- Silently treating a visual guess as a confirmed fact is the worst
  failure mode for an educational tool.
- Reviewability requires a typed provenance on every claim, not a
  single boolean.
- A consistent evidence vocabulary lets the same export validator work
  across subject areas.

## How

- Schema: `schemas/figure-item.schema.json` (deferred to Phase 3).
- The analyzer (Stage 5) emits `evidence` and `confidence` for every
  relation. Stage 5 is opt-in per run; when disabled, no
  evidence-typed relations are produced.
- The exporter (Stage 7) refuses to include relations whose review
  state is not `confirmed` or `corrected`, and refuses exports where
  any referenced relation has `evidence` in the disallowed set.

## Trade-offs

- Adds friction: every claim needs a justification. Accepted because
  silently inventing domain conditions is the worst possible failure
  mode.
- Stage 5 is intentionally optional. A user who only wants clean text
  and embedded figures can skip the analyzer entirely and rely on
  textual evidence in the body.
