# Export specification

## Preferred format

PDF with selectable native text and embedded raster or vector images.

## Independence requirement

Each exported file must remain complete when copied alone. It must not
require:

- adjacent image files;
- remote URLs;
- a preserved source directory structure;
- provider-specific metadata files.

## Granularity — outline-driven, not tool-decided

The pipeline does not pick a single granularity. The user picks it via an
`export_plan.yaml` and / or the outline they supply. Reasonable patterns:

- one export per outline topic leaf (the default when an outline is
  supplied);
- one export per chapter (when no outline is supplied);
- one export per method or knowledge point when explicitly listed in the
  plan;
- one export for unmatched items, named `_misc-<timestamp>.pdf`.

Avoid:

- one export per page;
- one export per question by default;
- one export for an entire large textbook;
- mixing unrelated chapters.

See `docs/PIPELINE.md` Stage 4c for the full planning rules and the three
reorganization modes (A / B / C).

### Default fallback for unmatched items

When the chosen outline does not match some items, those items go into a
single `_misc-<timestamp>.pdf` placed alongside the topic exports. This is
the only way the pipeline is allowed to "split content differently from the
topic exports" without dropping data. The validation report lists
`unclassified_items` so the user can grow the outline iteratively.

## Required contents

Each document should include:

- clear title;
- subject, grade, chapter, topic, and content type when known;
- source name and source-page references;
- normalized text;
- embedded relevant figures;
- concise figure descriptions;
- worked solutions when present;
- warnings or qualifications when a visual relationship is not explicit.

When the document is the `_misc` fallback, its title must make the fallback
role explicit, and the cover should reference the outline used plus the
unclassified count.

## File naming

Recommended pattern:

```text
stage-grade-subject-chapter-topic-content-type-sequence.pdf
```

Topic segments are derived from the outline leaf path, slug-cased and
joined with `-`. When the export is the `_misc` fallback, the topic segment
is `_misc`.

Examples:

```text
sample-chapter-basic-concepts-worked-examples-01.pdf
sample-chapter-main-properties-exercises-01.pdf
sample-chapter-_misc-2026-07-08T1430-01.pdf
```

Chinese filenames are acceptable when the operating environment and
DeepTutor deployment handle them reliably.

## Figure descriptions

Descriptions should prioritize facts that are relevant to interpreting
the item:

- named entities and their relationships as shown;
- explicit labels, marks, or annotations on the figure;
- structural cues (grouping, ordering, connections) the reader needs;
- placement or context needed to interpret the item;
- explicit warnings against unsupported visual assumptions.

Descriptions should not attempt to replace the original figure.

## Export manifest

The export directory should include a machine-readable manifest listing:

- export file;
- checksum;
- included item IDs;
- knowledge tags;
- source pages;
- outline used (id, version, sha256) or `null`;
- reorganization mode (`A | B | C`);
- validation result;
- generation timestamp.

See `schemas/export-plan.schema.json` for the planning contract and
`schemas/project-manifest.schema.json` for how individual exports are
recorded under the project manifest.
