# Decision log

Project-level decisions that are stable enough to outlive a single
handoff. Each file in this directory captures one decision, the
alternatives considered, and the rationale. The root `HANDOFF.md`
references the active decisions; new ones are appended here so the
HANDOFF stays small.

## Active decisions

- `2026-07-08-manual-mineru.md` — MinerU is invoked manually by the user
  (web or desktop client); the pipeline reads a folder of artifacts, not
  an API.
- `2026-07-08-asset-localization.md` — every remote image URL becomes a
  stable local asset ID before any downstream step; never reference a
  remote URL in a final export.
- `2026-07-08-outline-driven.md` — final export granularity and
  regrouping are driven by a user-supplied outline; unmatched items go
  to a single `_misc` export so no content is dropped.
- `2026-07-08-evidence-typing.md` — geometry relations carry an explicit
  `evidence` field; only `problem_text`, `diagram_mark`, and
  `problem_text_and_diagram_mark` may be promoted to confirmed givens,
  while `visual_inference` remains a review-only flag.
- `2026-07-08-runtime-and-ui.md` — Python is the runtime; the operator
  UI is a local web dashboard only, no native desktop shell.
- `2026-07-08-reorganization-modes.md` — three explicit modes (A, B, C)
  govern how content may be reordered inside an export; default is B.
- `2026-07-10-mode-c-bridges.md` — Mode C inserts one transition
  paragraph per adjacent pair of plans via a pluggable
  `BridgeProvider` (default `MockBridgeProvider`). Mode B remains
  deterministic and bridge-free.
- `2026-07-10-negative-keyword-veto.md` — outline leaves may declare
  `negative_keywords` / `negative_patterns` that veto a positive match
  when the item text carries an unambiguous "this is not my topic"
  signal. Resolves the xfail on the g8 fixture's `12.5 小结` routing.
- `2026-07-10-chapter-stopwords.md` — outline topics may declare
  `chapter_stopwords` (inherited top-down) so the matcher scrubs
  chapter-wide prose noise from the keyword substring check. Patterns
  are deliberately unaffected.