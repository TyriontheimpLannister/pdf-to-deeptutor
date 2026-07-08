# Architecture

## Deployment model

The application runs locally on the user's primary computer.

External services may include:

- MinerU — invoked manually by the user; the pipeline only reads its output
  from the inbox directory;
- a vision-language model for figure analysis (Phase 3);
- an optional language model for normalization and regrouping (Mode C).

The industrial PC remains responsible only for hosting DeepTutor and serving
imported knowledge.

## Core modules

### Project manager

Creates and opens project workspaces, persists metadata, and controls stage
state.

### Source ingestor

Validates and copies the original PDF into immutable project storage.

### MinerU loader (folder-based, no API)

Replaces the earlier "MinerU adapter" concept. Scans the inbox directory,
validates each task's `meta.json`, locks the task, copies raw artifacts into
the project workspace, and records fingerprints. Does not perform any
network I/O.

### Asset localizer

Finds remote image references across all MinerU artifacts, downloads them,
verifies content, hashes files, deduplicates assets, and rewrites
references.

### Normalizer

Converts provider-specific output into a stable internal representation.
Handles headings, paragraphs, formulas, page markers, captions, and image
references.

### Structure extractor

Builds the `BookView` from the normalized document — chapters, items,
figures, and stable IDs. Operates on the whole book before any slicing.

### Outline matcher

Given an outline YAML from `./outlines/` (or `--outline <path>`), assigns
each item to one or more outline topics using the vocabulary. Records a
`TopicAssignment` per item. Unmatched items are tagged `_unclassified`.

### Reorganizer + Export planner

Applies the active reorganization mode (A / B / C) and produces one
`ExportPlan` per topic leaf plus, when needed, a `_misc` plan for
unclassified items.

### Segmenter (legacy name)

The legacy "segmenter" role is now split between `Structure extractor`,
`Outline matcher`, and `Reorganizer + Export planner`. The original module
name is kept here only for backwards documentation references.

### figure analysis analyzer

Associates figures with nearby content and emits structured relations with
evidence types and confidence. Deferred to Phase 3.

### Review manager

Stores warnings, user corrections, approvals, and provenance.

### Export builder

Renders selected content units into self-contained PDFs with native text
and embedded figures.

### Validator

Checks project integrity, asset availability, formula balance, figure
references, evidence rules, and export readiness.

### Optional DeepTutor adapter

Copies exports to the server or invokes a documented batch-import command.
This remains separate from preprocessing.

## Layer boundaries

- Provider adapters must not define the internal data model.
- Exporters must consume normalized internal content, not raw MinerU
  responses.
- The UI must call pipeline services rather than embedding transformation
  logic.
- Validation must run independently from the UI and export renderer.
- Model-generated data must always retain provenance and review state.
- The `BookView` is the single source of truth for slicing; nothing below
  Stage 4 reads raw MinerU output.

## Storage strategy

Each project is a directory containing immutable input, raw provider output,
localized assets, normalized data, review data, exports, and logs.

Suggested layout:

```text
project/
├── source/
├── providers/mineru/raw/
├── assets/
├── normalized/
├── book_view/                  ← Stage 4a output
├── topic_assignments/          ← Stage 4b output
├── export_plans/               ← Stage 4c output
├── review/
├── exports/deeptutor/
├── reports/
├── logs/
└── project.json
```

The shared inbox is at the project root, not inside any single project:

```text
pdf-to-deeptutor/
├── inbox/                      ← user drops MinerU output here
│   ├── _processing/
│   └── _archive/
├── outlines/                   ← user-supplied topic taxonomies
│   └── _templates/
├── projects/                   ← per-source-project workspaces
├── schemas/
├── docs/
└── ...
```

## Reorganization modes

The pipeline supports three explicit modes, configured per run:

- **Mode A — preserving order.** Regroup by topic; keep original order.
- **Mode B — reorder within topic clusters (default).** Regroup by
  knowledge point / method / difficulty; reorder inside each export. No
  generative rewriting.
- **Mode C — full restructure.** Free reordering plus optional transitional
  text. Requires a generative step.

When an outline is supplied, Mode B is the default. When no outline is
supplied, the planner falls back to chapter-style grouping. Unclassified
items always go to a `_misc` export.

## Technology selection criteria

Choose technologies based on:

- strong PDF and image support;
- reliable Windows operation;
- easy local packaging;
- deterministic file processing;
- testability;
- future local web UI compatibility.

A Python core is a natural default because of PDF, OCR, image, and
document-processing libraries. The local web UI is a separate component.
