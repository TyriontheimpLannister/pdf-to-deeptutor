# Full domain-neutralization pass (2026-07-10)

Goal: make the repo **completely domain-neutral** — remove every remaining
math / subject-specific reference across source, tests, docs, scripts, and
fixtures, so nothing in-tree ties the tool to a particular subject domain.
User request: "需要保持此仓库完全领域中性" (scope: Tier 1+2+3; fixture
rewrite uses generic placeholder text, no subject binding).

## What changed

### Fixture (Tier 2)
- Renamed `demos/inbox-sample/g8-triangle-ch03` → `sample-chapter-01`
  (via `git mv`, history preserved; 4 images unchanged).
- Rewrote `full.md`, `layout.json`, `meta.json` to generic placeholder
  content (第一章 示例主题 / 基本概念 / 主要性质 / 例题 / 习题 / 小结).
  Test contracts preserved: 4 image refs, 4 pages, task_id `sample-chapter-01`,
  and a keyword-free `_misc` fallback paragraph.
- Gotcha: the intro must NOT contain "示例" — the substring "例" matches the
  `examples` outline keyword and suppresses the `_misc` plan. Used "占位主题".
- Rewired 5 fixture-coupled tests (`test_asset_localizer`, `test_inbox_loader`,
  `test_pipeline_runner`, `test_bookview`, `test_export`) to the new dir name,
  neutral task_id / filename / content assertions, and `subject=general` /
  `stage=sample`.

### Source code
- `src/pdf2dt/pipeline/runner.py`: defaults `subject="math"→"general"`,
  `stage="middle-G8"→"sample"` (both `run()` and the module-level wrapper).
- `src/pdf2dt/assets/downloader.py`: `user_agent` `"math-content-preprocessor/0.1"`
  → `"pdf-to-deeptutor/0.1"`.
- `src/pdf2dt/__init__.py`: package docstring self-named as the upstream repo
  → corrected to `pdf-to-deeptutor`.
- `src/pdf2dt/outlining/items.py`: comment "Chinese math textbooks" →
  "Chinese textbooks/documents"; "(geometry, review)" → "(figure analysis,
  review)".

### Docs & scripts (Tier 1)
- `README.md`: example export filenames (`math-G8-*` → `topic-*`/`_misc-*`),
  fixture copy paths, `schemas/ … geometry` → `… items`.
- `scripts/run_pipeline.py`, `scripts/init_inbox_meta.py`, `scripts/README.md`:
  neutralized docstring/usage examples (learner-brand `学之舟` and math
  filenames → `my-book` / `sample-document.pdf`).
- `docs/EXPORT_SPEC.md`: file-naming examples + figure-description guidance
  ("mathematically relevant facts" → interpretation-relevant facts).
- `docs/DATA_MODEL.md`: "structured figure" / "figure analysis relation"
  fields + examples genericized (points/segments/perpendicular/collinearity →
  named entities / regions / adjacency / grouping / ordering).
- `docs/decisions/2026-07-08-evidence-typing.md`: geometry example +
  STEM bullet genericized.
- `CONTRIBUTING.md`: `schemas/ … geometry` → `… items`.
- `demos/inbox-sample/README.md`: fixture name, misc-fallback description,
  `math-pp run` → `pdf2dt-run`.
- `docs/handoffs/2026-07-10-parity-backport.md`: stale fixture / private-export
  references updated.

### Schema (Tier 3)
- Removed unused `schemas/geometry-item.schema.json` (via `git rm`). Confirmed
  dead code: only `outline.schema.json` is loaded by `outline.py`; the deleted
  schema was referenced nowhere in src/tests/PIPELINE.md.

### Test text
- `tests/test_mineru_adapter.py`: private project path `学之舟-总复习` →
  `private-real-project`; synthetic math text ("Integers…", "Naturals…") →
  generic placeholder text (assertions updated to match).
- `tests/test_bookview.py`: docstring "pre-built math workspace" →
  "pre-built subject-specific workspace".

## Verification
- `python -m pytest tests/` → **77 passed / 2 skipped** (managed venv
  `…/python/envs/default`, `CODEBUDDY_SAFE_DELETE_SANDBOX=0`).
- Repo-wide leakage scan (全等/三角形/勾股/geometry/congruent/perpendicular/
  collinear/midpoint/mathematic/g8-triangle/elementary-math/学之舟/… and bare
  `math`): **zero domain matches**. The only surviving `math*` strings are
  provenance references to the upstream repo name `math-content-preprocessor`
  in `HANDOFF.md` and the archived handoffs — required cross-repo context, not
  domain leakage.

## Not done (intentional)
- Not committed / not pushed — the user asked only to neutralize the repo, not
  to commit. Changes are in the working tree / index (incl. the `git mv` rename
  and `git rm` deletion) awaiting an explicit push request.
