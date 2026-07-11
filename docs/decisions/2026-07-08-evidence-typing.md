# Geometry evidence typing

## Decision

Every geometry relation extracted by the analyzer carries an explicit
`evidence` field with one of these values:

- `problem_text`
- `diagram_mark`
- `problem_text_and_diagram_mark`
- `visual_inference`
- `unknown`

Only the first three may be auto-promoted to confirmed givens.
`visual_inference` must remain a warning or review suggestion. `unknown`
is rejected from final exports until a human resolves it.

## Why

- Mathematics must not be inferred from pixels alone. A figure that
  "looks" parallel is not a given unless the text or an explicit mark
  says so.
- Reviewability requires a typed provenance on every claim.
- A single boolean "is this confirmed?" is not enough — what kind of
  confirmation matters for downstream display and pedagogy.

## How

- Schema: `schemas/geometry-item.schema.json` (deferred to Phase 3).
- The analyzer (Stage 5) emits `evidence` and `confidence` for every
  relation.
- The exporter (Stage 7) refuses to include relations whose review
  state is not `confirmed` or `corrected`.

## Trade-offs

- Adds friction to geometry analysis — every claim needs a justification.
  Accepted because silently inventing mathematical conditions is the
  worst possible failure mode for an educational tool.