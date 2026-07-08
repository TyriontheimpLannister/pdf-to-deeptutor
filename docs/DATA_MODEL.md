# Data model

## Project

Represents one source document and all derived artifacts.

Required fields:

- project ID;
- title;
- source file path;
- source SHA-256;
- creation and update timestamps;
- subject metadata;
- stage records;
- provider task metadata;
- outline used (if any);
- export versions.

## Outline

A user-supplied taxonomy used to drive Stage 4. Independent of any specific
source book.

Required fields:

- `outline_id` — stable identifier;
- `name` — human-readable name;
- `version` — semver-style string;
- `applies_to` — `subject` and optional `stage` filter;
- `topics` — list of topic nodes (see `Topic`);
- `vocabulary` — keyword and pattern map per topic id;
- `strategy` — optional reorganization overrides;
- content hash — derived from the YAML body; recorded in the manifest.

See `schemas/outline.schema.json` for the canonical contract.

## Topic

One node in an outline tree.

Required fields:

- `id` — stable identifier used in cross-references and export filenames;
- `label` — display label, also used as the PDF title segment.

Optional:

- `children` — list of subtopics;
- `description` — short note for the user;
- `priority` — tiebreaker when items match multiple topics.

## TopicAssignment

The result of Stage 4b. Records which outline topics an item was assigned
to.

Required fields:

- `item_id` — the assigned content item;
- `topic_ids` — list of matching topic ids (empty means unclassified);
- `match_details` — per-topic list of matched keywords and patterns;
- `review_state` — `unreviewed | confirmed | corrected | rejected`.

Assignments are persisted per project so reruns are reproducible.

## BookView

The full-book internal representation produced by Stage 4a. Holds the
complete extracted structure before any slicing.

Typical fields:

- `book_id` — derived from source SHA-256;
- `chapters` — list of `Chapter` in source order;
- `items` — every content item (definition / theorem / worked example /
  exercise / solution / method / chapter summary) with stable IDs;
- `figures` — every figure with its associated items;
- `assets` — every asset ID referenced from any item;
- `outline_assignment` — map from item id to `TopicAssignment` (when an
  outline was applied).

The `BookView` is the input to Stage 4c. Slicing never reads raw MinerU
output again.

## Chapter

A chapter or section derived from the Markdown heading hierarchy.

Required fields:

- `chapter_id` — stable;
- `title`;
- `level` — heading depth (1 = top-level chapter, 2 = section, ...);
- `source_block_ids` — pointers back to normalized blocks;
- `child_chapter_ids` — for nested headings;
- `contained_item_ids` — items that physically live in this chapter.

## Source block

A normalized unit derived from MinerU output.

Typical fields:

- block ID;
- page number;
- block type;
- text or formula content;
- bounding box when available;
- source-provider pointer;
- associated asset IDs.

## Asset

A permanently stored image or other binary resource.

Required fields:

- asset ID;
- original URL or source pointer;
- local path;
- SHA-256;
- MIME type;
- dimensions;
- source page;
- download and validation status.

## Content item

A semantic unit such as a definition, theorem, worked example, exercise,
solution, method, or chapter summary.

Typical fields:

- item ID;
- item type;
- title;
- knowledge tags;
- source block IDs;
- asset IDs;
- source pages;
- review state;
- `topic_ids` — assigned topics (filled by Stage 4b).

## structured figure

Represents one mathematically relevant figure and its structured
interpretation. Reserved for Stage 5.

Typical fields:

- figure ID;
- asset ID;
- associated item ID;
- points;
- segments;
- relations;
- explicit marks;
- uncertain visual observations;
- model metadata;
- review state.

## figure analysis relation

Required fields:

- relation type;
- participating entities;
- evidence type;
- source reference;
- confidence;
- review state.

Examples:

- point on segment;
- equal lengths;
- equal angles;
- parallel lines;
- perpendicular lines;
- midpoint;
- angle bisector;
- collinearity.

## ExportPlan

The output of Stage 4c. Drives Stage 7.

Required fields:

- `plan_id` — stable;
- `topic_id` — outline topic, or `_misc` for the fallback plan;
- `title` — used as the export PDF title segment;
- `mode` — `A | B | C`;
- `item_ids` — included content items in planned order;
- `figure_ids` — included figures;
- `output_filename` — derived per the naming convention in `EXPORT_SPEC.md`;
- `outline_used` — `{outline_id, version, sha256}` or `null`.

See `schemas/export-plan.schema.json`.

## Export document

Represents one generated DeepTutor import file.

Typical fields:

- export ID;
- title;
- filename;
- included item IDs;
- generated path;
- SHA-256;
- page count;
- validation result;
- generator version.
