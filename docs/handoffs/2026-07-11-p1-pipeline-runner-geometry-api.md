# P1 #4 — Public PipelineRunner geometry-provider API

> Updated: 2026-07-11
> Owner: TRAE
> Scope: audit item P1 #4 from
> `docs/handoffs/2026-07-11-vlm-audit-blockers.md` —
> "public pipeline API cannot select VLM".

## Background

After P0/P1 #1/P1 #2/P1 #3 the CLI's `--geometry-provider` flag was
the only way to choose anything other than the default rules-only
analyzer. Programmatic callers (`PipelineRunner`, `run_pipeline`)
silently fell back to `GeometryAnalyzer()` and never reached the hybrid
VLM path. Notebook users, future UI hooks, and any test that wants to
inject a fake provider all had to either re-implement the runner or
build a CLI subprocess.

## What changed

- `PipelineRunner.run()` now accepts two new keyword arguments:
  - `geometry_analyzer: GeometryAnalyzer | None = None` — when set, the
    runner uses this analyzer (and its provider, if any) instead of the
    default rules-only `GeometryAnalyzer`. Any subclass or wrapper of
    `GeometryAnalyzer` is accepted; the runner does not pin the type to
    `HybridGeometryAnalyzer`.
  - `force_geometry: bool = False` — mirrors the CLI's
    `--force-geometry` flag. When True, Stage 5 is re-run even if
    `stage5_geometry` is already marked COMPLETED. The existing P0
    review-state reset (`review_reset=true` in the manifest metadata)
    still applies. Default behavior (skip on resume) is preserved.
- The public module-level `run_pipeline(...)` convenience function in
  `src/pdf2dt/pipeline/runner.py` surfaces both new keyword arguments
  and forwards them to `PipelineRunner.run(...)`. Non-CLI callers no
  longer need to instantiate the runner explicitly to pick an analyzer.
- `PipelineRunner._run_geometry` was reworked so the new knobs flow
  through to `analyze_geometry(workspace, analyzer=..., force=...)`.
  The hybrid runner remains skip-on-resume by default; `force_geometry`
  is the explicit re-extract path.
- The CLI (`scripts/run_pipeline.py`) was already calling
  `analyze_geometry` directly for its `--geometry` block and was not
  changed; the CLI's `--geometry-provider` string continues to flow
  through `build_geometry_analyzer`. Programmatic callers now have the
  same selection contract through the public Python API.

## Files touched

- `src/pdf2dt/pipeline/runner.py` — `PipelineRunner.run(...)` and the
  module-level `run_pipeline(...)` gain
  `geometry_analyzer` + `force_geometry` kwargs;
  `_run_geometry` forwards them to `analyze_geometry`.
- `tests/test_pipeline_runner.py` — five new regression tests covering
  injection through the runner, the rules-only default, custom
  subclasses, the module-level convenience, and the
  `force_geometry=True` contract.

## Verification

- Passed: `uv run python -m pytest --no-header -q` — **260 passed**
  (was 255 before this commit; 5 new tests added).
- Passed: `uv run ruff check src tests` — clean.
- Pre-existing: `ruff check scripts/init_inbox_meta.py:162` still flags
  `W292`. Confirmed pre-existing in the audit; not modified.

### New tests

- `test_pipeline_runner_accepts_injected_geometry_analyzer` —
  `HybridGeometryAnalyzer(provider=_FakeVlmProvider(...))` injected
  through `PipelineRunner.run(...)`. The runner persists
  `visual_inference` relations into `review/geometry_figures.json`,
  proving the hybrid provider reached the actual figures in the
  synthetic fixture.
- `test_pipeline_runner_without_injected_analyzer_uses_rules_only` —
  default path produces a Stage 5 record with no `vlm_summary` and no
  `vlm_report_path`, so callers cannot accidentally trigger a paid VLM
  call by calling `run_pipeline` with no arguments.
- `test_pipeline_runner_geometry_analyzer_with_custom_rules` — a
  subclass of `GeometryAnalyzer` is also accepted; proves the runner
  does not pin callers to the hybrid flavor.
- `test_run_pipeline_convenience_forwards_geometry_analyzer` — the
  module-level `run_pipeline(...)` helper surfaces the same knob as
  the runner so callers do not need to instantiate
  `PipelineRunner` directly.
- `test_pipeline_runner_force_geometry_re_extends_with_injected_analyzer`
  — `force_geometry=True` drives Stage 5 through the injected analyzer
  on a fresh workspace and records `review_reset=true` in the manifest
  metadata, mirroring the CLI's `--force-geometry` contract.

The injected provider is `_FakeVlmProvider` defined inside the test
file: an in-memory stand-in with a counter on `calls`; no network
access. The audit called for "a test that injects a fake VLM provider
without network access" — that contract is met by the four tests that
actually exercise the provider.

## Open items still on the audit list

- P2 (VLM_GEOMETRY docs refresh — describe the new
  `geometry_analyzer` / `force_geometry` knobs so notebook and UI
  authors learn about them).

## Safety boundary

The injected analyzer is the only path that can supply a remote VLM
provider. Tests inject `_FakeVlmProvider` instances that hold zero
network capability; the runner never imports
`MiniMaxM3Provider` or `SenseNovaProvider` itself, so the default
public API remains rules-only.
