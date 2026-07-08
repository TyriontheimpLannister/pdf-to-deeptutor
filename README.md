# pdf-to-deeptutor

> Local-first preprocessing tool that turns scanned PDFs into
> self-contained, outline-driven exports ready for DeepTutor import.

`pdf-to-deeptutor` bridges two existing tools:

1. **MinerU** (or any OCR source that emits Markdown + JSON) — gives you
   structured text plus a set of figure references.
2. **DeepTutor** — wants self-contained PDF documents with native text
   and embedded images, organized by topic rather than by page.

The pipeline runs entirely on your own computer. It does not call any
third-party API and never uploads your source material.

## What it does

Given one scanned PDF (after you have already run it through MinerU
yourself), the tool produces a directory of self-contained PDFs,
grouped by topic:

```
input folder (MinerU output)        output directory
===========================         ================
inbox/<your-book>/                  projects/<your-book>/
  meta.json                            exports/deeptutor/
  full.md                                math-G8-congruent-triangles.pdf
  layout.json                            math-G8-parallel-lines.pdf
  images/                                math-G8-misc-2026-07-08.pdf
        source.pdf                     assets/...
        assets_registry.json
        normalized/full.md
        outline_assignments/
        export_plans/
        ...
```

A typical run produces one PDF per topic in your outline, plus an
`_misc-<timestamp>.pdf` for any content that does not match.

## When to use it

`pdf-to-deeptutor` is for you if you want to:

- Import scanned textbooks or workbooks into DeepTutor without losing
  the embedded figures or having them depend on temporary URLs.
- Reorganize a flat OCR dump into topic-based sub-books (per chapter,
  per knowledge point, or per method) before importing.
- Apply the same outline across many books of the same subject so that
  your knowledge base stays consistently organized.

It is intentionally **not** an OCR tool. You provide the OCR output
(MinerU or compatible). It is also intentionally **not** a knowledge-
extraction tool — the optional Stage 5 figure analyzer produces
evidence-typed relations, not LLM-generated explanations.

## Installation

```bash
pip install pdf-to-deeptutor
```

Or, for development:

```bash
git clone https://github.com/your-org/pdf-to-deeptutor.git
cd pdf-to-deeptutor
pip install -e ".[dev]"
```

Requires Python 3.10 or later.

## Quick start

This example uses the bundled synthetic fixture to demonstrate the full
end-to-end path without needing MinerU or a real scan.

```bash
# 1. Create the inbox layout (the user normally does this once per book)
mkdir -p inbox/my-book/images
cp demos/inbox-sample/g8-triangle-ch03/full.md inbox/my-book/
cp demos/inbox-sample/g8-triangle-ch03/layout.json inbox/my-book/
cp -r demos/inbox-sample/g8-triangle-ch03/images inbox/my-book/

# 2. Generate meta.json (computes source SHA-256, discovers products)
pdf2dt-init-meta --inbox-dir inbox/my-book --original-filename scan.pdf

# 3. Run the pipeline end-to-end
pdf2dt-run \
    --inbox inbox/my-book \
    --project-root projects/my-book \
    --project-id my-book \
    --title "My scanned book" \
    --downloader local \
    --mirror inbox/my-book/images
```

For a real MinerU export, use `--downloader http` instead of `local`:

```bash
pdf2dt-run \
    --inbox inbox/my-book \
    --project-root projects/my-book \
    --project-id my-book \
    --title "My scanned book" \
    --downloader http
```

The runner will download any image still pointing at a remote URL.
Already-local images under `inbox/<book>/images/` are reused without a
network round-trip.

## Concepts

- **Inbox** — the directory where you drop MinerU output. One
  subdirectory per book. See `docs/PIPELINE.md` Stage 1 for the
  contract.
- **Outline** — a YAML taxonomy of topics with keyword and pattern
  vocabulary. Lives under `outlines/`. A blank template is provided at
  `outlines/_templates/blank.yaml`. Outlines are content-hashed and
  recorded in every project manifest so classification results are
  reproducible.
- **Project** — one scanned source document plus every artifact derived
  from it. Lives under `projects/<id>/`.
- **Asset** — a downloaded image with a stable SHA-256-derived ID. No
  final export references a remote URL.
- **Reorganization mode** — three explicit modes (A, B, C) that govern
  how content may be reordered inside an export. Default is B.

See `docs/PRODUCT_SPEC.md` and `docs/PIPELINE.md` for the full design.

## Outline-driven slicing

Outlines are how you tell `pdf-to-deeptutor` how to regroup content.
Here is the smallest possible outline:

```yaml
# outlines/my-outline.yaml
outline_id: my-outline-v1
name: My outline
version: 1.0.0
applies_to:
  subject: general

topics:
  - id: introduction
    label: Introduction
  - id: worked-examples
    label: Worked examples

vocabulary:
  introduction:
    keywords: ["introduction", "overview", "preface"]
  worked-examples:
    keywords: ["example", "worked", "solution"]

strategy:
  default: B
```

```bash
pdf2dt-run \
    --inbox inbox/my-book \
    --project-root projects/my-book \
    --outline outlines/my-outline.yaml \
    --downloader http
```

Items matching no topic go to a single `_misc-<timestamp>.pdf` so no
content is ever dropped.

## Subject-area coverage

The pipeline core (Stages 0-2) is domain-agnostic. Stages 3-7 adapt by
subject area:

- **Figure-heavy STEM material** — exercise path. Stage 5 figure
  analyzer runs and emits evidence-typed relations.
- **Language workbooks, exam-prep booklets, printed hand-outs** — no
  figure analyzer. Stage 5 is skipped; exports are organized by topic
  outline only.
- **Picture-heavy early-grades readers** — same as above, plus an
  optional noise-filter pass during asset localization that drops
  decorative figures below a minimum size threshold.

For subjects where Stage 5 does not apply, just drop the `--outline`
flag and the pipeline falls back to chapter-style grouping.

## Project layout

```
pdf-to-deeptutor/
  AGENTS.md               # startup rules (load first)
  HANDOFF.md              # active handoff state
  README.md               # this file
  CONTRIBUTING.md
  LICENSE                 # MIT
  pyproject.toml
  src/pdf2dt/             # Python package
    inbox/                # Stage 1: MinerU inbox loader
    assets/               # Stage 2: asset localization
    project/              # Stage 0: workspace + manifest
    pipeline/             # orchestrator
  tests/                  # pytest suite
  scripts/                # CLI entry points (also installed as console scripts)
  schemas/                # JSON schemas for outline, export-plan, geometry
  docs/                   # product / architecture / decision docs
  demos/                  # synthetic fixtures
  outlines/               # user-supplied topic taxonomies (templated)
  inbox/                  # dropped MinerU output (gitignored)
  projects/               # generated per-book workspaces (gitignored)
```

## Status

The MVP covers Stages 0, 1, and 2 end-to-end (project creation, MinerU
output ingestion, asset localization). Tests pass on Python 3.10+ on
Windows. Stages 3-7 are designed but not yet implemented; see
`docs/ROADMAP.md` for the full pipeline.

## Contributing

See `CONTRIBUTING.md`. The short version:

- Open an issue before sending a PR for non-trivial changes.
- Keep changes focused; one PR per concern.
- Run `pytest` before pushing.
- New pipeline stages follow the contract in `docs/PIPELINE.md`.

## License

MIT. See `LICENSE`.
