"""Data models for pre-flight check reports."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CheckSeverity(str, Enum):
    """Severity of a check finding."""

    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CheckResult(BaseModel):
    """Result of a single pre-flight check."""

    name: str
    severity: CheckSeverity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PreFlightReport(BaseModel):
    """Aggregated pre-flight report for one task directory."""

    task_id: str
    task_dir: str
    results: list[CheckResult] = Field(default_factory=list)
    overall_severity: CheckSeverity = CheckSeverity.OK
    should_proceed: bool = True

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == CheckSeverity.ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == CheckSeverity.WARNING]

    def add(self, result: CheckResult) -> None:
        self.results.append(result)
        if result.severity == CheckSeverity.ERROR:
            self.should_proceed = False
            if self.overall_severity != CheckSeverity.ERROR:
                self.overall_severity = CheckSeverity.ERROR
        elif result.severity == CheckSeverity.WARNING:
            if self.overall_severity == CheckSeverity.OK:
                self.overall_severity = CheckSeverity.WARNING

    def to_summary(self) -> str:
        """Human-readable one-line summary."""
        parts = [f"[{self.overall_severity.value}] {self.task_id}"]
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        ok = [r for r in self.results if r.severity == CheckSeverity.OK]
        if ok:
            parts.append(f"{len(ok)} ok")
        return " — ".join(parts)

    def to_detail(self) -> str:
        """Multi-line human-readable report."""
        lines = [f"Pre-Flight Report: {self.task_id}", f"Directory: {self.task_dir}", ""]
        for r in self.results:
            tag = r.severity.value.upper()
            lines.append(f"  [{tag}] {r.name}: {r.message}")
            for key, val in r.details.items():
                lines.append(f"        {key}: {val}")
        lines.append("")
        lines.append(f"Overall: {self.overall_severity.value}")
        lines.append(f"Should proceed: {self.should_proceed}")
        return "\n".join(lines)
