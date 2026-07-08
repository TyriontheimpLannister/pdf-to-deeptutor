# Asset localization — remote URLs are forbidden after Stage 2

## Decision

Every image reference in the loaded MinerU output must be downloaded,
validated, content-hashed, and rewritten to a stable local asset path
before any further pipeline stage runs. No final export may reference a
remote URL.

## Why

- MinerU temporary URLs expire. A final export that still points at
  them becomes a dead link the moment MinerU reaps the file.
- Self-contained DeepTutor imports require embedded figures.
- A SHA-256-keyed asset ID is stable across re-runs and lets Stage 7
  rebuild any export deterministically.

## How

- `AssetLocalizer` accepts a pluggable downloader; `LocalFirstDownloader`
  resolves local files first and falls back to HTTP for real MinerU
  exports.
- Asset ID = first 12 hex chars of SHA-256.
- Files are persisted under `projects/<id>/assets/<asset_id>.<ext>`.
- Rewriting produces `assets/<asset_id>.<ext>` references in Markdown
  and `assets/<asset_id>.<ext>` plus an `asset_id` field in
  layout.json blocks.

## Trade-offs

- Local-first behavior means we never re-download content the user has
  already dropped into `images/`. If a user wants a fresh fetch, they
  delete the local copy first.
- We do not yet deduplicate across projects; this is acceptable for
  the MVP and may be revisited if a user runs many books.
