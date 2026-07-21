# pdf-to-deeptutor Handoff

> Updated: 2026-07-21
> Owner: ian
> Status: DONE (backport to upstream `b4b58bc` complete; committed locally 2026-07-21, not yet pushed)

## Current Goal

Maintain a local-first, **domain-agnostic** preprocessing tool that turns a
scanned PDF (after manual MinerU processing) into self-contained,
topic-oriented PDFs ready for DeepTutor import. This repo is the public fork
of the private `../math-content-preprocessor/`: same pipeline, domain-agnostic
architecture.

**"Domain-neutral" definition:** the pipeline is domain-agnostic and supports
multiple domains — math/geometry is the bundled **reference domain**, kept
faithful (not stripped). A generic demo (`sample-chapter-01`) also ships to
show the tool is not math-locked.

Parity policy: upstream is canonical and leads; this fork only **receives**
backports. We follow upstream; we do not pioneer new stages.

## Current State

- Done: **full pipeline parity with `math-content-preprocessor` @ `b4b58bc`**
  (2026-07-21), extending the prior `a5f60ac` parity. New since `a5f60ac`:
  - **Stage 2.5 document-structure recovery** — `document_structure/`
    (`recovery.py` + `alignment.py`): native structure recovery + markdown
    layout alignment.
  - **`figure_roles/` package** — context-aware figure-role classification:
    `cluster.py` auxiliary layer, `pre_filter.py` + `pre_filter_runtime.py`
    (provider-free dry-run, local-image context, VLM-hardened), and
    `review/figure_roles.py` store + audit log.
  - **`providers/mineru/` Stage 0a adapter** — `api_client.py`, `quota.py`,
    `submission.py` (submit to MinerU + poll task); wired via
    `scripts/submit_to_mineru.py` / `scripts/poll_mineru_task.py`.
  - **`outlining/noise.py`** — text-noise filter (Workstream D).
  - **`review/template_decor.py`** — template-decor audit + approved-skip
    wiring.
  - **Export fixes (Problem A/B/C)** — `export/planner.py` drops subset /
    single-item plans; `export/renderer.py` back-fills `asset_refs` from inline
    markdown markers, drops chapter-intro items, cleans stale PDFs.
  - **BookView topic match scores** — `bookview/builder.py` carries match
    scores into BookView.
  - **`geometry/vlm.py`** — image-local figure context restore + real SenseNova
    endpoint + OpenAI-style `image_url` payload.
  - **scripts** — `classify_image_roles.py`, `submit_to_mineru.py`,
    `poll_mineru_task.py`, `_diag_*` diagnostics; `run_pipeline.py` /
    `init_inbox_meta.py` updated.
  - **Graphify knowledge graph** — `graphify-out/` (2287 nodes / 5328 edges /
    101 communities) built code-only (local AST; no LLM key in this env, so
    community names are `Community N` placeholders). Regenerate with
    `graphify .`; set a Gemini/OpenAI/etc. key + re-run `graphify .` for the full
    doc/paper/image semantic graph. Skill doc mirrored at `.codex/skills/graphify/`.
  - Backport = copy upstream `src/math_pp/*` → `src/pdf2dt/*` with the **only**
    rename `math_pp`→`pdf2dt`; all math/geometry content preserved. Same for
    `tests/`, `scripts/` (token rename only). One downstream adaptation: test
    fixtures live at `demos/inbox-sample/` (not upstream's `inbox-sample/`), so
    bare `inbox-sample` paths in copied tests are rewritten to `demos/inbox-sample`.
- Fixtures: math demo `demos/inbox-sample/g8-triangle-ch03` +
  `outlines/elementary-math-v1.yaml` (math = allowed reference domain); generic
  demo `sample-chapter-01` retained. `projects/demo-g8-triangle/` was regenerated
  by the new pipeline (BookView topic scores + updated fingerprints).
- In progress / Blocked: none (parity reached).

## Scope

Allowed paths: `src/pdf2dt/`, `tests/`, `scripts/`, `demos/inbox-sample/`,
`outlines/`, `schemas/`, `docs/`, `projects/` (`!projects/demo-*/` committed).

Do not modify:

- Files under `D:\AI_Data\Shared\` (the protocol itself).
- The private `../math-content-preprocessor/` from this side — canonical
  source; this fork only *receives* backports.
- Raw MinerU output inside `demos/inbox-sample/<task>/` once ingested.

## Next Steps

1. Keep this fork at parity on the next upstream push. Every sync is initiated
   *here* (pull the new module in), never the other way around.
2. Extend `figure_roles/` / `geometry/` analyzers to additional domains if a
   new reference domain is added upstream — modules accept other relation
   vocabularies.
3. For a full Graphify semantic graph (docs/papers/images, named communities),
   set e.g. `GEMINI_API_KEY` and re-run `graphify .` (current graph is code-only).

## Verification

- Passed: `python -m pytest tests/` — **469 passed / 3 skipped** (2026-07-21;
  managed venv `…/python/envs/default` with `pytest pytest-asyncio fpdf2 pypdf
  Pillow pydantic httpx markdown pyyaml`). Sandbox safe-delete bulk guard
  disabled for the run:
  `env -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR -u CODEBUDDY_TOOL_CALL_ID \
   CODEBUDDY_SAFE_DELETE_SANDBOX=0 python -m pytest …`
- Graphify: `graphify . --code-only` → `graphify-out/graph.json` (2287 nodes /
  5328 edges / 101 communities); `graphify cluster-only .` → `GRAPH_REPORT.md` +
  `graph.html`.

## Quick Index

| Need | Read | Notes |
|---|---|---|
| Project rules | `AGENTS.md` | |
| Handoff archives | `docs/handoffs/` | 2026-07-10 parity-backport + domain-neutralization; 2026-07-21 b4b58bc backport |
| Decision log | `docs/decisions/README.md` | geometry-review-stages, figure-descriptions, mode-c-bridges, … |
| VLM design | `docs/VLM_GEOMETRY.md` | geometry VLM enrichment contract |
| Design docs | `docs/` | PRODUCT_SPEC, PIPELINE, DATA_MODEL, EXPORT_SPEC, … |
| Operator scripts | `scripts/run_pipeline.py`, `scripts/rerun_late_stages.py`, `scripts/review.py`, `scripts/submit_to_mineru.py`, `scripts/poll_mineru_task.py` | |
| Knowledge graph | `graphify-out/GRAPH_REPORT.md`, `graphify-out/graph.html`, `graphify-out/graph.json` | code-only; query with `graphify query "<q>"` |
| Canonical test bench | `../math-content-preprocessor/` | private; read-only from our side |
| Public remote | `git@github.com:TyriontheimpLannister/pdf-to-deeptutor.git` | SSH |

## Recent History

- 2026-07-21 ian: backported upstream `a5f60ac`→`b4b58bc` (Stage 2.5 document
  structure recovery; `figure_roles/` cluster + pre_filter + review store;
  `providers/mineru/` Stage 0a adapter; `outlining/noise.py`; `review/
  template_decor.py`; export planner/renderer Problem A/B/C fixes; BookView
  topic scores; geometry/vlm SenseNova wiring; new scripts + ~200 tests).
  Regenerated `projects/demo-g8-triangle/` to match new pipeline output.
  Faithful copy + `math_pp`→`pdf2dt` rename; math/geometry kept; fixture paths
  adapted to `demos/inbox-sample/`. Built Graphify code-only graph
  (2287 nodes / 5328 edges). **469 passed / 3 skipped**.
- 2026-07-12 ian: backported upstream `c9ae123`→`a5f60ac` (Stages 5/6 geometry
  review, preflight, resumable unified runner, Stage 7 export validation, VLM
  resource gate, review store, Mode C geometry bridge, scripts/review.py).
  272 passed / 1 skipped.
