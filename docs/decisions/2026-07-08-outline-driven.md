# Outline-driven export granularity

## Decision

Final export granularity and content regrouping are driven by a
user-supplied outline (a YAML taxonomy of topics with keyword and
pattern vocabulary). When no outline is supplied, the pipeline falls
back to chapter-style grouping. Unmatched items go to a single
`_misc-<timestamp>.pdf` export.

## Why

- The user knows how their material should be sliced; the tool should
  not guess.
- An outline is reusable across many books of the same subject.
- A single `_misc` fallback honors the "never discard content" rule
  while making outline coverage gaps visible.

## How

- Outline directory: `./outlines/` (relative to the project root).
- Outline schema: `schemas/outline.schema.json`.
- Outline content is content-hashed and recorded in the project
  manifest so classification results are reproducible.
- Items that match multiple topics appear in every matching export;
  figures deduplicate by SHA-256.

## Trade-offs

- Requires the user to maintain a small YAML file before some kinds of
  reorg runs.
- We do not yet inherit outlines across versions; v2 will be a
  hand-written superset of v1.
