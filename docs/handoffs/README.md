# handoffs/

Historical HANDOFF snapshots and detailed handoff records for this
project. The active handoff is the root `HANDOFF.md`. Read that first;
only consult this directory when you need to trace why something is the
way it is.

## Files

| File | When | Why kept |
|---|---|---|
| `2026-07-08-pre-protocol-handoff.md` | Before the multi-agent project protocol was applied to this project | Contains the original design rationale (Modes A/B/C, outline-driven slicing, manual MinerU). Superseded by the root `HANDOFF.md` plus `docs/decisions/`. Read only when designing a new major change. |
| `2026-07-10-v1.0.1-pipeline-and-handoff-rotation.md` | When `HANDOFF.md` grew to 283 lines and was rotated under the 100-line protocol cap | Holds the Recent History that lived in the root `HANDOFF.md` until 2026-07-10 06:55 (Stage 3 / 4b / 4c / 7 implementations, warning cleanup, outline v1.0.1). Read only when tracing how the current state was reached. |
| `2026-07-11-vlm-reaudit-remaining-blockers.md` | Re-audit after Trae addressed the VLM audit findings | Records the two remaining P1 defects, hygiene acceptance checks, and exact repair tests. |
| `2026-07-11-vlm-audit-blockers.md` | Independent review after adding hybrid MiniMax/SenseNova geometry enrichment | Records the active P0/P1 fixes required before hybrid VLM use on real material. |
| `2026-07-11-p0-stage5-review-state-fix.md` | TRAE fix for the P0 audit item — Stage 5 review-state loss | Documents the `--force-geometry` contract, reset behaviour, and E2E tests. Read when resuming the P1 audit items. |
| `2026-07-11-p1-vlm-safety-and-selection.md` | TRAE fix for audit items P1 #1 (safe fallback) and P1 #3 (VLM selection + audit evidence) | Records `should_call_vlm`, `VlmCallRecord`, and the `reports/vlm-raw/` persistence path. Read when tracing hybrid geometry audit behaviour. |
| `2026-07-11-p1-vlm-resource-gate.md` | TRAE fix for audit item P1 #2 (resource gate) | Records `check_vlm_asset`, provider short-circuits, and the `rejected` call status. Read when tuning MIME / byte / pixel caps. |
| `2026-07-11-p1-pipeline-runner-geometry-api.md` | TRAE fix for audit item P1 #4 (public pipeline geometry-provider API) | Records the `geometry_analyzer` + `force_geometry` kwargs on `PipelineRunner.run` and the public `run_pipeline()`. Read when wiring a custom analyzer into programmatic callers. |
| `2026-07-11-p2-vlm-geometry-docs-refresh.md` | TRAE fix for audit item P2 (VLM_GEOMETRY docs refresh) | Records the rewrite of `docs/VLM_GEOMETRY.md` so it matches the post-audit contract (selection strategy, audit log, resource gate, public API). Read when orienting a new contributor on the VLM stage. |
| `2026-07-11-p1-conflict-identity-and-image-integrity.md` | TRAE follow-up to `2026-07-11-vlm-reaudit-remaining-blockers.md` | Records the single-pass candidate handling in `vlm.py`, the Pillow `verify()`+`load()` gate in `resource_gate.py`, the no-key hybrid fallback E2E, the strict truncated-asset tests, and the CRLF normalization. Read when resuming the post-conflict-review-queue audit. |

## Conventions

- File name: `YYYY-MM-DD-<topic>.md`. One file per handoff event.
- Each file is a self-contained snapshot of the previous HANDOFF.md;
  no incremental diffs.
- Add a header noting it is an archive so the next reader does not
  mistake it for the live handoff.
- Do not edit archived snapshots; create a new file if the situation
  changes.
