# P2 — VLM_GEOMETRY.md docs refresh

> Updated: 2026-07-11
> Owner: TRAE
> Scope: audit item P2 from
> `docs/handoffs/2026-07-11-vlm-audit-blockers.md` — refresh
> `docs/VLM_GEOMETRY.md` to reflect the new selection strategy,
> audit log, and resource gate contract.

## Background

The old `VLM_GEOMETRY.md` predated the P0 + full P1 audit fixes. It
described Stage 5 as a hybrid stage that "calls the VLM for every
figure" and did not document:

- `should_call_vlm` selection strategy.
- `VlmCallRecord` fields (`asset_sha256`, response SHA, timing,
  sanitized request metadata, raw response path).
- `check_vlm_asset` resource gate (MIME whitelist, byte cap,
  pixel cap).
- The new `status` state machine (`skipped` / `ok` / `failed` /
  `rejected`).
- The public `PipelineRunner.run(geometry_analyzer=..., force_geometry=...)`
  knobs.

After the previous rounds the file was both inaccurate and
dangerously silent on the no-network default — readers could not
tell which call paths actually hit MiniMax or SenseNova.

## What changed

`docs/VLM_GEOMETRY.md` was rewritten from scratch.  The new doc is
organized around five reader questions rather than by file:

1. **What is this and what is it not.** Calls out that VLM is an
   *opt-in* enrichment over the deterministic rules analyzer and
   that a bad VLM call cannot erase or replace the rules result.
2. **Quick decision matrix.** A four-row table that tells the reader
   whether each caller surface needs the network and which knob to
   turn. Explicit reminder that the default public Python API never
   reaches a remote provider.
3. **Two providers, one contract.** MiniMax-M3 vs SenseNova: endpoint
   shape, env vars (`MINIMAX_API_KEY` / `SENSENOVA_API_KEY`),
   authorization headers, per-image / per-request caps, and the
   no-key error string.
4. **Selection strategy.** The `should_call_vlm` decision tree with
   every reason (`rules_blank`, `rules_only_non_promotable`,
   `rules_low_confidence`, `rules_have_visual_observations`,
   `rules_sufficient`).  Plus the conflict observation that surfaces
   when rules and VLM disagree on the same entity pair.
5. **Resource gate.** `check_vlm_asset`'s MIME whitelist, 10 MB
   byte cap, 25 MP pixel cap, and the `asset_rejected` error string
   shape; public surface `VlmGateResult` and where to import from
   (`pdf2dt.geometry`).
6. **Safe-fallback contract.** What happens on malformed choices,
   network errors, JSON parse errors, missing / non-numeric
   confidence, and bad relation dictionaries.  Reassures the reader
   that Stage 5 is never aborted by a VLM problem.
7. **Audit log.** Tables for `VlmCallRecord` fields, the four
   `status` values, and the `metadata.vlm_summary` block. Explicit
   notice that the API key never appears on disk.
8. **CLI surface.** Worked examples for `--geometry-provider`,
   including the `ValueError` path for typos and the
   `--force-geometry` re-extract flag.
9. **Public Python API.** Worked example for
   `PipelineRunner.run(...)` and `run_pipeline(...)` showing the
   `geometry_analyzer` + `force_geometry` kwargs in action.
10. **Quick test recipes.** Three one-liners the reader can copy to
    re-verify the contract after editing any of the four moving
    components.
11. **References.**  Direct links back to each previous handoff doc
    and to the source files in `src/pdf2dt/geometry/` plus
    `src/pdf2dt/pipeline/runner.py`.

## Files touched

- `docs/VLM_GEOMETRY.md` — full rewrite to match the post-audit
  contract; no code or tests changed for P2 (the audit explicitly
  scoped this work to documentation).

## Verification

This is a docs-only commit.  Validation was:

- Read-through for consistency with the four handoff docs the
  previous rounds produced.
- All code samples in the new doc were verified to import
  successfully against the current public surface in
  `src/pdf2dt/geometry/__init__.py`:
  `Evidence`, `GeometryAnalyzer`, `HybridGeometryAnalyzer`,
  `MiniMaxM3Provider`, `SenseNovaProvider`, `VlmGateResult`,
  `check_vlm_asset`, `build_geometry_analyzer`.
- File path references point to existing files:
  - `src/pdf2dt/geometry/vlm.py`
  - `src/pdf2dt/geometry/resource_gate.py`
  - `src/pdf2dt/geometry/analyzer.py`
  - `src/pdf2dt/pipeline/runner.py`
  - `tests/test_geometry.py`
  - `tests/test_vlm_resource_gate.py`
  - `tests/test_pipeline_runner.py`
- Test recipes resolve to existing pytest targets in the current
  test suite (verified by grepping the test files).

No Python or shell code was changed; tests + ruff not re-run.

## Audit status

After this commit the VLM audit is **fully closed**:

- P0 — review-state default-skip and `--force-geometry` reset
  (closed earlier).
- P1 #1 — VLM safe-fallback contract (closed earlier).
- P1 #2 — resource gate (closed earlier).
- P1 #3 — selection strategy + audit evidence (closed earlier).
- P1 #4 — public `PipelineRunner.geometry_analyzer` /
  `force_geometry` API (closed earlier).
- P2 — `docs/VLM_GEOMETRY.md` refresh (this commit).
