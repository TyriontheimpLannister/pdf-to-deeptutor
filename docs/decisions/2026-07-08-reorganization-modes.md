# Reorganization modes

## Decision

The pipeline supports three explicit modes for how content may be
reordered inside an export. The user picks one per run (CLI flag or
`export_plan.yaml`). The default is **Mode B**.

- **Mode A — preserving order.** Regroup cross-chapter items of the
  same kind but keep each export in the source book's original order.
- **Mode B — reorder within topic clusters (default).** Regroup items
  by knowledge point, method, or difficulty and reorder them inside
  each export. Item wording is preserved verbatim; no generative
  rewriting.
- **Mode C — full restructure.** Free reordering plus optional
  transitional text. Requires a generative step. Reviewability still
  applies.

## Why

- Different users want different levels of intervention. Forcing one
  default would either be too conservative (Mode A only) or too
  aggressive (Mode C only).
- The three modes are explicit on purpose — pipeline runs must be
  reproducible, so implicit heuristics are out.
- Slicing is never based on file size or page count. Reordering is
  driven by outline topics, knowledge points, or an explicit user
  strategy.

## How

- The CLI flag is `--mode {A,B,C}`. Default is B.
- An outline may override the default per topic via
  `strategy.overrides.<topic_id>`.
- The chosen mode is recorded in every `ExportPlan` (see
  `schemas/export-plan.schema.json`) and in the project manifest.

## Trade-offs

- Mode C opens the door to LLM-rewritten exports. We accept this only
  with full review state and model provenance recorded. Mode C remains
  optional and gated behind an explicit user choice.
