# P1 — VLM safe fallback and selection strategy

> Updated: 2026-07-11
> Owner: TRAE
> Scope: P1 #1 (malformed output can abort Stage 5) and P1 #3 (VLM runs for every
> figure; audit evidence incomplete) from the VLM audit.

## What changed

### P1 #1 — safe fallback (`src/pdf2dt/geometry/vlm.py`)

- `_parse_response` now validates every model field with `_coerce_confidence` and
  per-record type checks. Bad confidence (`"high"`, `None`, non-numeric) is
  silently dropped with `discarded N invalid relation(s)` in the error string.
- `_extract_message_text` and `_extract_sensenova_text` walk the provider
  response shape safely; missing containers return `""` rather than raising
  `AttributeError` or `TypeError`.
- `MiniMaxM3Provider.analyze_image` and `SenseNovaProvider.analyze_image`
  catch `json.JSONDecodeError` separately, never let HTTP-level exceptions
  propagate, and return `VlmResponse(error=...)` for every malformed body.
- A malformed VLM response no longer aborts Stage 5: the rules figure is
  always persisted; the failure is recorded in the call log.

### P1 #3 — selection strategy and audit evidence

- New `should_call_vlm(figure)` decides whether the paid call is worth making:
  - Call when rules produced no relations (blank / only-visual).
  - Call when every relation is non-promotable.
  - Call when a promotable relation has confidence below the trigger (0.5).
  - Call when the figure has unresolved visual observations.
  - Otherwise skip with `skip_reason="rules_sufficient"`.
- `HybridGeometryAnalyzer.analyze` writes a `VlmCallRecord` for every figure,
  whether the call was made or skipped. Each record carries
  `asset_id`, `asset_sha256`, `provider`, `model`, `endpoint`, `status`,
  `skip_reason`, `error`, `request_bytes`, `context_chars`, `elapsed_ms`,
  `response_sha256`, and a relative `raw_response_path` for paid calls.
- Raw responses are persisted under `reports/vlm-raw/<figure_id>.json` for paid
  calls only. No API keys, prompt bodies, or environment values are stored.
- `analyze_geometry` writes the new `geometry_vlm_report.json` with
  `selection_strategy: "should_call_vlm"`, the per-call list, and a
  `vlm_summary` block (`total` / `called` / `skipped` / `failed` /
  `skip_reasons`) into the `stage5_geometry` manifest metadata.
- Conflicting VLM relations (same entity set, different relation type) emit a
  `conflict on … — review needed` observation so the queue surfaces them.
- `visual_observations` semantics are now restricted to *unresolved* patterns
  (those that did not yield an entity-resolved relation), which aligns with
  the docstring and gives `should_call_vlm` a real signal.

## Files touched

- `src/pdf2dt/geometry/vlm.py` — providers, parsing, hybrid analyzer,
  `should_call_vlm`, `VlmCallRecord`.
- `src/pdf2dt/geometry/analyzer.py` — `analyze_geometry` wires the raw
  responses directory, the per-call report, the vlm summary, and tightens the
  `visual_observations` semantics.
- `tests/test_geometry.py` — 11 new tests covering the safe-fallback and
  selection paths (see Verification).

## Verification

- Passed: `uv run python -m pytest --no-header -q` — **244 passed** (was 233
  before this commit; 11 new tests added).
- Passed: `uv run ruff check src tests` — clean.
- Pre-existing: `ruff check scripts/init_inbox_meta.py:162` still flags
  `W292`. Confirmed pre-existing in the audit; not modified.

### New tests

Safe-fallback coverage:

- `test_parse_response_handles_nonnumeric_confidence` — `confidence: "high"`
  is dropped, the rest survives.
- `test_parse_response_handles_list_body` — JSON list maps to
  `response JSON must be an object`.
- `test_parse_response_handles_partial_valid_payload` — one good record, one
  bad enum, one non-dict, one missing entities — only the good one survives.
- `test_parse_response_handles_empty_text` — `""` returns `empty response`.
- `test_minimax_provider_returns_error_on_malformed_choices` — Anthropic
  `content` as a string returns `MiniMax returned no text block`.
- `test_sensenova_provider_returns_error_on_missing_data_field` — missing
  `data.choices` returns `SenseNova returned no text`.

Selection + evidence coverage:

- `test_should_call_vlm_decisions` — blank / only-visual / confident /
  non-promotable decisions.
- `test_hybrid_analyzer_skips_paid_call_when_rules_sufficient` — counting
  provider registers zero calls and the record is `skipped`.
- `test_hybrid_analyzer_records_per_call_metadata` — `VlmCallRecord`
  fields, raw-response persistence, no API keys in payload.
- `test_hybrid_analyzer_records_conflict_observation` — VLM relation is
  added with `VISUAL_INFERENCE` evidence when rules yielded nothing.
- `test_hybrid_analyzer_skips_vlm_when_rules_sufficient` — conflict cannot
  fire once rules are confident; VLM is skipped, queue is unchanged.

## Open P1 items still on the audit list

- P1 #2 (resource gates — MIME / byte cap / decoded-pixel cap).
- P1 #4 (public `PipelineRunner` geometry-provider API and tests with a
  fake provider injected).
- P2 (VLM_GEOMETRY docs refresh).

## Safety boundary

No API key is requested, printed, persisted, or committed. A no-key hybrid
run still produces a successful rules-only export with `failed: 1` recorded
in `vlm_summary`.
