# Contributing to pdf-to-deeptutor

Thanks for considering a contribution. This document explains the
expectations so your PR has a good chance of being merged quickly.

## Code of conduct

Be respectful. The project is small and friendly. Disagreements are
fine; rudeness is not.

## Reporting issues

Open a GitHub issue and include:

- What you were trying to do.
- What command you ran.
- What you expected to happen.
- What actually happened (with the full error message and stack trace).
- The Python version (`python --version`) and OS.

## Development setup

```bash
git clone https://github.com/your-org/pdf-to-deeptutor.git
cd pdf-to-deeptutor
python -m venv .venv
. .venv/bin/activate        # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
pytest
```

The dev extras install `pytest`, `pytest-asyncio`, and `ruff`.

## Repository layout

- `src/pdf2dt/` — the Python package. One subpackage per pipeline
  stage (`inbox`, `assets`, `project`, `pipeline`). New stages follow
  the same pattern.
- `tests/` — pytest. Mirror `src/pdf2dt/`'s structure.
- `docs/` — product, architecture, decision log. Update the decision
  log when you make a stable change.
- `schemas/` — JSON schemas for cross-package contracts (outline,
  export-plan, geometry). Update these when the contract changes.
- `demos/` — synthetic fixtures. Anything under `demos/` is safe to
  commit.
- `inbox/` and `projects/` are gitignored. Do not commit real
  materials or generated workspaces.

## Coding style

- Python 3.10+. Use `from __future__ import annotations`.
- Follow PEP 8. The repo uses `ruff` with the default rule set;
  `ruff check src tests` should pass before pushing.
- Public functions and classes get type annotations. Pydantic models
  are preferred for any cross-module data type.
- Keep the public API small. Anything that should not be imported
  externally lives behind a leading underscore.

## Pipeline contracts

Each pipeline stage reads and writes files under `projects/<id>/`.
See `docs/PIPELINE.md` for the contracts and `docs/DATA_MODEL.md` for
the data shapes.

When you add or change a stage:

1. Update the stage description in `docs/PIPELINE.md`.
2. Update or add a JSON schema in `schemas/` if you introduce a new
   artifact type.
3. Add or update a decision in `docs/decisions/` for stable design
   choices.
4. Add tests, including at least one end-to-end test that runs against
   `demos/inbox-sample/`.

## Subject-area compatibility

The pipeline core is domain-agnostic. When adding support for a new
subject area, do not hard-code subject-specific vocabulary into the
package; instead, ship an example outline under
`demos/outlines/` or `outlines/_templates/` so users can copy and
adapt it.

## Pull requests

- One concern per PR.
- Include a clear PR description: what changed, why, and how to test.
- Reference any related issues.
- Keep the PR diff small enough to review in 15 minutes.
- Expect a few rounds of review.

## Versioning

This project follows Semantic Versioning 2.0. Breaking changes bump
the major version. Pipeline-stage contracts in `docs/PIPELINE.md` and
schemas under `schemas/` are considered public API and follow the
same rule.

## Release process

Maintainers run:

```bash
pytest
ruff check src tests
python -m build
python -m twine upload dist/*
```

and tag the commit with the new version.

## License

By contributing, you agree that your contributions will be licensed
under the MIT License. See `LICENSE`.
