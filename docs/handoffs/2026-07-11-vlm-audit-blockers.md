# VLM Geometry Audit — Active Blockers

> Detailed handoff record, 2026-07-11. The active entry point is the project
> root `HANDOFF.md`.

## Context

Commit `0945ca8` added rules-first VLM geometry enrichment with MiniMax-M3 and
SenseNova providers. The pipeline intentionally retains deterministic rules and
marks VLM additions as `visual_inference` plus `unreviewed`.

The audit confirmed that this evidence policy and the MiniMax Anthropic request
shape are sound. It also found blockers that prevent calling the hybrid path
production-ready.

## Findings and required resolution

### P0 — Stage 5 can erase human review state

`scripts/run_pipeline.py` executes `analyze_geometry()` every time `--geometry`
is supplied. Stage 5 writes a new `review/geometry_figures.json`, thereby
discarding confirmed/corrected/rejected states. `review/review_state.json` is
not cleared, so it becomes inconsistent with the queue.

Resolve by matching `rerun_late_stages.py`: skip an already completed Stage 5
unless an explicit `--force-geometry` is supplied. Define force behavior before
implementation: either merge review state by `(figure_id, relation_key)` or
require a deliberate review reset and record it in the manifest. Add an E2E
test that applies a decision, reruns the command, and proves the decision stays
effective by default.

### P1 — malformed VLM output can abort Stage 5

`src/pdf2dt/geometry/vlm.py:_parse_response()` calls `float()` on arbitrary
model output. For example, `confidence: "high"` raises `ValueError`. Both
providers also assume a dict/list response shape after a successful HTTP 200,
which can raise `AttributeError` or `TypeError`.

Resolve by validating every parsed field and response shape; map all provider,
JSON and normalization failures to `VlmResponse(error=...)`. The rules result
must still be persisted. Add regression tests for a nonnumeric confidence,
list-shaped JSON body, malformed provider choices and a valid partial response.

### P1 — no local image/request resource gate

The providers directly call `read_bytes()` and Base64 encode each asset. There
is no MIME whitelist, byte cap, decoded-pixel cap, or provider-specific request
size preflight. MiniMax-M3's documented image limit is 10 MB and request limit
is 64 MB.

Resolve by rejecting unsupported/oversize assets before read/encode and
returning a VLM error so rules continue. Use Pillow to validate decoded image
dimensions and add boundary tests. Keep caps configurable only if a concrete
provider requirement needs it.

### P1 — VLM runs for every figure and audit evidence is incomplete

`HybridGeometryAnalyzer` calls the VLM for all figure-bound items, including
ones where rules already produced high-confidence text-backed relations. This
increases paid calls, third-party data transfer and false proposals. The report
stores only a response SHA-256; it lacks raw response, image SHA-256, model ID,
provider endpoint/request metadata and per-call timing.

Resolve by defining an explicit trigger: call VLM when rules found no relation,
only low-confidence/non-promotable evidence, or unresolved visual observations.
Persist raw response locally under `reports/` with a record containing image
hash, provider, model, sanitized request metadata, result status and error. Do
not store API keys. Add conflict candidates to the review queue rather than
silently treating unrelated entity sets as independent facts.

### P1 — public pipeline API cannot select VLM

Only the CLI passes a hybrid analyzer. `PipelineRunner.run()` and public
`run_pipeline()` expose no geometry provider/analyzer option, so programmatic
calls remain rules-only.

Resolve by adding a typed analyzer/provider parameter through both APIs and
pinning it with a test that injects a fake VLM provider without network access.

### P2 — operation and handoff documentation

Hybrid mode sends image bytes and up to 8,000 characters of OCR-derived text
to the selected external provider. `docs/VLM_GEOMETRY.md` should make this
data-transfer choice explicit and show `rules` as the no-network alternative.
The previous `HANDOFF.md` still described VLM as future work; the active
handoff now supersedes that statement.

## Verification at handoff

- VLM, geometry and renderer targeted suite: 37 tests passed in the independent
  audit.
- Pipeline-related tests also passed in the independent audit.
- Full suite before this audit: 230 tests passed.
- Global Ruff currently reports one pre-existing `W292` in
  `scripts/init_inbox_meta.py:162`.

## Safety boundary

Do not request, print, persist or commit any API key. A no-key hybrid run must
remain a successful rules-only export with failure records written locally.
