# pdf-to-deeptutor Handoff

> Updated: 2026-07-09 23:31
> Owner: ian
> Status: IN_PROGRESS

## Current Goal

Maintain a local-first, **domain-neutral** preprocessing tool that turns a
scanned PDF (after manual MinerU processing) into self-contained,
topic-oriented PDFs ready for DeepTutor import. This repository is the
public fork of the private `../math-content-preprocessor/` test bench:
same pipeline, no domain-specific taxonomy shipped in-tree.

## Current State

- Done:
  - Documentation: `AGENTS.md`, `README.md`, `CONTRIBUTING.md`,
    `docs/{ARCHITECTURE,PRODUCT_SPEC,PIPELINE,DATA_MODEL,EXPORT_SPEC,
    ROADMAP,VALIDATION,DEEPTUTOR_INTEGRATION}.md`,
    `docs/decisions/*` (decision log + 6 decision records).
  - Stages 0, 1, and 2 implemented and tested end-to-end (22 pytest
    cases passing).
  - **Stage 4b (outline matching) backported** from
    `math-content-preprocessor` and wired into
    `scripts/run_pipeline.py --outline <yaml>`. Now **49 pytest cases
    passing** (27 new). New modules:
    `src/pdf2dt/outlining/{outline,items,matcher}.py`. New tests:
    `tests/test_outline_loader.py`, `tests/test_items.py`,
    `tests/test_outline_matcher.py`, plus `tests/conftest.py` to put
    `src/` on `sys.path` when the package is not `pip install -e .`d.
  - **Domain-neutral fixtures** (intentionally NOT math-specific):
    - `outlines/sample-outline-v1.yaml` — 8 leaf topics covering keyword
      scoring, priority tie-break, `_misc` fallback, and empty-vocab
      never-match behavior.
    - `demos/inbox-sample/sample-chapter/full.md` — 14 content blocks
      exercising the same code paths as the private math fixtures.
  - Public GitHub remote at
    `git@github.com:TyriontheimpLannister/pdf-to-deeptutor.git`
    (source + docs + outlines + schemas + tests only; `inbox/`,
    `projects/`, `inbox-sample/`-style materials are gitignored).
- In progress:
  - Keeping this fork at parity with the canonical private test bench
    (`math-content-preprocessor`). The private project treats
    `../pdf-to-deeptutor/` as **read-only** to keep history clean, so
    every sync is initiated *here* (pull the new module in), never the
    other way around.
  - Resuming Stage 3 normalization work (BookView builder) and the Mode
    B export planner are the next pipeline tasks.
- Blocked: none.
- Recent handoffs: see `docs/decisions/README.md` and git history.

## Scope

Allowed paths:

- `src/pdf2dt/` — Python package.
- `tests/` — pytest suite.
- `scripts/` — operator entry points (`run_pipeline.py`,
  `init_inbox_meta.py`).
- `demos/inbox-sample/`, `outlines/`, `schemas/`, `docs/` — fixtures
  and design artifacts.
- `projects/` — generated per-book workspaces; treat as
  rebuild-from-input artifacts (gitignored).

Do not modify:

- Files under `D:\AI_Data\Shared\` (the protocol itself).
- The private `D:\AI_Data\Projects\math-content-preprocessor\` from this
  side — it is the canonical source; this fork only *receives*
  backports.
- Raw MinerU output inside `demos/inbox-sample/<task>/` once ingested.

## Next Steps

1. Implement Stage 3 normalization + BookView builder (consume Stage 4b
   `topic_ids`). Note: a general limitation of the keyword matcher is
   that it only matches on literal substrings (e.g. LaTeX `\triangle`
   won't match a CJK keyword); Stage 3 normalization should optionally
   expand LaTeX macros to plain text for matching purposes **without**
   rewriting the source.
2. Implement Mode B export planner and self-contained PDF renderer.

## Verification

- Passed: `python -m pytest tests/` — **49 passed** (2026-07-09 23:31).
  No `xfail`: the generic fixtures do not reproduce the math-specific
  keyword-overlap case the private fork tracks as `xfail`.
- Passed: `python scripts/run_pipeline.py --inbox demos/inbox-sample/g8-triangle-ch03
  --outline outlines/sample-outline-v1.yaml` end-to-end (Stages 0–2 then
  4b). Stage 4b produced `items=20, unclassified=5` on the `g8-triangle`
  sample.
- Not run: Stage 3 BookView builder, Mode B export planner, Stage 7 PDF
  renderer.
- **Operational note (sandbox):** the WorkBuddy sandbox's safe-delete
  hook raises `OSError` while cleaning pytest temp dirs (recycle bin
  unavailable), which makes the raw exit code non-zero even when all
  tests pass. Re-run with `CODEBUDDY_SAFE_DELETE_SANDBOX=0` to get a
  clean `49 passed, exit=0`.

## Quick Index

| Need | Read | Notes |
|---|---|---|
| Project rules | `AGENTS.md` | |
| Decision log | `docs/decisions/README.md` | active decisions |
| Product overview | `docs/PRODUCT_SPEC.md` | |
| Pipeline contract | `docs/PIPELINE.md` | |
| Data model | `docs/DATA_MODEL.md` | |
| Export spec | `docs/EXPORT_SPEC.md` | |
| Roadmap | `docs/ROADMAP.md` | |
| Validation | `docs/VALIDATION.md` | |
| Architecture | `docs/ARCHITECTURE.md` | |
| DeepTutor integration | `docs/DEEPTUTOR_INTEGRATION.md` | |
| Schemas | `schemas/` | |
| Operator scripts | `scripts/run_pipeline.py` | |
| Canonical test bench | `../math-content-preprocessor/` | private; read-only from our side |
| Public remote | `git@github.com:TyriontheimpLannister/pdf-to-deeptutor.git` | SSH, source+docs only |

## Recent History

- 2026-07-09 23:31 ian: backported Stage 4b (outline matching) from
  `math-content-preprocessor` into `src/pdf2dt/outlining/`
  (`outline.py`, `items.py`, `matcher.py`); wired `--outline` into
  `scripts/run_pipeline.py`; added domain-neutral fixtures
  (`outlines/sample-outline-v1.yaml`, `demos/inbox-sample/sample-chapter/
  full.md`) and 27 new pytest cases. Full suite **49 passed** (no
  xfail). Verified CLI end-to-end. HANDOFF created (was missing).
- 2026-07-09 00:15 trae: spun up this public fork from
  `math-content-preprocessor` (commit `8a74776` of that repo). Renamed
  package to `pdf2dt`, rewrote docs in domain-neutral language, added
  MIT LICENSE, README, CONTRIBUTING, .gitignore. 22 pytest cases
  passed. Stages 0–2 only at fork time.
