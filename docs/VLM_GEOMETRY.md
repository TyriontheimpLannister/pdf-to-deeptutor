# VLM-backed Geometry Extraction

> Updated: 2026-07-11 — refreshed after the P0 + full P1 audit fixes.
> This is the human-facing reference for the optional Vision Language
> Model (VLM) hybrid stage. See
> `docs/handoffs/2026-07-11-vlm-audit-blockers.md` for the
> audit that drove the rewrite.

## What this is and what it is not

VLM extraction is an *optional* enrichment layer for Stage 5. It
never runs unless a caller explicitly asks for it. When Stage 5 runs
without VLM, the deterministic rules-only `GeometryAnalyzer` produces
the entire output (entities, relations, evidence), and no network
call is ever made.

When a caller does opt in (via the CLI flag, the public Python API,
or a custom Python entry point), the same Stage 5 still runs through
a `HybridGeometryAnalyzer`. The rules-only output is the **floor**:
every VLM relation is added to the rules result, marked
`Evidence.VISUAL_INFERENCE`, and flagged
`ReviewState.UNREVIEWED`. A bad or missing VLM response cannot
replace or erase the rules result.

## Quick decision matrix

| Caller surface | Needs network? | Selection knob | Handoff doc |
|----------------|----------------|----------------|-------------|
| `run_pipeline.py` CLI (`--geometry-provider rules`) | No | `--geometry-provider rules` (default) | this page |
| `run_pipeline.py` CLI (`--geometry-provider hybrid-...`) | **Yes** (opt-in) | `--geometry-provider {hybrid-minimax-m3,hybrid-sensenova}` + matching API-key env var | this page |
| `PipelineRunner.run(...)` / `run_pipeline(...)` programmatic API | No | default (omits the kwarg) | this page |
| `PipelineRunner.run(... geometry_analyzer=...)` | depends on the injected analyzer | pass any `GeometryAnalyzer` subclass (see `docs/handoffs/2026-07-11-p1-pipeline-runner-geometry-api.md`) | P1 #4 handoff |

> The default public API surface never reaches a remote provider. To
> trigger a real MiniMax-M3 / SenseNova request you must either run
> the CLI with `--geometry-provider hybrid-...` or supply an analyzer
> that wraps one of `MiniMaxM3Provider` / `SenseNovaProvider`.

## Two providers, one contract

Both providers implement the same protocol (`analyze_image(path,
context) -> VlmResponse`) so the hybrid analyzer is provider-agnostic.

### MiniMax-M3 (`MiniMaxM3Provider`, provider name `hybrid-minimax-m3`)

- Endpoint: Anthropic-compatible Messages API
  (`{base_url}/v1/messages`, default base URL =
  `https://api.MiniMax.com/v1`).
- Environment: requires `MINIMAX_API_KEY` in the runtime
  environment.
- Per-image cap: 10 MB; per-request cap: 64 MB (sourced from the
  MiniMax-M3 docs).
- Authorization header: `x-api-key: <MINIMAX_API_KEY>`.
- Provider does not call any endpoint before the API key is
  configured; a missing key is reported as
  `VlmResponse(error="MINIMAX_API_KEY is not set")`.

### SenseNova (`SenseNovaProvider`, provider name `hybrid-sensenova`)

- Endpoint: OpenAI-compatible chat-completions API
  (`{base_url}/v1/chat/completions`, default base URL =
  `https://openapi.sensenova.cn/v1`).
- Environment: requires `SENSENOVA_API_KEY` in the runtime
  environment.
- Authorization header: `Authorization: Bearer <SENSENOVA_API_KEY>`.
- Provider does not call any endpoint before the API key is
  configured; a missing key is reported as
  `VlmResponse(error="SENSENOVA_API_KEY is not set")`.

### What gets uploaded

For every paid call the provider sends:

- The original figure asset (PNG / JPG / JPEG / GIF / WEBP), encoded
  as base64 inline image content with the appropriate MIME type.
- A `context_chars`-bounded copy of the figure caption and any
  preceding text the rules analyzer saw (this is **prompt text the
  caller already placed in the project workspace**; it is *not* any
  other project file).
- The system prompt and the JSON-only instruction (the structured
  output schema lives in `src/pdf2dt/geometry/vlm.py` — see
  `_extract_message_text` / `_extract_sensenova_text` for the exact
  payload shape; both providers refuse to call out if the asset
  doesn't exist or fails the resource gate).

The provider never sends or persists the API key. The base URL,
HTTP method, model id, and request byte count are recorded in the
audit log so the human reviewer can see exactly what went on the
wire (see "Audit log" below).

## Selection strategy: `should_call_vlm`

The hybrid analyzer does not call the provider on every figure. It
runs `should_call_vlm(figure)` first; if the rules result is already
sufficient the figure is left untouched and a call record is written
with `status="skipped"`.

The decision tree (see `src/pdf2dt/geometry/vlm.py`):

| `should_call_vlm` reason | Meaning |
|--------------------------|---------|
| `rules_blank` | The rules analyzer produced no relations. |
| `rules_blank_no_relations` | Same, with the v1.0 wording for graphs that only have nodes. |
| `rules_only_non_promotable` | Only `visual_inference` / `unverified` evidence was emitted (every rules relation had to be downgraded because no typed match). |
| `rules_low_confidence` | All rules relations fell below the configured confidence threshold. |
| `rules_have_visual_observations` | The rules analyzer surfaced a `visual_observations` entry that is *unresolved* — that is, a regex pattern fired but no entity-resolved relation was produced. The hybrid call is the only path that can promote these. |
| `rules_sufficient` | The rules analyzer is confident; the provider is skipped to save quota. |

The hybrid runtime also observes:

- **Conflict observations.** When a rules relation and a VLM
  relation cover the same `(entity_a, entity_b, relation_type)` tuple
  but disagree on a side detail, the analyzer appends a textual
  observation instead of silently overwriting either side. The audit
  log surfaces these so a human reviewer can decide.

The call counter plus reason for every figure land in the manifest's
`metadata.vlm_summary` block (see "Audit log" below).

## Resource gate: `check_vlm_asset`

Both providers run `check_vlm_asset(image_path)` immediately before
they base64-encode the asset (see `src/pdf2dt/geometry/resource_gate.py`).
This audit gate (P1 #2) was added because no MIME whitelist, byte
cap, or decoded-pixel cap existed before:

- **MIME whitelist**: only `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
  are accepted. Anything else is rejected with
  `asset_rejected: unsupported image extension: <suffix>`.
- **Byte cap**: any file larger than `_DEFAULT_MAX_IMAGE_BYTES`
  (10 MB; mirrors the MiniMax-M3 image cap) is rejected with
  `asset_rejected: image too large: <bytes> bytes (max ...)`.
- **Decoded-pixel cap**: any image whose `width * height` exceeds
  `_DEFAULT_MAX_PIXELS` (25 million) is rejected with
  `asset_rejected: image too large: <w>x<h> = <pixels> pixels
  (max ...)`. Pillow is a hard dep so this branch is always
  reached; the probe uses the lazy `Image.open()` context so we
  never actually decode the bitmap.
- **Missing / undecodable**: missing files and Pillow decode errors
  produce the same shape of error string.

A `check_vlm_asset` rejection becomes a `VlmResponse(error="asset_rejected: ...")` and the audit log marks the call with `status="rejected"` —
distinct from `"failed"` which is reserved for network / parse /
shape failures.

Public surface:

```python
from pdf2dt.geometry import check_vlm_asset, VlmGateResult

result: VlmGateResult = check_vlm_asset(path)
if not result.ok:
    raise RuntimeError(result.error)
# result.media_type is the MIME the provider will use.
# result.pixel_count is the decoded width * height, or 0 on rejection.
```

## Safe-fallback contract

Every code path inside `HybridGeometryAnalyzer` and the two
providers is written so a Stage 5 run is *never* aborted by a VLM
problem:

- Provider returns no text / malformed choices / unknown shape →
  `_parse_response` returns `VlmResponse(error=...)` and counts the
  discarded relations in `parse_error`. Rules result is unchanged.
- `httpx.HTTPError`, `JSONDecodeError`, `OSError` are caught at the
  provider boundary; the caller sees a `VlmResponse(error=...)` with
  the exception class plus message.
- Confidence is coerced: a missing `confidence` is treated as
  low-confidence; a non-numeric `confidence` is dropped to `0.0` and
  flagged with `confidence_unknown=True`.
- Bad relation dictionaries (`type` outside the `RelationType`
  enum, missing entities, etc.) are silently dropped from the
  response. The audit log records `discarded N invalid relation(s)`
  so the human reviewer notices.

The Stage 5 result therefore always reflects a deterministic
rules-only world plus whatever VLM relations were verifiable.

## Audit log: VlmCallRecord + vlm_summary + reports/vlm-raw/

Every paid call writes a `VlmCallRecord` and the analyzer appends it
to `HybridGeometryAnalyzer.call_records`. The pipeline then writes
`review/geometry_vlm_report.json` and a `metadata.vlm_summary` block
in the project manifest:

| Field | What it is |
|-------|-----------|
| `asset_sha256` | SHA256 of the file the provider received. |
| `model` | Concrete model id. |
| `endpoint` | Provider endpoint string. |
| `request_bytes` | Bytes on the wire (asset + prompt). |
| `context_chars` | Length of the prompt-side text payload. |
| `elapsed_ms` | Wall-clock time spent in the provider call. |
| `response_sha256` | SHA256 of the provider response body. |
| `raw_response_path` | Where the response body was persisted (under `reports/vlm-raw/<figure_id>.json`). Empty when the call was skipped. |

Plus per-call status:

| `status` | Meaning |
|---------|---------|
| `skipped` | `should_call_vlm` returned `rules_sufficient`. |
| `ok` | Provider returned at least one verifiable relation. |
| `failed` | Network error, parse error, malformed body, etc. |
| `rejected` | The resource gate blocked the asset. |

The `vlm_summary` block in the manifest contains the totals per
status plus a breakdown of the skip reasons. **`status="failed"`
is a quorum signal — if any figure fails, the reviewer should look
at `vlm_summary` before merging.**

For each `status="ok"` call the response body is persisted under
`reports/vlm-raw/<figure_id>.json` (path is inside
`<project_root>/reports/`). The file contains the response body, the
computed `response_sha256`, and the elapsed-ms; **the API key never
appears in the body, in the headers, or in any persisted artifact.**

## CLI surface (`run_pipeline.py`)

```bash
# Default — rules-only, no network.
python -m pdf2dt.tools.run_pipeline --project-root <path> ... --geometry

# Opt in to MiniMax-M3 (requires MINIMAX_API_KEY).
python -m pdf2dt.tools.run_pipeline --project-root <path> \
    ... --geometry \
    --geometry-provider hybrid-minimax-m3

# Opt in to SenseNova (requires SENSENOVA_API_KEY).
python -m pdf2dt.tools.run_pipeline --project-root <path> \
    ... --geometry \
    --geometry-provider hybrid-sensenova

# Re-run Stage 5 even when the manifest marks it complete.
python -m pdf2dt.tools.run_pipeline --project-root <path> \
    ... --geometry \
    --geometry-provider rules \
    --force-geometry
```

The CLI calls `build_geometry_analyzer(name)` to convert the string
to a concrete analyzer. The choices are:

- `rules` (default) — `GeometryAnalyzer()`.
- `hybrid-minimax-m3` — `HybridGeometryAnalyzer(provider=MiniMaxM3Provider())`.
- `hybrid-sensenova` — `HybridGeometryAnalyzer(provider=SenseNovaProvider())`.

A typo raises `ValueError("unknown geometry provider: <name>")`
before any HTTP work begins.

## Public Python API

Programmatic callers (notebooks, UI hooks, tests, downstream tools)
gain a `--geometry-provider`-equivalent through `PipelineRunner`:

```python
from pdf2dt.assets import LocalMirrorDownloader  # or your own AssetDownloader
from pdf2dt.geometry import (
    HybridGeometryAnalyzer,
    MiniMaxM3Provider,        # or SenseNovaProvider
    GeometryAnalyzer,         # for a custom subclass
)
from pdf2dt.pipeline import PipelineRunner

runner = PipelineRunner(LocalMirrorDownloader(...))

# Rules-only (no network).
result = runner.run(..., preflight=False)

# Hybrid (requires MiniMax-M3 network + MINIMAX_API_KEY).
minimax_analyzer = HybridGeometryAnalyzer(provider=MiniMaxM3Provider())
result = runner.run(
    ...,
    geometry_analyzer=minimax_analyzer,
    preflight=False,
)

# Force re-extraction even on resume.
result = runner.run(
    ...,
    geometry_analyzer=minimax_analyzer,
    force_geometry=True,
    preflight=False,
)
```

`run_pipeline(...)` exposes the exact same `geometry_analyzer` and
`force_geometry` keyword arguments. Pass any
`GeometryAnalyzer` subclass — the runner does not pin callers to
the hybrid flavor (see
`docs/handoffs/2026-07-11-p1-pipeline-runner-geometry-api.md` for
the test contract).

## Quick test recipes

```bash
# Pure unit tests for the safe-fallback contract.
uv run python -m pytest tests/test_geometry.py -k "vlm or hybrid" -v

# Resource gate regression tests.
uv run python -m pytest tests/test_vlm_resource_gate.py -v

# End-to-end runner smoke with a fake provider (no network).
uv run python -m pytest tests/test_pipeline_runner.py -k "geometry_analyzer" -v
```

## References

- Audit that drove this rewrite:
  `docs/handoffs/2026-07-11-vlm-audit-blockers.md`.
- Safe-fallback contract + selection strategy implementation notes:
  `docs/handoffs/2026-07-11-p1-vlm-safety-and-selection.md`.
- Resource gate implementation notes:
  `docs/handoffs/2026-07-11-p1-vlm-resource-gate.md`.
- Public runner API implementation notes:
  `docs/handoffs/2026-07-11-p1-pipeline-runner-geometry-api.md`.
- Source: `src/pdf2dt/geometry/vlm.py`,
  `src/pdf2dt/geometry/resource_gate.py`,
  `src/pdf2dt/geometry/analyzer.py`.
- Stage 5 orchestrator: `src/pdf2dt/pipeline/runner.py`.
