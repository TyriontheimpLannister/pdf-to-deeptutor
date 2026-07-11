# P0 — Stage 5 review-state loss fix (2026-07-11)

> Detailed handoff record, 2026-07-11. The active entry point is the project
> root `HANDOFF.md`.

## Context

The VLM geometry audit (`docs/handoffs/2026-07-11-vlm-audit-blockers.md`)
flagged a P0 data-loss bug: `scripts/run_pipeline.py --geometry` called
`analyze_geometry()` on every invocation, which overwrites
`review/geometry_figures.json` and discards every confirmed/corrected/rejected
`review_state` while `review/review_state.json` stays stale. The CLI help
string even claimed Stage 5 was "skipped automatically", but the code path had
no guard.

## Decision

Adopted the **explicit reset** force behaviour (audit option B) rather than
merging review state by `(figure_id, relation_key)`:

- Default `--geometry` rerun: skip Stage 5 when already completed; record
  `SKIPPED`; leave `review/` untouched.
- `--force-geometry`: re-extract, overwrite the queue, and clear
  `review/review_state.json` to an empty `decisions` array so the audit log
  does not contradict a freshly unreviewed queue. Record `review_reset: true`
  and `review_reset_at` in the `stage5_geometry` manifest metadata so the
  decision is traceable.

Rationale: merging review state across a re-extracted queue requires handling
deleted/modified relations and vanished figures — a large, error-prone surface
for marginal benefit. An explicit reset is transparent, small, and matches the
existing `rerun_late_stages.py` contract.

## Changes

- `src/pdf2dt/geometry/analyzer.py`: `analyze_geometry()` gains
  `force: bool = False`. When True, `review_state.json` is wiped before the
  new queue is written and the manifest records the reset.
- `scripts/run_pipeline.py`: adds `--force-geometry`; implements the skip guard
  (mirrors `rerun_late_stages.py`); fixes the help string that previously
  claimed auto-skip behaviour that did not exist in code.
- `scripts/rerun_late_stages.py`: forwards `force=args.force_geometry` to
  `analyze_geometry()` so both CLIs behave identically.
- `tests/test_geometry.py`: three new tests under an `e2e_workspace` fixture
  that runs the full pipeline (Stages 0-7):
  - `test_stage5_rerun_preserves_review_state_by_default` — applies a confirm
    decision, asserts it survives a default rerun.
  - `test_stage5_force_clears_review_state_and_records_reset` — asserts
    `force=True` wipes the audit log and records the reset in the manifest.
  - `test_run_pipeline_cli_exposes_force_geometry_flag` — CLI smoke.

## Verification

- `uv run --extra dev python -m pytest tests/ -q` — 233 passed (was 230).
- `uv run --extra dev ruff check` on all changed files — clean.
- Targeted E2E tests cover both the default-preserves and force-clears paths.

## Files touched

```
src/pdf2dt/geometry/analyzer.py
scripts/run_pipeline.py
scripts/rerun_late_stages.py
tests/test_geometry.py
HANDOFF.md
docs/handoffs/2026-07-11-p0-stage5-review-state-fix.md  (this file)
```

## Remaining audit items

P1 items from `docs/handoffs/2026-07-11-vlm-audit-blockers.md` remain open:
safe VLM fallback, resource gates, VLM selection/evidence, and
traceability/public API. None block the rules-only or no-key hybrid export
path.
