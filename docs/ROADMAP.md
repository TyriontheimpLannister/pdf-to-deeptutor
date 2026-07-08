# Roadmap

## Phase 1: end-to-end CLI

- Project creation.
- Source ingestion.
- MinerU adapter.
- Raw result preservation.
- Asset localization.
- Reference rewriting.
- Minimal normalization.
- One self-contained PDF export.
- Validation report.

## Phase 2: stable internal model

- Page and block model.
- Content item model.
- Resumable stages.
- Deterministic reprocessing.
- Export manifests.
- Test fixtures and regression tests.
- **MinerU layout adapter** — convert the real MinerU VLM output
  (`pdf_info / preproc_blocks / ...`) into the simplified
  `{pages: [{blocks: [...]}]}` schema that Stage 1 currently expects.
  Today, the loader ignores real MinerU layout.json and falls back to
  Markdown-only normalization. After the adapter lands, Stage 3 can
  use bbox-accurate block positions and image placements for tighter
  PDF reconstruction.

## Phase 3: figure analysis intelligence

- Figure-to-text association.
- Vision-model adapter.
- Evidence-tagged relation extraction.
- Visual-inference safeguards.
- Reviewable figure analysis JSON.

## Phase 4: topic segmentation

- Chapter and section detection.
- Example and exercise grouping.
- Question-answer binding.
- Topic-oriented export planning.

## Phase 5: review interface

- Local project dashboard.
- Text and image preview.
- Warning resolution.
- Relation editing.
- Export grouping controls.

## Phase 6: DeepTutor delivery

- Export sync.
- Optional batch-import helper.
- Import result recording.
- Reindex guidance and verification tests.

