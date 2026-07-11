"""Pre-flight checker — validates MinerU output before pipeline processing.

Runs structural, content, image-reference, and layout-consistency checks
against an inbox task directory *before* Stage 0 creates a workspace.
Produces a structured report so the user can decide whether to proceed.
"""

from .checker import PreFlightChecker, PreFlightError, check_task
from .report import CheckResult, CheckSeverity, PreFlightReport

__all__ = [
    "PreFlightChecker",
    "PreFlightError",
    "check_task",
    "CheckResult",
    "CheckSeverity",
    "PreFlightReport",
]
