# Historical HANDOFF snapshot — 2026-07-08 (pre-protocol)

> This file is an **archive**. The active HANDOFF.md at the project root
> follows the multi-agent project protocol template.
> Read the root `HANDOFF.md` first; only consult this file when you need
> the design rationale that preceded the protocol-aligned rewrite.

---

# HANDOFF.md

## Current status

Project initialized. Documentation and design decisions are stable.
Stages 0, 1, and 2 of the pipeline are implemented and tested
end-to-end (22 pytest cases, all passing). Two real-world runs are
present:

- `projects/demo-g8-triangle/` — synthetic fixture (4 figures, full type
  mix). Demonstrates the canonical happy path with fixture image URLs.
- `projects/学之舟-总复习/` — real MinerU VLM export of the elementary
  math review book. Demonstrates the local-first path: VLM cropped 13
  figures (7 mind maps + 6 small blocks) into the task's `images/`, the
  loader accepts both `http(s)://`, `file://`, and `images/...`
  references, and the pipeline rewrites every reference to a stable
  local asset path.

New Stage 2 capability:

- `LocalFirstDownloader` — production downloader for real MinerU
  exports. Resolves `file://` URLs and `http(s)` URLs whose tail filename
  already exists under any configured local search path; only falls back
  to HTTP when no local copy is found.

Implemented modules:

- `src/pdf2dt/project/` — workspace creation, manifest persistence, stage
  records
- `src/pdf2dt/inbox/` — MinerU inbox loader (folder-based, no API),
  accepts relative `images/` references
- `src/pdf2dt/assets/` — asset localization (download, validate, hash,
  dedup, persist, rewrite Markdown + layout.json references). Includes
  `HttpxDownloader`, `LocalMirrorDownloader`, and `LocalFirstDownloader`.
- `src/pdf2dt/pipeline/` — orchestration runner wiring Stages 0-2
- `scripts/run_pipeline.py`, `scripts/init_inbox_meta.py` — CLI entry
  points

## Immediate objective

Produce an MVP that converts one scanned mathematics PDF (preprocessed through
MinerU manually) into a set of self-contained, topic-oriented PDF documents
suitable for DeepTutor import.

## Recommended first milestone

Implement the non-UI pipeline for one source document:

1. Initialize a project workspace.
2. Store the original PDF and project metadata.
3. Read MinerU output dropped into a fixed inbox directory by the user.
4. Preserve the raw Markdown, JSON, images, and any other MinerU artifacts.
5. Download every remote image to local permanent storage.
6. Rewrite remote image references to local asset IDs or paths.
7. Produce a normalized intermediate document with stable block IDs.
8. Build a full-book internal view (chapters, knowledge points, exercises,
   figures) before any slicing happens.
9. Apply the configured outline (if any) or the default strategy to regroup
   content into topic-oriented export units.
10. Generate self-contained PDF exports.
11. Run validation and emit a report.

Do not begin with chapter splitting, automated geometry reasoning, a desktop UI,
or a local web UI until this end-to-end path works reliably.

## Operating modes for content reorganization

The pipeline supports three explicit reorganization modes. The user picks one
per run via CLI flag or `export_plan.yaml`.

### Mode A — Preserving order

Regroup cross-chapter items of the same kind, but keep each export in the
source book's original order. The lowest-risk mode; only structural
aggregation, no reordering.

### Mode B — Default. Reorder within topic clusters (default)

Regroup items by knowledge point / method / difficulty and reorder them
inside each export. Item wording is preserved verbatim. No generative
rewriting.

### Mode C — Full restructure

Freely reorganize content inside each export, including transitional
sentences or wording rewrites. Requires a generative step. Most aggressive;
must remain reviewable per the engineering expectations.

If no mode is specified, the pipeline runs in Mode B.

## Outline-driven reorganization

A user-supplied outline (a YAML taxonomy of topics with keywords and patterns)
can drive the regrouping step. The outline is independent of any specific
source book.

- Default outline directory: `./outlines/` (relative to the project root).
- A run can target one outline via `--outline <path>`.
- An outline has a `version` and a content hash. Both are recorded in the
  project manifest so that classification results are reproducible.
- Items that match multiple topics appear in every matching export. Figure
  assets are deduplicated by SHA-256, so storage is not wasted.
- Unmatched items follow the fallback strategy below.

### Default fallback strategy for unmatched items

Items that match no topic in the chosen outline are collected into a single
export file named `_misc-<timestamp>.pdf` placed alongside the topic exports.
This honors the "never discard content" constraint and makes outline coverage
gaps visible at a glance. The validation report also lists
`unclassified_items` so the user can iteratively grow the outline.

## Key decisions already made

- Processing runs on the user's main computer, not the low-power industrial PC.
- MinerU is invoked manually by the user (web or desktop client). Its output is
  dropped into a fixed inbox directory; the pipeline never calls the MinerU
  API. See `docs/PIPELINE.md` Stage 1 for the inbox contract.
- MinerU image URLs must be downloaded immediately and treated as temporary
  transport references only.
- Final DeepTutor imports must be self-contained files.
- PDF is the preferred final import format.
- Final PDFs should contain native text and embedded figures.
- Geometry figures should also receive concise textual and structured
  descriptions.
- Visual appearance alone must not be promoted to a mathematical given.
- Final export granularity should be topic-oriented rather than one file per
  source page or per question.
- The full source book is read into an internal view before any slicing;
  slicing is structural, not size-based.
- The user may reorder or regroup content across chapters. Reorganization is
  driven by the user-supplied outline (if present) or by Mode B by default.
- The UI target is a local web dashboard only. No native desktop shell.

## Read next

- `CONTEXT_INDEX.md`
- `docs/PRODUCT_SPEC.md`
- `docs/ARCHITECTURE.md`
- `docs/PIPELINE.md`
- `docs/DATA_MODEL.md`
- `docs/EXPORT_SPEC.md`
- `docs/VALIDATION.md`
- `outlines/README.md`

## First implementation tasks

- Choose the initial language and runtime.
- Define the MinerU output loader interface (folder-based, not API-based).
- Implement project workspace creation.
- Implement asset downloading with retries, hashing, and file validation.
- Implement URL rewriting without losing source traceability.
- Implement project manifest persistence.
- Build the full-book internal view (BookView) from MinerU Markdown + JSON.
- Implement the OutlineMatcher that assigns items to outline topics.
- Implement Mode A / B export planning with stable IDs.
- Implement a minimal Markdown-to-PDF export path with embedded local images.
- Add one fixture representing a MinerU response with temporary image URLs.
- Add one blank outline template under `outlines/_templates/`.

## Open decisions

- PDF generation backend.
- Formula rendering strategy.
- When to introduce Mode C and which generative model is acceptable.
- How DeepTutor batch upload will be triggered, if included.
- Topic segmentation nuances for specific textbook styles beyond outlines.

## Constraints to preserve

- Never discard the original PDF.
- Never overwrite raw MinerU output.
- Never leave a final export dependent on a remote image URL.
- Never silently convert visual guesses into confirmed geometry conditions.
- Never mark an export ready when referenced assets are missing or unreadable.
- Never drop content because of outline coverage gaps. Send unmatched items to
  the `_misc` export and report them.
- Never reorder content based on physical size or page count. Reordering is
  driven by outline topics, knowledge points, or explicit user strategy.