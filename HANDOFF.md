# pdf-to-deeptutor Handoff

> Updated: 2026-07-10 18:25
> Owner: ian
> Status: DONE

## Current Goal

Maintain a local-first, **domain-neutral** preprocessing tool that turns a
scanned PDF (after manual MinerU processing) into self-contained,
topic-oriented PDFs ready for DeepTutor import. This repo is the public fork
of the private `../math-content-preprocessor/`: same pipeline, no
domain-specific taxonomy in-tree.

Parity policy: upstream is canonical and leads; this fork only **receives**
backports. We follow upstream; we do not pioneer new stages.

## Current State

- Done: **full pipeline parity with `math-content-preprocessor` @ `c9ae123`**
  (2026-07-10). Stages 0–2 (ingest/normalize/localize), Stage 3 (BookView +
  MinerU adapter), Stage 4b (outline matching), Stage 4c (export planner),
  Stage 7 (PDF renderer, fpdf2), and Mode C (pluggable `BridgeProvider`) are
  all implemented and tested. Operator scripts, schemas, and domain-neutral
  fixtures are in place. Full detail: `docs/handoffs/2026-07-10-parity-backport.md`.
- In progress: none (parity reached).
- Blocked: none.

## Scope

Allowed paths:

- `src/pdf2dt/` — Python package (incl. `bookview/`, `export/`).
- `tests/` — pytest suite.
- `scripts/` — operator entry points (`run_pipeline.py`,
  `rerun_late_stages.py`, `init_inbox_meta.py`).
- `demos/inbox-sample/`, `outlines/`, `schemas/`, `docs/` — fixtures and
  design artifacts.
- `projects/` — generated per-book workspaces (gitignored, rebuildable).

Do not modify:

- Files under `D:\AI_Data\Shared\` (the protocol itself).
- The private `../math-content-preprocessor/` from this side — canonical
  source; this fork only *receives* backports.
- Raw MinerU output inside `demos/inbox-sample/<task>/` once ingested.

## Next Steps

1. (Optional, v2) Real LLM-backed `BridgeProvider` for Mode C. Protocol +
   `register_bridge_provider(...)` slot already in place; see
   `docs/decisions/2026-07-10-mode-c-bridges.md`. No code change until a
   provider is chosen.
2. Keep this fork at parity on the next upstream push. Every sync is initiated
   *here* (pull the new module in), never the other way around.

## Verification

- Passed: `python -m pytest tests/` — **77 passed / 2 skipped**
  (2026-07-10 18:25, managed venv `…/python/envs/default` with
  `pytest fpdf2 pypdf Pillow`). Skips are environmental (private MinerU export
  not shipped; this `fpdf2` build has no `DejaVuSans.ttf` for the non-CJK
  fallback). Detail in `docs/handoffs/2026-07-10-parity-backport.md`.
- Sandbox note: re-run with `CODEBUDDY_SAFE_DELETE_SANDBOX=0` for a clean
  `exit 0` (safe-delete hook otherwise errors cleaning pytest temp dirs).
- Runtime deps added by this backport: `fpdf2>=2.8`, `Pillow>=10.0`
  (Stage 7); `pypdf>=4.0` in dev extras. Recorded in `pyproject.toml`.

## Quick Index

| Need | Read | Notes |
|---|---|---|
| Project rules | `AGENTS.md` | |
| Latest backport detail | `docs/handoffs/2026-07-10-parity-backport.md` | |
| Decision log | `docs/decisions/README.md` | active decisions |
| Product overview | `docs/PRODUCT_SPEC.md` | |
| Pipeline contract | `docs/PIPELINE.md` | |
| Data model | `docs/DATA_MODEL.md` | |
| Export spec | `docs/EXPORT_SPEC.md` | |
| Roadmap | `docs/ROADMAP.md` | |
| Validation | `docs/VALIDATION.md` | |
| Architecture | `docs/ARCHITECTURE.md` | |
| DeepTutor integration | `docs/DEEPTUTOR_INTEGRATION.md` | |
| Schemas | `schemas/` | incl. `negative_keywords`/`chapter_stopwords` |
| Operator scripts | `scripts/run_pipeline.py`, `scripts/rerun_late_stages.py` | |
| Canonical test bench | `../math-content-preprocessor/` | private; read-only from our side |
| Public remote | `git@github.com:TyriontheimpLannister/pdf-to-deeptutor.git` | SSH, source+docs only |

## Recent History

- 2026-07-10 18:25 ian: trimmed HANDOFF to protocol §4 limit; moved backport
  detail to `docs/handoffs/2026-07-10-parity-backport.md`; set Status=DONE;
  committed + pushed to `origin/main`.
- 2026-07-10 17:40 ian: caught up to upstream `c9ae123` (Stage 3/4c/7 + Mode C
  + outlining sync). Full suite 77 passed / 2 skipped. Detail archived.
- 2026-07-09 23:31 ian: backported Stage 4b outline matching; added neutral
  fixtures + 27 tests (49 passed). HANDOFF created.
- 2026-07-09 00:15 trae: spun up this public fork from
  `math-content-preprocessor` `8a74776`. Renamed package to `pdf2dt`,
  domain-neutral docs, MIT LICENSE. Stages 0–2 only at fork time.
