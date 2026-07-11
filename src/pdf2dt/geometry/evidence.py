"""Evidence and review-state enumerations.

These are the only values the project allows for the ``evidence``
field on a geometry relation and the ``review_state`` field on a
figure or relation.  ``PROMOTABLE_EVIDENCE`` and
``NON_PROMOTABLE_EVIDENCE`` partition the evidence set so that
review code can answer "may this be auto-confirmed?" in one
constant-time check.
"""
from __future__ import annotations

from enum import Enum


class Evidence(str, Enum):
    """How a geometry relation was inferred.

    Only the values in :data:`PROMOTABLE_EVIDENCE` may be
    auto-promoted to confirmed givens.  ``visual_inference`` and
    ``unknown`` must remain a warning or review suggestion; they
    must never be stated as a confirmed condition.
    """

    PROBLEM_TEXT = "problem_text"
    DIAGRAM_MARK = "diagram_mark"
    PROBLEM_TEXT_AND_DIAGRAM_MARK = "problem_text_and_diagram_mark"
    VISUAL_INFERENCE = "visual_inference"
    UNKNOWN = "unknown"


# Evidence that a human may set to ``confirmed`` without further
# correction.  All others are flagged for review and excluded from
# the export PDF until the human issues a ``corrected`` or
# ``rejected`` decision.
PROMOTABLE_EVIDENCE: frozenset[Evidence] = frozenset(
    {
        Evidence.PROBLEM_TEXT,
        Evidence.DIAGRAM_MARK,
        Evidence.PROBLEM_TEXT_AND_DIAGRAM_MARK,
    }
)
NON_PROMOTABLE_EVIDENCE: frozenset[Evidence] = frozenset(
    {
        Evidence.VISUAL_INFERENCE,
        Evidence.UNKNOWN,
    }
)


class ReviewState(str, Enum):
    """A human review decision for a figure or a single relation."""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


# A relation whose review_state is in this set is eligible to be
# embedded in the export PDF.  ``unreviewed`` is not eligible
# because the project forbids silent promotion of
# ``visual_inference`` or ``unknown`` evidence.
INCLUDABLE_REVIEW_STATES: frozenset[ReviewState] = frozenset(
    {
        ReviewState.CONFIRMED,
        ReviewState.CORRECTED,
    }
)


__all__ = [
    "Evidence",
    "ReviewState",
    "PROMOTABLE_EVIDENCE",
    "NON_PROMOTABLE_EVIDENCE",
    "INCLUDABLE_REVIEW_STATES",
]
