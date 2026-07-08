# Product specification

## Problem

Scanned teaching materials and textbooks often contain figures, formulas, OCR errors, and complex page layouts. Directly uploading these scans to a knowledge base produces unreliable text extraction and weak association between questions, figures, answers, and knowledge points.

MinerU can provide structured output, but its returned image links may be temporary. DeepTutor manages imported documents as independent document records, so a loose Markdown-plus-assets package is not a reliable final format.

## Product

A local preprocessing application that converts scanned PDFs (textbooks, workbooks, or hand-outs) into reviewed, self-contained, topic-oriented PDFs suitable for DeepTutor.

## Primary user workflow

1. Create a project.
2. Select a scanned PDF.
3. Run MinerU extraction.
4. Review localized images and normalized text.
5. Review detected figure-relationship annotations and warnings.
6. Approve or adjust content grouping.
7. Export self-contained PDFs.
8. Upload the exported PDFs to DeepTutor.

## Target content (initial focus)

Initial focus:

- middle-school and high-school textbooks;
- diagram-heavy chapters (figure analysis is an optional stage);
- worked examples with solutions;
- exercises and answer explanations;
- scanned textbooks, workbooks, and printed hand-outs.

The tool is domain-agnostic. Common subject areas include STEM textbooks, language workbooks, exam prep booklets, and printed hand-outs. Picture-heavy material such as early-grades readers benefits from the same asset-localization path.

## MVP features

- Local project workspace.
- Source PDF preservation.
- MinerU API integration.
- Raw response preservation.
- Temporary image URL localization.
- Reference rewriting.
- Basic OCR cleanup.
- Native-text PDF regeneration.
- Embedded image export.
- Manifest generation.
- Validation report.
- Resume after failure.

## Post-MVP features

- Automatic chapter and question segmentation.
- Vision-model figure analysis extraction.
- Human review interface.
- Topic-oriented grouping suggestions.
- Formula correction tools.
- Duplicate image detection.
- DeepTutor batch-upload helper.
- Reprocessing and export version comparison.

## Success criteria

A successful export:

- opens without network access;
- contains no temporary external image dependency;
- preserves important source figures;
- contains searchable text;
- includes enough textual context for retrieval without image understanding;
- can be uploaded to DeepTutor as an independent document;
- retains source-page traceability.




