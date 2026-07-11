# VLM Re-audit — Remaining Blockers for Trae

> Detailed handoff record, 2026-07-11. Read the root `HANDOFF.md` first.

## Audit scope

This review checked Trae's commits after the first VLM audit:

- `586758d` — completed/skipped status oscillation
- `4b0070a` — real MIME detection
- `717472c` — rule/VLM conflict review queue

The original Stage 5 status oscillation and renamed-image MIME issues are
fixed. The remaining issues below are still actionable.

## P1 — conflict candidates duplicate the review key

### Cause

`HybridGeometryAnalyzer.analyze()` first appends a normal VLM relation with
`evidence=visual_inference` when the candidate key is not already present.
For a conflicting candidate it then appends a second
`evidence=unknown` relation with the same type and entities. Both records have
the same `relation_key()`.

`GeometryFigure.relation(key)` returns the first matching relation, so a
`ReviewDecision` addressed to the conflict key may update the earlier visual
inference record while the later `unknown` record stays unreviewed. The
renderer can therefore remain blocked after the reviewer apparently corrected
the conflict.

### Evidence

- Candidate insertion: `src/pdf2dt/geometry/vlm.py:633-652`
- Conflict insertion: `src/pdf2dt/geometry/vlm.py:659-692`
- Key-only lookup: `src/pdf2dt/geometry/models.py:143-146`

### Required fix

Determine conflict before adding the ordinary VLM relation. For a conflict,
append only one `unknown` record, or otherwise give it an unambiguous stable
review identity. Preserve the original rules relation unchanged.

### Acceptance test

Construct a rule `parallel(AB)` and VLM `perpendicular(AB)` candidate. Assert:

1. There is one, not two, relation with key `perpendicular::ab`.
2. Its evidence is `unknown` and it is unreviewed.
3. `ReviewStateStore.apply(CORRECT)` updates that exact record.
4. The renderer no longer lists its figure as blocked after correction.

## P1 — gate does not fully verify accepted image bytes

### Cause

`check_vlm_asset()` uses `PIL.Image.open()` to inspect the header and dimensions
but does not call `verify()` or fully load the image. A truncated PNG with a
valid header can pass and be Base64-submitted to a VLM.

The current truncated-payload test deliberately accepts either outcome, so it
does not enforce rejection.

### Evidence

- Gate: `src/pdf2dt/geometry/resource_gate.py:95-132`
- Permissive test: `tests/test_vlm_resource_gate.py:270-301`

### Required fix

After format/size inspection, reopen the file and call `verify()` (or load it
in a safe decode path). Treat decode errors as `VlmGateResult(ok=False)`.
Keep the check before `read_bytes()` and before HTTP submission.

### Acceptance test

Create a deliberately truncated PNG, attach an HTTP mock that fails if called,
and assert both `check_vlm_asset().ok is False` and provider response begins
`asset_rejected:`.

## P2 — repository hygiene and handoff protocol

- `git show --check 4b0070a` and `git show --check 717472c` report many
  trailing-whitespace entries caused by CRLF lines in newly added tests.
  Normalize the touched files and require `git show --check <new commit>`.
- The prior root `HANDOFF.md` grew to 190 lines. It has been replaced with a
  concise active handoff; keep it below the 100-line protocol cap.

## Verification snapshot

- Targeted resource-gate and geometry tests: 61 passed during re-audit.
- Global Ruff still reports one pre-existing missing newline in
  `scripts/init_inbox_meta.py:162`; do not fold unrelated cleanup into the VLM
  fix unless explicitly requested.
- The working tree was clean before this documentation handoff update.

## Safety boundary

Never read, print, persist or commit API keys. No-key hybrid mode must continue
using rules and write local failure records without contacting a provider.
