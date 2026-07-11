# P1 #2 — VLM resource gate

> Updated: 2026-07-11
> Owner: TRAE
> Scope: audit item P1 #2 from
> `docs/handoffs/2026-07-11-vlm-audit-blockers.md` —
> "no local image / request resource gate".

## Background

Both VLM providers base64-encode every submitted asset before sending it
to the remote service. The audit found that nothing rejected an
oversized file, an unsupported MIME type, or a pathologically large
pixel grid. MiniMax-M3 documents a 10 MB image cap and a 64 MB request
cap; one unfiltered asset could blow past either.

## What changed

- New module `src/pdf2dt/geometry/resource_gate.py` exposes
  `check_vlm_asset(image_path, *, max_image_bytes, max_pixels) ->
  VlmGateResult`. The check is deterministic and never raises: missing
  files, unsupported suffixes, file sizes over `max_image_bytes`, and
  decoded pixel counts over `max_pixels` each yield
  `VlmGateResult(ok=False, error=...)`.
- `MiniMaxM3Provider.analyze_image` and `SenseNovaProvider.analyze_image`
  call `check_vlm_asset` before Base64 encoding. A failure returns
  `VlmResponse(error="asset_rejected: <reason>")` so the rules-first
  pipeline still finishes and the failure is recorded in the VLM call
  audit log.
- Constructor overrides (`max_image_bytes=`) are exposed only on the
  providers themselves; the rest of the pipeline uses the documented
  defaults (10 MB / 25 MP).
- The `VlmCallRecord.status` field now distinguishes `failed` (network /
  API problem) from `rejected` (asset gate). The `vlm_summary` block in
  the stage-5 manifest adds a `rejected` counter alongside
  `failed` / `skipped`.

## Files touched

- `src/pdf2dt/geometry/resource_gate.py` — new module.
- `src/pdf2dt/geometry/vlm.py` — both providers call the gate;
  `HybridGeometryAnalyzer` maps asset-gate failures to a `rejected`
  status with an `asset_rejected` observation.
- `src/pdf2dt/geometry/analyzer.py` — `vlm_summary` report now exposes
  `rejected` and `skip_reasons` is computed against the new status set.
- `src/pdf2dt/geometry/__init__.py` — exports `VlmGateResult` and
  `check_vlm_asset`.
- `tests/test_vlm_resource_gate.py` — new file: 11 regression tests.
- `tests/test_geometry.py` — three real-provider tests now write a real
  PNG via a small `_fake_png` helper. FakeProvider tests are unchanged;
  the gate is not invoked because the fake provider never reaches the
  network.

## Verification

- Passed: `uv run python -m pytest --no-header -q` — **255 passed** (was
  244 before this commit; 11 new tests added).
- Passed: `uv run ruff check src tests` — clean.
- Pre-existing: `ruff check scripts/init_inbox_meta.py:162` still flags
  `W292`. Confirmed pre-existing in the audit; not modified.

### Gate coverage

| Case | Behaviour |
|---|---|
| Missing file | `image file missing: ...` |
| Unsupported suffix | `unsupported image extension: ...` |
| File > cap | `image too large: N bytes (max ...)` |
| W × H > cap | `image too large: WxH = N pixels (max ...)` |
| Undecodable body | `cannot decode image: ...` |
| Within caps | `VlmGateResult(ok=True, media_type="image/png", pixel_count=...)` |
| Real provider reaching gate | `VlmResponse(error="asset_rejected: ...")` |
| Hybrid analyzer | `call_records[0].status == "rejected"` + figure still produced |

### New tests

- `test_check_vlm_asset_rejects_missing_file`
- `test_check_vlm_asset_rejects_unsupported_suffix`
- `test_check_vlm_asset_rejects_oversize_file`
- `test_check_vlm_asset_rejects_oversize_pixels`
- `test_check_vlm_asset_accepts_within_caps`
- `test_check_vlm_asset_rejects_undecodable_payload`
- `test_minimax_provider_short_circuits_on_oversize_asset`
- `test_minimax_provider_short_circuits_on_bad_suffix`
- `test_sensenova_provider_short_circuits_on_oversize_asset`
- `test_hybrid_analyzer_marks_asset_gate_rejection`
- `test_hybrid_analyzer_uses_pillow_to_detect_pixel_count`

The MockTransport handlers inside the provider short-circuit tests
raise `AssertionError` if reached; the gate must reject the asset
before the HTTP call goes out.

## Open P1 items still on the audit list

- P1 #4 (public `PipelineRunner` geometry-provider API and tests with a
  fake provider injected).
- P2 (VLM_GEOMETRY docs refresh).

## Safety boundary

No API key is printed or persisted. The gate never Base64-encodes a
rejected asset, so an over-cap file never reaches the HTTP layer.
