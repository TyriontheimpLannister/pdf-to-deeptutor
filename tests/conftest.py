"""Pytest configuration: ensure the ``pdf2dt`` source is importable.

When the package is installed editable (``pip install -e .``) this is
not needed, but when tests are invoked directly with ``python -m
pytest tests/`` from a clone without installation the ``src/`` layout
needs to be on ``sys.path``. We resolve the project root relative to
this file and prepend ``src/`` so ``import pdf2dt`` succeeds.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
