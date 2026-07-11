# Review and Worker Fixes — 2026-07-10

## Scope

Read-only audit followed by narrow worker fixes. No raw MinerU input, external
services, commits, or pushes were changed.

## Implemented fixes

1. `LocalFirstDownloader` now converts Windows `file:///C:/...` URIs into
   valid local paths. Regression coverage is in `tests/test_downloader.py`.
2. Hash-deduplicated assets retain mappings for every successful source URL,
   so all matching Markdown/layout references can become local paths.
3. Asset localization records per-URL failures. The pipeline writes
   `reports/asset_localization_report.json`, records Stage 2 as `failed`, and
   raises `AssetLocalizationError` instead of allowing later stages to use
   temporary URLs.
4. Outline strategy overrides now determine individual plan modes when the
   effective CLI mode is default B. Explicit Mode A/C remains a global override
   for backward-compatible CLI behavior; Mode C bridges are attached only to
   Mode C recipient plans.
5. `pypdf` is declared under the dev extra and the export test explicitly
   decodes generated JSON as UTF-8.

## Verification

- `uv run --extra dev python -m pytest tests/` — 99 passed.
- Focused Ruff check on changed downloader, registry, runner, and tests —
  passed.
- `git diff --check` — passed.

## Remaining MVP gaps

These require a separately approved design, rather than another narrow bug
fix:

1. Geometry extraction, evidence typing, and a review queue (Stages 5/6) are
   not implemented. The schema exists, but the pipeline does not prevent
   `visual_inference` or `unknown` relations from becoming unreviewed output.
2. There is no independent export validator/report. Stage 7 can render a
   caption for a missing figure and still record completion; it does not update
   `project.json.exports` with validation status.
3. Stages 0–2 are not resumable: an existing workspace raises `FileExistsError`
   and raw MinerU copy currently replaces a same-named preserved directory.
4. The current v1 contract is manual MinerU processing. Source-PDF submission
   and provider polling are not implemented; either document that v1 boundary
   clearly or design a MinerU submission adapter.

## Compatibility decision

Outline strategy is configuration for the normal default Mode B path. An
explicit CLI Mode A or C is an operator request and therefore has priority over
the outline's strategy, preserving existing mode-C bridge workflows.
