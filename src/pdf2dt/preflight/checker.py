"""Pre-flight checker — validates MinerU output before pipeline processing.

This module sits *before* Stage 0.  It inspects the inbox task directory,
runs a series of checks, and returns a structured :class:`PreFlightReport`.
The caller decides whether to proceed based on ``report.should_proceed``.

Checks performed:

1. **Structure** — meta.json exists, is valid JSON, matches schema; declared
   markdown and optional layout.json files are present.
2. **Content** — markdown is non-empty, has heading lines, has sufficient
   text content (not just whitespace or image markers).
3. **Image references** — every ``![](url)`` in markdown and every
   ``image_url`` in layout.json is resolvable: local files exist, remote
   URLs have a valid scheme.
4. **Layout consistency** — layout.json has the expected ``pages[]`` →
   ``blocks[]`` structure; image count roughly matches markdown references.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..inbox.loader import InboxLoader, ValidationError
from ..inbox.models import InboxTask, LoadedMinerU
from .report import CheckResult, CheckSeverity, PreFlightReport

_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)

# Thresholds
_MIN_TEXT_CHARS = 50
_MIN_HEADING_COUNT = 1


class PreFlightError(Exception):
    """Raised when the pre-flight check itself fails (not a check finding)."""

    pass


class PreFlightChecker:
    """Validates an inbox task directory before pipeline processing."""

    def __init__(self, inbox_root: Path | str) -> None:
        self._loader = InboxLoader(inbox_root)

    def check(self, task_dir: Path | str) -> PreFlightReport:
        """Run all pre-flight checks and return a report.

        Never raises for check failures — those go into the report as
        ERROR-severity results.  Only raises :class:`PreFlightError` if
        the checker itself cannot run (e.g. path does not exist).
        """
        task_dir = Path(task_dir)
        if not task_dir.is_dir():
            raise PreFlightError(f"Task directory does not exist: {task_dir}")

        task_id = task_dir.name
        report = PreFlightReport(task_id=task_id, task_dir=str(task_dir))

        # Phase 1 — structure
        task = self._check_structure(task_dir, report)
        if task is None:
            # Structure failed fatally; no point continuing.
            return report

        # Phase 2 — load content via InboxLoader
        loaded = self._load_content(task, report)
        if loaded is None:
            return report

        # Phase 3 — content health
        self._check_content(loaded, report)

        # Phase 4 — image reference reachability
        self._check_image_refs(task, loaded, report)

        # Phase 5 — layout consistency
        self._check_layout(loaded, report)

        return report

    # ------------------------------------------------------------------ #
    # Phase 1 — Structure
    # ------------------------------------------------------------------ #

    def _check_structure(
        self, task_dir: Path, report: PreFlightReport
    ) -> InboxTask | None:
        """Validate meta.json and declared product files."""
        meta_path = task_dir / "meta.json"
        if not meta_path.is_file():
            report.add(CheckResult(
                name="structure",
                severity=CheckSeverity.ERROR,
                message="meta.json not found",
            ))
            return None

        try:
            task = self._loader._validate_task_dir(task_dir)
        except ValidationError as exc:
            report.add(CheckResult(
                name="structure",
                severity=CheckSeverity.ERROR,
                message=f"meta.json validation failed: {exc}",
            ))
            return None

        # Check declared files actually exist
        missing: list[str] = []
        if task.meta.products.markdown:
            md_path = task_dir / task.meta.products.markdown
            if not md_path.is_file():
                missing.append(task.meta.products.markdown)
        for attr in ("layout_json", "html", "latex", "docx"):
            declared = getattr(task.meta.products, attr, None)
            if declared and not (task_dir / declared).is_file():
                missing.append(declared)

        if missing:
            report.add(CheckResult(
                name="structure",
                severity=CheckSeverity.ERROR,
                message=f"Declared product files missing: {', '.join(missing)}",
                details={"missing_files": missing},
            ))
            return None

        details: dict[str, Any] = {
            "task_id": task.meta.task_id,
            "markdown": task.meta.products.markdown,
            "layout_json": task.meta.products.layout_json,
            "has_source_pdf": task.source_pdf_path is not None,
            "has_images_dir": task.images_dir is not None,
        }
        report.add(CheckResult(
            name="structure",
            severity=CheckSeverity.OK,
            message="meta.json valid, all declared products present",
            details=details,
        ))
        return task

    # ------------------------------------------------------------------ #
    # Phase 2 — Load content
    # ------------------------------------------------------------------ #

    def _load_content(
        self, task: InboxTask, report: PreFlightReport
    ) -> LoadedMinerU | None:
        """Use InboxLoader to fully load markdown and layout data."""
        try:
            loaded = self._loader._load_task(task)
        except Exception as exc:
            report.add(CheckResult(
                name="content_load",
                severity=CheckSeverity.ERROR,
                message=f"Failed to load content: {exc}",
            ))
            return None

        report.add(CheckResult(
            name="content_load",
            severity=CheckSeverity.OK,
            message="Markdown and layout loaded successfully",
            details={
                "markdown_chars": len(loaded.markdown_text),
                "layout_pages": (
                    len(loaded.layout_data.get("pages", []))
                    if loaded.layout_data
                    else 0
                ),
                "image_refs_found": len(loaded.image_references),
            },
        ))
        return loaded

    # ------------------------------------------------------------------ #
    # Phase 3 — Content health
    # ------------------------------------------------------------------ #

    def _check_content(
        self, loaded: LoadedMinerU, report: PreFlightReport
    ) -> None:
        """Check that markdown has real, usable content."""
        md = loaded.markdown_text
        issues: list[str] = []

        # Non-empty
        stripped = md.strip()
        if not stripped:
            report.add(CheckResult(
                name="content",
                severity=CheckSeverity.ERROR,
                message="Markdown is empty or whitespace-only",
            ))
            return

        # Text content (strip image refs and whitespace)
        text_only = _MARKDOWN_IMAGE_RE.sub("", md).strip()
        text_chars = len(text_only)
        if text_chars < _MIN_TEXT_CHARS:
            issues.append(
                f"Very little text content ({text_chars} chars, "
                f"threshold {_MIN_TEXT_CHARS})"
            )

        # Headings
        headings = _HEADING_RE.findall(md)
        if len(headings) < _MIN_HEADING_COUNT:
            issues.append(
                f"No markdown headings found (expected >={_MIN_HEADING_COUNT})"
            )

        # Check for common OCR garbage patterns
        # (lots of single-char lines, excessive repetition)
        lines = [ln for ln in md.split("\n") if ln.strip()]
        if lines:
            short_lines = sum(1 for ln in lines if len(ln.strip()) <= 2)
            if len(lines) > 10 and short_lines / len(lines) > 0.6:
                issues.append(
                    f"High ratio of very short lines "
                    f"({short_lines}/{len(lines)}), possible OCR fragmentation"
                )

        if issues:
            report.add(CheckResult(
                name="content",
                severity=CheckSeverity.WARNING,
                message="; ".join(issues),
                details={
                    "text_chars": text_chars,
                    "heading_count": len(headings),
                    "total_lines": len(lines),
                },
            ))
        else:
            report.add(CheckResult(
                name="content",
                severity=CheckSeverity.OK,
                message="Content has sufficient text and structure",
                details={
                    "text_chars": text_chars,
                    "heading_count": len(headings),
                    "total_lines": len(lines),
                },
            ))

    # ------------------------------------------------------------------ #
    # Phase 4 — Image reference reachability
    # ------------------------------------------------------------------ #

    def _check_image_refs(
        self,
        task: InboxTask,
        loaded: LoadedMinerU,
        report: PreFlightReport,
    ) -> None:
        """Verify every image reference points to an accessible source."""
        refs = loaded.image_references
        if not refs:
            report.add(CheckResult(
                name="image_refs",
                severity=CheckSeverity.OK,
                message="No image references found (text-only document)",
            ))
            return

        unreachable: list[str] = []
        remote_count = 0
        local_count = 0
        resolved_count = 0

        for ref in refs:
            if ref.startswith(("http://", "https://")):
                remote_count += 1
                # Can't verify remote reachability without a network call;
                # just confirm the URL looks well-formed.
                if len(ref) < 10 or " " in ref:
                    unreachable.append(ref)
            elif ref.startswith("file://"):
                local_count += 1
                local_path = Path(ref[7:])
                # On Windows, file:///C:/... → strip file:/// → C:/...
                if not local_path.is_absolute():
                    local_path = Path(ref[8:]) if ref.startswith("file:///") else local_path
                if not local_path.is_file():
                    unreachable.append(ref)
            elif ref.startswith("images/"):
                local_count += 1
                local_path = task.task_dir / ref
                if local_path.is_file():
                    resolved_count += 1
                else:
                    unreachable.append(ref)
            else:
                unreachable.append(ref)

        if unreachable:
            report.add(CheckResult(
                name="image_refs",
                severity=CheckSeverity.ERROR,
                message=(
                    f"{len(unreachable)}/{len(refs)} image reference(s) "
                    "cannot be resolved"
                ),
                details={
                    "total_refs": len(refs),
                    "unreachable": unreachable,
                    "remote": remote_count,
                    "local": local_count,
                    "resolved_local": resolved_count,
                },
            ))
        else:
            report.add(CheckResult(
                name="image_refs",
                severity=CheckSeverity.OK,
                message=f"All {len(refs)} image reference(s) resolvable",
                details={
                    "total_refs": len(refs),
                    "remote": remote_count,
                    "local": local_count,
                    "resolved_local": resolved_count,
                },
            ))

    # ------------------------------------------------------------------ #
    # Phase 5 — Layout consistency
    # ------------------------------------------------------------------ #

    def _check_layout(
        self, loaded: LoadedMinerU, report: PreFlightReport
    ) -> None:
        """Check layout.json structure and cross-reference with markdown."""
        if loaded.layout_data is None:
            report.add(CheckResult(
                name="layout",
                severity=CheckSeverity.INFO,
                message="No layout.json — BookView will use markdown only",
            ))
            return

        layout = loaded.layout_data
        issues: list[str] = []
        details: dict[str, Any] = {}

        # Must have pages[]
        pages = layout.get("pages")
        if not isinstance(pages, list):
            issues.append("layout.json missing 'pages[]' array")
            report.add(CheckResult(
                name="layout",
                severity=CheckSeverity.ERROR,
                message="; ".join(issues),
            ))
            return

        details["page_count"] = len(pages)

        # Each page should have blocks[]
        pages_without_blocks = 0
        layout_image_urls: list[str] = []
        for page in pages:
            if not isinstance(page, dict):
                issues.append("Non-dict entry in pages[]")
                continue
            blocks = page.get("blocks")
            if not isinstance(blocks, list):
                pages_without_blocks += 1
                continue
            for block in blocks:
                if isinstance(block, dict):
                    img_url = block.get("image_url")
                    if isinstance(img_url, str):
                        layout_image_urls.append(img_url)

        if pages_without_blocks:
            issues.append(
                f"{pages_without_blocks} page(s) missing 'blocks[]'"
            )

        # Cross-reference: layout images vs markdown images
        md_images = set(loaded.image_references)
        layout_images = set(layout_image_urls)
        details["layout_image_count"] = len(layout_images)
        details["markdown_image_count"] = len(md_images)

        # Images in layout but not in markdown (not necessarily bad)
        layout_only = layout_images - md_images
        if layout_only:
            details["layout_only_images"] = len(layout_only)

        # Images in markdown but not in layout (may indicate layout gap)
        md_only = md_images - layout_images
        if md_only:
            details["markdown_only_images"] = len(md_only)

        if issues:
            report.add(CheckResult(
                name="layout",
                severity=CheckSeverity.WARNING,
                message="; ".join(issues),
                details=details,
            ))
        else:
            report.add(CheckResult(
                name="layout",
                severity=CheckSeverity.OK,
                message=(
                    f"layout.json valid: {len(pages)} page(s), "
                    f"{len(layout_images)} image(s)"
                ),
                details=details,
            ))


def check_task(task_dir: Path | str) -> PreFlightReport:
    """Convenience: run pre-flight checks on a single task directory."""
    return PreFlightChecker(Path(task_dir).parent).check(task_dir)
