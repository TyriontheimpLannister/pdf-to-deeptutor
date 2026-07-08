# Processing pipeline

## Stage 0: project creation

Inputs:

- source PDF;
- project title;
- optional subject, grade, book, and source metadata.

Outputs:

- immutable source copy;
- initial project manifest;
- source checksum.

## Stage 1: MinerU output ingestion

MinerU is invoked manually by the user. Its output is dropped into a fixed
inbox directory whose structure is contracted below. The pipeline never calls
the MinerU API.

Inbox root: `inbox/` (relative to the project root).

Each MinerU task occupies one subdirectory named after the MinerU task ID or
a user-chosen slug. Required and optional files:

| File              | Required | Notes                                              |
| ----------------- | -------- | -------------------------------------------------- |
| `meta.json`       | Yes      | Task metadata; the pipeline entry point.           |
| `full.md`         | Yes      | Markdown main product.                             |
| `layout.json`     | No       | Structured output with bboxes and block types.     |
| `middle.json`     | No       | Alternative structured output.                     |
| `full.html`       | No       | Fallback when no Markdown is available.            |
| `source.pdf`      | No       | Original PDF; recommended for traceability.        |
| `images/`         | No       | MinerU-provided image cache; URLs in MD still rule.|
| `assets_extra/`   | No       | Other artifacts (LaTeX, docx) kept for reference.  |

`meta.json` must at minimum declare:

- `task_id`
- `source.original_filename` and `source.sha256`
- `minerU.exported_at`
- which product files are present under `products`

The loader validates completeness and refuses to proceed when a required file
is missing. Failure modes are recorded as `error` in the validation report.

### Internal inbox directories

The pipeline manages two reserved subdirectories inside `inbox/`:

- `_processing/` — currently locked tasks. Each locked task has a `.lock`
  sibling file. Tools refuse to re-enter a locked task.
- `_archive/` — completed tasks, organized by completion date.

Actions:

- scan inbox, list candidate task directories;
- validate each via `meta.json`;
- move a valid task into `_processing/`, create `.lock`;
- copy raw artifacts into `projects/<project_id>/providers/mineru/raw/`
  (originals are never overwritten);
- record input and output fingerprints in the manifest;
- on completion, move the task to `_archive/<YYYY-MM-DD>/` or leave in inbox
  per configuration.

Completion rule: the manifest records the MinerU artifact set, their SHA-256
hashes, and the source SHA-256.

## Stage 2: asset localization

Actions:

- enumerate every remote asset reference across all MinerU artifacts;
- download with retry and timeout rules;
- validate status, MIME type, size, decodability, and dimensions;
- calculate SHA-256;
- deduplicate by content hash;
- save source URL and local path mapping;
- rewrite content references to local asset IDs.

Completion rule: no required image reference depends on a remote temporary
URL.

## Stage 3: normalization

The MinerU Markdown and optional JSON are converted into a stable internal
representation.

Actions:

- create stable page and block identifiers;
- normalize headings and paragraphs;
- preserve formulas without unnecessary rewriting;
- associate image captions and page references;
- retain links to raw source blocks.

Completion rule: normalized content can be rendered without reading
provider-specific files.

## Stage 4: full-book view and reorganization

This stage produces both an internal "full-book" view and the export plan.
Slicing is structural and outline-driven. It is never based on file size or
page count.

### Stage 4a — structure extraction

- Read the entire normalized book into a `BookView` object.
- Extract the chapter outline from Markdown heading hierarchy.
- Extract exercise / example / solution / summary items with stable IDs.
- Extract figures and bind them to their closest text block(s).

### Stage 4b — outline matching (optional)

If an outline is supplied (default location `./outlines/`, overridable with
`--outline <path>`):

- Walk the outline topic tree.
- For each item, compute a topic assignment using the outline vocabulary
  (keywords and regex patterns).
- An item can match multiple topics. Multi-match items appear in every
  matching export. Figure assets are deduplicated by SHA-256.
- Items that match no topic are tagged `_unclassified`.

If no outline is supplied, every item is implicitly classified as "the whole
book" and Stage 4c plans a single chapter-style export, unless the run is
otherwise configured.

### Stage 4c — export planning

The mode of reorganization is selected by `--mode {A,B,C}` (default B):

- **Mode A — preserving order.** Regroup by topic but keep original order.
- **Mode B — reorder within topic clusters (default).** Regroup by knowledge
  point / method / difficulty; reorder inside each export. No generative
  rewriting.
- **Mode C — full restructure.** Free reordering plus optional transitional
  text. Requires a generative step.

The planner emits one `ExportPlan` per topic leaf (or per chapter when no
outline is supplied) plus, when needed, a `_misc` plan for unclassified items.

### Stage 4d — slicing

- For each plan, gather the assigned items and their referenced figures.
- Resolve cross-references inside the export so each export is self-contained.
- Apply mode-specific ordering.

### Stage 4e — completeness check

- Each export contains its referenced figures and solutions.
- No export references an item not assigned to it.
- Cross-references resolve to stable IDs.

Completion rule: every export plan is coherent, traceable, and self-contained.

## Stage 5: figure analysis

Deferred to a later phase. See `docs/DATA_MODEL.md` for the evidence and
review contract that the analyzer must follow.

## Stage 6: review

Actions:

- present validation warnings;
- review figure associations;
- review formula and OCR anomalies;
- approve or edit structured relations;
- approve export grouping.

Completion rule: blocking issues are resolved or explicitly overridden with a
recorded reason.

## Stage 7: export

Actions:

- render native text;
- embed local images;
- include concise figure descriptions;
- include source metadata and references;
- generate self-contained PDFs;
- generate export manifest.

Completion rule: PDFs open offline and pass validation.

## Stage 8: delivery

Actions:

- copy or sync exports to the DeepTutor host;
- optionally invoke batch import;
- record delivery result separately from preprocessing state.

## Resumability

Each stage must record:

- status;
- started and completed timestamps;
- input fingerprint;
- output fingerprint;
- provider or model version;
- error details;
- retry count.

A stage may be skipped only when its current input fingerprint matches the
previously completed run.
