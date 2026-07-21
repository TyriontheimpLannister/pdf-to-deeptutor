"""Stage 2.5 document-level structure recovery."""

from .alignment import AlignmentSummary, LayoutVisual, align_markdown_to_layout
from .recovery import (
    DocumentBlock,
    DocumentRelation,
    DocumentStructure,
    recover_document_structure,
)

__all__ = [
    "AlignmentSummary",
    "DocumentBlock",
    "DocumentRelation",
    "DocumentStructure",
    "LayoutVisual",
    "align_markdown_to_layout",
    "recover_document_structure",
]
