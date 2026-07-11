"""Review state — Stage 6.

Stage 6 turns the geometry queue produced by Stage 5 into a
per-relation review state.  The package exposes:

* :class:`ReviewAction` — what a human can do to a relation.
* :class:`ReviewDecision` — one apply call.
* :class:`ReviewStateStore` — loads / saves the on-disk state.
* :func:`apply_review` — pipeline entry point.

Promotion rules
---------------

* :data:`pdf2dt.geometry.PROMOTABLE_EVIDENCE` may be set to
  ``confirmed`` without correction.
* :data:`pdf2dt.geometry.NON_PROMOTABLE_EVIDENCE` may only be set
  to ``corrected`` (with new entities) or ``rejected``.  Setting
  them to ``confirmed`` raises :class:`PromotionError`.

The rules are checked both at apply time and when the renderer
later walks the queue to embed relations in a PDF.
"""
from .store import (
    PromotionError,
    ReviewAction,
    ReviewDecision,
    ReviewStateStore,
    apply_review,
    load_review_state,
)

__all__ = [
    "PromotionError",
    "ReviewAction",
    "ReviewDecision",
    "ReviewStateStore",
    "apply_review",
    "load_review_state",
]
