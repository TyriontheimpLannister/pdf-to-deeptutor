# Decision log

Project-level decisions that are stable enough to outlive a single
release. Each file in this directory captures one decision, the
alternatives considered, and the rationale. The root `HANDOFF.md` (if
present) and `docs/PRODUCT_SPEC.md` reference the active decisions;
new ones are appended here so they remain small and findable.

## Active decisions

- `2026-07-08-manual-mineru.md` — MinerU is invoked manually by the user
  (web or desktop client); the tool reads a folder of artifacts, not
  an API. Works for any subject area that MinerU supports.
- `2026-07-08-asset-localization.md` — every remote image URL becomes a
  stable local asset ID before any downstream step; no final export
  references a remote URL.
- `2026-07-08-outline-driven.md` — final export granularity and
  regrouping are driven by a user-supplied outline (a YAML taxonomy of
  topics with keyword and pattern vocabulary); unmatched items go to a
  single `_misc-<timestamp>.pdf` export so no content is dropped.
- `2026-07-08-evidence-typing.md` — figure-relationship claims carry an
  explicit `evidence` field; only `problem_text`, `diagram_mark`, and
  `problem_text_and_diagram_mark` may be promoted to confirmed facts.
- `2026-07-08-runtime-and-ui.md` — Python is the runtime; the operator
  UI is a local web dashboard only, no native desktop shell.
- `2026-07-08-reorganization-modes.md` — three explicit modes (A, B, C)
  govern how content may be reordered inside an export; default is B.

## Cross-cutting concerns

- **Domain scope.** This tool is intentionally domain-agnostic. The
  MVP development path started with figure-heavy STEM material because
  it exercises the hardest parts of the pipeline (OCR noise, complex
  figure layouts, evidence typing). Other subject areas (language
  workbooks, exam-prep booklets, early-grades readers) reuse the same
  Stage 0-2 path; Stage 5 (the figure analyzer) is opt-in and may be
  skipped entirely when figures are decorative rather than load-bearing.
- **Children's material.** Picture-heavy early-grades readers are a
  known edge case: the asset-localization step still runs, but the
  figure analyzer should be disabled. The user can pass
  `--no-figure-analysis` (or equivalent) to skip Stage 5.
