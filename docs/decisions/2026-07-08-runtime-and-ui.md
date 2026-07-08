# Runtime language and operator UI

## Decision

- Pipeline core: **Python** (3.10+).
- Operator UI: **local web dashboard only** — no native desktop shell.

## Why

- Python offers mature PDF, image, OCR-adjacent, schema, and HTTP
  libraries with first-class Windows support.
- Local packaging and distribution are simpler in Python (PyInstaller
  or Nuitka) than in Rust or Go for an end-user installer on Windows.
- The user only needs to drive occasional one-off preprocessing runs;
  a web dashboard is sufficient and easier to iterate than a native UI.
- A Rust component is still possible later for the asset-download
  bottleneck if measurements justify it. We do not pre-pay for it.

## How

- `src/pdf2dt/` is the Python package; modules split by pipeline
  stage (`inbox`, `assets`, `project`, `pipeline`).
- The web dashboard is Phase 5. Until then, `scripts/run_pipeline.py`
  is the operator entry point.

## Trade-offs

- The web UI has to be hosted somewhere (loopback is fine for a local
  tool). We are not building authentication or remote deployment.
- We accept slightly slower asset downloads in exchange for faster
  iteration in the earlier stages.
