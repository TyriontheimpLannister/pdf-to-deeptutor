# Mode C generative transitions via pluggable BridgeProvider

**Date:** 2026-07-10
**Status:** Active (Mode C available behind `--mode C`)
**Supersedes:** deferred ReorgMode.C stub (2026-07-08 design rationale)

## Context

Outline reorganization modes A / B are deterministic and
wording-preserving. ReorgMode.C was reserved in the schema for a
"full restructure" variant that lets the planner insert
generative connective text between adjacent plans, so a reader
moving from one topic's PDF to the next gets a short bridge
sentence instead of an abrupt topic jump.

The blocker was the missing provider contract. A naive
implementation that hard-coded `"Lorem ipsum"-style` strings
would have shipped placebo bridges; a naive implementation that
called an LLM directly would have forced users to configure
credentials and would have made the test suite impossible to run
offline.

## Decision

Add a tiny **BridgeProvider** Protocol with two built-in
implementations:

- **MockBridgeProvider** (default) writes a clearly-marked
  placeholder text such as `[Mock bridge] 本节承接自《{prev}》，
  进入《{next}》…`。Deterministic, no external LLM access.
- **NoOpBridgeProvider** returns `None` so tests can assert
  the "no bridges" path and operators can opt out without
  rewriting code.

A future v2 register a real LLM-backed provider through
`pdf2dt.export.register_bridge_provider(...)` without touching
planner / renderer / CLI.

### Planner contract

* `ExportPlan` grows a `bridges: list[Bridge]` field; each bridge
  belongs to the plan it **introduces** (i.e.
  `plans[i+1].bridges` contains the transition from `plans[i]`).
* Mode B never invokes the provider at all — `bridges` is
  always empty, the deterministic v1 contract is untouched.
* Mode C invokes the provider exactly once per adjacent pair
  (zero or one bridge per plan). A provider that returns `None`
  skips insertion; a provider that raises is captured as an
  explicit `[bridge-error]` bridge so review tools can see the
  failure without blocking the rest of the collection.

### Renderer contract

The renderer typesets bridge paragraphs at the **top of each
plan**, before the first item body. The first plan in a
collection has no predecessors so its bridge list is empty —
no placeholder is written there.

### CLI contract

`scripts/run_pipeline.py` and `scripts/rerun_late_stages.py`
gain `--bridge-provider {mock,noop}` (default = mock). The
provider name is forwarded into Stage 4c and recorded in
`export_plans/plans.json`.

## Consequences

- Stage 4c mode C work is now real but optional. **Mode B
  remains the v1 contract** for the public-default CLI UX.
- Planners that ship without an external LLM dependency stay
  deterministic; reviewers can immediately tell a mock bridge
  from a real one because of the `[Mock bridge]` marker.
- The plan collection carries full provider provenance
  (`follows_plan_id`, `follows_topic_id`, `provider`,
  `metadata`) so any future audit tooling can audit a specific
  bridge back to its inputs and provider version.

## Alternatives considered

- **Direct LLM call inside the planner.** Rejected: forces every
  user to configure credentials and breaks offline tests.
- **Hard-coded placeholder text in planner.** Rejected: makes
  Mode C indistinguishable from Mode B in the rendered PDF;
  fails the auditability contract.
- **Async provider protocol.** Deferred to v2. Current sync
  signature keeps the planner and the CLI a single, testable
  Python call.

## Update 2026-07-10 (later): outline + geometry providers

The original decision reserved `outline` and `geometry` as
"future v2" slots. They are now shipped as built-in providers,
both deterministic and offline. They are opt-in by name
(`mock` is still the default to preserve the v1 contract).

### New: `OutlineBridgeProvider` (name = `"outline"`)

* Consumes the parsed `Outline` model via a new
  `BridgeProvider.attach_context(BridgeProviderContext)`
  lifecycle hook the planner calls once per plan run.
* Computes the lowest common ancestor of the two adjacent
  leaves, then writes a bridge that names that ancestor and
  lists merged vocabulary keywords (CJK-friendly
  `、` separator, case-fold de-duplication).
* When the leaves have no common parent the wording flattens
  to a "shared keywords" variant; when the outline is missing
  it degrades to a clearly-marked `outline-fallback` placeholder
  carrying `fallback_reason` in `metadata` so reviewers can
  tell.

### New: `GeometryBridgeProvider` (name = `"geometry"`)

* Reads `review/geometry_figures.json` and quotes one
  `confirmed`/`corrected` relation from each side (preceding
  plan as "given", next plan as "to be applied"). Honoured the
  project's evidence rules — `visual_inference` / `unknown` are
  never quoted regardless of review state.
* Priority order: `equal_length` → `parallel` / `perpendicular`
  → midpoint, collinear, point_on_segment, equal_angle.
  Within a priority bucket the lexicographically smallest
  `key` wins so the output is deterministic.
* Returns `None` (not a placeholder) when either side has no
  confirmed relations or the file is missing / malformed —
  the planner simply skips insertion. A geometry-aware
  provider has nothing useful to say when there is no geometry
  data, and a fake placeholder would pollute the export.

### Contract impact

* The `BridgeProvider` Protocol grew an `attach_context`
  method (default no-op) so providers that need project-level
  data do not re-read files for every bridge. The planner
  invokes it once per `plan()` call with a
  `BridgeProviderContext { plans_by_id, review_dir, outline }`.
* `register_bridge_provider` now validates by `hasattr`
  (non-empty `name` + callable `generate_bridge`) instead of
  `isinstance(provider, BridgeProvider)`. `attach_context`
  is optional, so the registry accepts minimal stub providers
  unchanged.
* The `PlanAccessor` dataclass is the smallest possible read
  view of one plan (`plan_id`, `topic_id`, `title`,
  `item_count`, `asset_ids`, `items`). It is a frozen
  dataclass so providers cannot mutate the plan list they
  received.
* All 218/218 tests still pass; `test_bridges.py` grew from
  8 to 22 cases.
