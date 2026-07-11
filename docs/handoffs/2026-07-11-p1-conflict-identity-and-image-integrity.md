# P1 — conflict identity and image integrity (TRAE follow-up to codex re-audit)

> Detailed handoff record, 2026-07-11. Read the root `HANDOFF.md`
> first.  Archive — do not edit; create a new handoff if the
> situation changes.

## What codex found

Two P1 defects and one P2 hygiene defect were still open after
the previous re-audit:

1. **P1 conflict identity.** `HybridGeometryAnalyzer.analyze()` did
   two passes over the VLM candidates.  The first pass appended a
   `visual_inference` record when the candidate key was new; the
   second pass appended an `unknown` record for the same key when
   rules disagreed.  Both records shared the same `relation_key`,
   and `GeometryFigure.relation(key)` returns the first match, so a
   `ReviewDecision` addressed to the conflict key could update the
   `visual_inference` record while the `unknown` record stayed
   unreviewed — leaving the figure on the renderer's block list
   even after the reviewer thought they had corrected the conflict.
2. **P1 image integrity.** `check_vlm_asset()` only inspected the
   image header and declared size.  A truncated PNG with a valid
   8-byte header passed the gate and could be Base64-submitted to
   the VLM.  The existing test deliberately accepted either
   branch (rejected or accepted) and so did not enforce rejection.
3. **P2 hygiene.** The two test files added in the previous
   revision were committed with CRLF line endings, so
   `git show --check` flagged them as having trailing whitespace.

## What was fixed

### P1 conflict identity (`src/pdf2dt/geometry/vlm.py`)

The two passes were collapsed into a single pass that decides the
outcome for each VLM candidate before appending:

1. If the candidate key already exists on the figure, log an
   "agrees on" observation and move on.
2. Else, look for a rule relation that targets the same entity set
   with a different relation type.  If found, append a single
   `GeometryRelation(evidence=Evidence.UNKNOWN,
   source_reference="vlm:<provider>:conflict", review_state=UNREVIEWED)`
   record; the conflict is the **only** record the figure holds for
   that key.
3. Otherwise append the normal `visual_inference` record.

`figure.relation(key)` therefore resolves to exactly the conflict
record, `ReviewStateStore.apply([CORRECT])` mutates that record,
and the renderer's `geometry_blocked_figures` list no longer
contains a stale unreviewed entry.

### P1 image integrity (`src/pdf2dt/geometry/resource_gate.py`)

`check_vlm_asset()` now opens the file twice via Pillow: first to
run `Image.verify()` (canonical Pillow check for internal
structure — CRCs, IDAT/IEND, end-of-image marker), then to run
`Image.load()` (actually decodes the pixel data).  Either step
raises on truncated or corrupt bytes and the existing exception
list routes the failure to a `VlmGateResult(ok=False,
error="cannot decode image: …")`.  The check runs before
`read_bytes()` and before any HTTP request, so a corrupt asset
never reaches the VLM.

The pre-existing permissive test
`test_check_vlm_asset_rejects_truncated_payload` was rewritten to
assert strict rejection (the previous "either branch" assertion
did not enforce the contract).  A new
`test_check_vlm_asset_rejects_truncated_jpeg` covers JPEG.  A new
provider-level test
`test_minimax_provider_rejects_truncated_payload` confirms the
HTTP transport is never called.

### P2 hygiene

`tests/test_geometry.py` and `tests/test_vlm_resource_gate.py`
were converted from CRLF to LF.  `git diff --check` and
`git show --check <new-commit>` on the new commit are clean.

## Acceptance tests

- `test_hybrid_analyzer_records_conflict_observation` — rewritten
  to assert exactly one record per conflict key; verified to fail
  on the pre-fix code (StopIteration) and pass after the fix.
- `test_hybrid_analyzer_conflict_relation_lands_in_review_queue` —
  extended to assert that after `CORRECT` there is still exactly
  one record for the conflict key, that record's review state is
  `CORRECTED`, and `figure.relation(key)` resolves to it.  This is
  the codex acceptance scenario 1-4 verbatim.
- `test_hybrid_analyzer_no_key_falls_back_to_rules` — new E2E
  that drops both `MINIMAX_API_KEY` and `SENSENOVA_API_KEY` from
  the environment, builds `MiniMaxM3Provider()` with no explicit
  key, and asserts the figure contains only rules-sourced
  relations while the audit log records the no-key failure.
- `test_check_vlm_asset_rejects_truncated_payload` — rewritten
  to assert strict rejection.
- `test_check_vlm_asset_rejects_truncated_jpeg` — new, mirror
  case for JPEG.
- `test_minimax_provider_rejects_truncated_payload` — new, end-to-end
  test that the `MockTransport` handler is never called when the
  gate rejects a truncated payload.

## Verification

- `uv run python -m pytest --no-header -q` — **273 passed** (was
  270 before this revision).
- `uv run ruff check src tests` — clean.
- `git diff --check` — clean.
- `git show --check <this-commit>` — clean (the older P0/P1 #1 /
  P1 #2 commits remain CRLF; they are history, the new commit is
  the one codex asked to verify).
