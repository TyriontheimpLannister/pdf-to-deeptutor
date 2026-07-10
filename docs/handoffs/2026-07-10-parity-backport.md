# 2026-07-10 — Full parity backport (upstream `c9ae123`)

> Archived detail moved out of `HANDOFF.md` per protocol §4 (100-line cap).
> Owner: ian · Status at archive time: DONE

## What was done

Caught this fork up to `../math-content-preprocessor` @ `c9ae123`. All new
code backported domain-neutral (`math_pp` → `pdf2dt`); no math taxonomy shipped.

Stages / modules backported:

- **Stage 3** — BookView builder (`src/pdf2dt/bookview/builder.py`) + MinerU
  `pdf_info[]` adapter (`src/pdf2dt/bookview/mineru_adapter.py`).
- **Stage 4c** — export planner (`src/pdf2dt/export/planner.py`).
- **Stage 7** — PDF renderer (`src/pdf2dt/export/renderer.py`, fpdf2).
- **Mode C** — generative transitions via pluggable `BridgeProvider`
  (`src/pdf2dt/export/bridges.py`); CLI `--mode C --bridge-provider {mock,noop}`;
  Mode B stays bridge-free.
- Synced `src/pdf2dt/outlining/{outline,items,matcher}.py` with upstream
  (`negative_keywords` / `negative_patterns` / `chapter_stopwords`) and
  `schemas/outline.schema.json`.

Tooling / config:

- Added `scripts/rerun_late_stages.py` (re-run Stages 3/4b/4c/7 on an existing
  workspace).
- Wired `--book-view` / `--mode {A,B,C}` / `--bridge-provider` into
  `scripts/run_pipeline.py`; neutralized the math-specific `--subject` default
  and docstring examples in both scripts.
- Added runtime deps `fpdf2>=2.8`, `Pillow>=10.0`; `pypdf>=4.0` to dev extras
  (PDF footer assertions). Recorded in `pyproject.toml`.

Tests added (all domain-neutral):

- `tests/test_bridges.py`, `tests/test_mineru_adapter.py` — ported
  verbatim/near-verbatim (pure-unit).
- `tests/test_bookview.py`, `tests/test_export.py` — end-to-end tests reuse the
  in-repo `demos/inbox-sample/sample-chapter-01` inbox + neutral
  `outlines/sample-outline-v1.yaml` instead of the private math fixtures. They
  assert generic contracts (topic grouping, provenance, Mode C bridge count),
  not math-specific topic ids.

## Verification

- `python -m pytest tests/` → **77 passed / 2 skipped** (managed venv
  `…/python/envs/default` with `pytest fpdf2 pypdf Pillow`).
  - Skip #1 `test_adapt_mineru_layout_real_project` — needs the private
    real-project MinerU export, not shipped here.
  - Skip #2 `test_renderer_without_cjk_font` — this `fpdf2` build bundles no
    `DejaVuSans.ttf`, so the non-CJK fallback path can't run here (runs fine
    wherever DejaVu is available, e.g. upstream).
- Sandbox note: the safe-delete hook raises `OSError` cleaning pytest temp
  dirs; re-run with `CODEBUDDY_SAFE_DELETE_SANDBOX=0` for a clean `exit 0`.

## Design references

- Mode C bridges: `docs/decisions/2026-07-10-mode-c-bridges.md` (upstream-tracked).
- A real LLM-backed `BridgeProvider` is the only open v2 item; the Protocol and
  `register_bridge_provider(...)` slot are already in place, no code change
  needed here until a provider is chosen.
