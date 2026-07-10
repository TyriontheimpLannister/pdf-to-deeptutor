"""Stage 7 PDF renderer.

Renders :class:`ExportPlan` documents into self-contained PDFs using
``fpdf2``. Each plan becomes one PDF file in ``exports/deeptutor/``.

The renderer is designed to work offline: all images are read from the
project's local ``assets/`` directory, and no remote URLs are fetched.

Font handling:

* On Windows, the renderer tries ``msyh.ttc`` (Microsoft YaHei) and
  ``simhei.ttf`` (SimHei) for Chinese text. If neither is available it
  falls back to fpdf2's bundled ``DejaVuSans`` and warns that CJK
  characters may be rendered as placeholders.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF

from ..project import ProjectWorkspace, StageStatus, record_stage
from .planner import ExportPlan, ExportPlanCollection, ReorgMode


# Try to locate a Chinese system font on Windows.
_CANDIDATE_CJK_FONTS = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]


def _find_cjk_font() -> Path | None:
    for p in _CANDIDATE_CJK_FONTS:
        if p.is_file():
            return p
    return None


class _PdfDoc(FPDF):
    """Small wrapper that auto-adds a page header and footer."""

    def __init__(
        self,
        title: str,
        project_id: str,
        cjk_font_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._doc_title = title
        self._project_id = project_id
        self._cjk_font_path = cjk_font_path
        self._use_cjk = cjk_font_path is not None
        self._setup_fonts()
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()
        self.set_title(title)

    def _setup_fonts(self) -> None:
        # Body font. ``uni=True`` was the historical way to mark a font
        # as supporting the full Unicode range in fpdf2 < 2.5.1; from
        # 2.5.1 onwards the parameter is deprecated and any TrueType /
        # OpenType font is treated as Unicode-aware by default.
        if self._use_cjk:
            self.add_font("uni", "", str(self._cjk_font_path))
            self.add_font("uni", "B", str(self._cjk_font_path))
            self.set_font("uni", "", 12)
        else:
            self.add_font("DejaVu", "", "DejaVuSans.ttf")
            self.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf")
            self.set_font("DejaVu", "", 12)

    def _set_body(self, bold: bool = False, size: int = 12) -> None:
        family = "uni" if self._use_cjk else "DejaVu"
        self.set_font(family, "B" if bold else "", size)

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self._set_body(size=9)
        self.cell(0, 10, self._doc_title, border=0, align="L")
        self.ln(5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-15)
        self._set_body(size=9)
        self.cell(0, 10, f"{self._project_id}  —  {self.page_no()}", align="C")

    def write_heading(self, text: str, level: int = 1) -> None:
        size = {1: 18, 2: 14, 3: 12}.get(level, 12)
        self._set_body(bold=True, size=size)
        self.multi_cell(0, 8, self._safe(text))
        self.ln(2)

    def write_paragraph(self, text: str) -> None:
        self._set_body(size=12)
        self.multi_cell(0, 6, self._safe(text))
        self.ln(2)

    def write_caption(self, text: str) -> None:
        self._set_body(size=10)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 5, self._safe(text))
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def _safe(self, text: str) -> str:
        if self._use_cjk:
            return text
        # Fall-back: strip characters that DejaVu cannot render so the
        # PDF still generates without fpdf2 raising UnicodeEncodeError.
        return re.sub(r"[^\u0000-\u007F\u00A0-\u024F]+", "?", text)


@dataclass
class RenderResult:
    output_path: Path
    plan_id: str
    item_count: int
    figure_count: int


class PdfRenderer:
    """Render export plans to self-contained PDFs."""

    def __init__(self, workspace: ProjectWorkspace) -> None:
        self._workspace = workspace
        self._cjk_font = _find_cjk_font()
        self._assets_dir = workspace.root / "assets"
        self._assets_registry: dict[str, dict[str, Any]] = {}
        self._load_assets_registry()

    def render_collection(self, collection: ExportPlanCollection) -> list[RenderResult]:
        results: list[RenderResult] = []
        for plan in collection.plans:
            result = self.render_plan(plan)
            results.append(result)
        self._record_stage(collection, results)
        return results

    def render_plan(self, plan: ExportPlan) -> RenderResult:
        out_path = self._workspace.exports_dir / plan.output_filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        pdf = _PdfDoc(
            title=plan.title,
            project_id=self._workspace.root.name,
            cjk_font_path=self._cjk_font,
        )

        # Cover.
        pdf.write_heading(plan.title, level=1)
        pdf.write_paragraph(f"Project: {self._workspace.root.name}")
        if plan.outline_used:
            pdf.write_paragraph(
                f"Outline: {plan.outline_used.get('outline_id')} "
                f"v{plan.outline_used.get('version')}"
            )
        pdf.write_paragraph(f"Mode: {plan.mode.value}")
        pdf.write_paragraph(f"Items: {len(plan.items)}")
        if plan.is_misc_fallback:
            pdf.write_paragraph(
                f"This is the fallback export for {plan.unclassified_count} "
                "unclassified items."
            )
        pdf.ln(5)

        # Mode C: render every bridge that the planner attached to
        # this plan, in order, at the top of the plan. The first
        # plan in a collection has no predecessors so its bridge list
        # is empty; subsequent plans have exactly one bridge each.
        for bridge in plan.bridges:
            pdf.write_paragraph("")
            pdf.write_paragraph(bridge.text)

        rendered_figures: set[str] = set()
        for item in plan.items:
            self._render_item(pdf, item, rendered_figures)

        pdf.output(str(out_path))
        return RenderResult(
            output_path=out_path,
            plan_id=plan.plan_id,
            item_count=len(plan.items),
            figure_count=len(rendered_figures),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_assets_registry(self) -> None:
        reg_path = self._workspace.normalized_dir / "assets_registry.json"
        if not reg_path.is_file():
            return
        try:
            data = json.loads(reg_path.read_text(encoding="utf-8"))
            for asset in data.get("assets") or []:
                aid = asset.get("asset_id")
                if aid:
                    self._assets_registry[str(aid)] = asset
        except (json.JSONDecodeError, OSError):
            pass

    def _render_item(
        self,
        pdf: _PdfDoc,
        item: dict[str, Any],
        rendered_figures: set[str],
    ) -> None:
        item_type = item.get("item_type") or "other"
        title = item.get("title") or ""
        text = item.get("text") or ""

        pdf.write_heading(f"[{item_type}] {title}", level=3)

        # Body text (skip the title if it is duplicated at the start).
        body = text
        if body.startswith(title):
            body = body[len(title):].lstrip()
        if body:
            pdf.write_paragraph(body)

        # Page refs.
        page_refs = sorted(set(item.get("page_refs") or []))
        if page_refs:
            pdf.write_caption(f"Source pages: {', '.join(str(p) for p in page_refs)}")

        # Figures.
        for asset in item.get("asset_refs") or []:
            aid = asset.get("asset_id")
            if not aid or aid in rendered_figures:
                continue
            rendered_figures.add(aid)
            self._render_figure(pdf, aid, asset.get("caption"))

    def _render_figure(self, pdf: _PdfDoc, asset_id: str, caption: str | None) -> None:
        asset = self._assets_registry.get(asset_id)
        if asset is None:
            pdf.write_caption(f"[Figure {asset_id} — metadata not found]")
            return

        local_path = asset.get("local_path")
        if not local_path:
            pdf.write_caption(f"[Figure {asset_id} — no local path]")
            return

        img_path = self._workspace.root / local_path
        if not img_path.is_file():
            # Some registries store paths relative to the workspace root
            # with backslashes; try a direct join as a fallback.
            alt_path = Path(local_path)
            if alt_path.is_file():
                img_path = alt_path
            else:
                pdf.write_caption(f"[Figure {asset_id} — file not found: {local_path}]")
                return

        # Scale to fit page width (190 mm) preserving aspect ratio.
        width_mm = 190
        try:
            from PIL import Image as PILImage
            with PILImage.open(img_path) as pil_img:
                orig_w, orig_h = pil_img.size
        except Exception:
            orig_w = orig_h = None

        if orig_w and orig_h:
            aspect = orig_h / orig_w
            height_mm = width_mm * aspect
            max_height = 250
            if height_mm > max_height:
                scale = max_height / height_mm
                width_mm *= scale
                height_mm *= scale
        else:
            height_mm = 120

        if pdf.get_y() + height_mm > 270:
            pdf.add_page()
        try:
            pdf.image(str(img_path), x=10, y=pdf.get_y(), w=width_mm)
            pdf.set_y(pdf.get_y() + height_mm + 3)
        except Exception as exc:
            pdf.write_caption(f"[Figure {asset_id} — render error: {exc}]")
            return

        cap = caption or asset.get("caption") or f"Figure {asset_id}"
        if cap:
            pdf.write_caption(cap)

    def _record_stage(
        self,
        collection: ExportPlanCollection,
        results: list[RenderResult],
    ) -> None:
        export_manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": collection.mode.value,
            "plans_rendered": len(results),
            "files": [
                {
                    "plan_id": r.plan_id,
                    "path": str(r.output_path.relative_to(self._workspace.root)),
                    "sha256": hashlib.sha256(r.output_path.read_bytes()).hexdigest(),
                    "items": r.item_count,
                    "figures": r.figure_count,
                }
                for r in results
            ],
        }
        manifest_path = self._workspace.exports_dir / "export_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(export_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        fingerprints: list[str] = []
        for r in results:
            if r.output_path.is_file():
                fingerprints.append(hashlib.sha256(r.output_path.read_bytes()).hexdigest())
        record_stage(
            self._workspace,
            "stage7_export",
            status=StageStatus.COMPLETED,
            input_fingerprint=hashlib.sha256(
                (self._workspace.export_plans_dir / "plans.json").read_bytes()
            ).hexdigest(),
            output_fingerprint=hashlib.sha256(
                json.dumps(fingerprints, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if fingerprints
            else "",
            metadata={
                "exports_dir": str(self._workspace.exports_dir.relative_to(self._workspace.root)),
                "plans_rendered": len(results),
                "export_manifest": str(manifest_path.relative_to(self._workspace.root)),
            },
        )


def render_exports(
    workspace: ProjectWorkspace,
    *,
    plans_path: Path | str | None = None,
) -> list[RenderResult]:
    """Run Stage 7: render all plans in ``export_plans/plans.json``."""
    p = Path(plans_path) if plans_path else (workspace.export_plans_dir / "plans.json")
    if not p.is_file():
        raise FileNotFoundError(f"export plans not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    mode_val = data.get("mode", "B")
    mode_enum = ReorgMode(str(mode_val)) if not isinstance(mode_val, ReorgMode) else mode_val
    collection = ExportPlanCollection(
        project_id=data.get("project_id", workspace.root.name),
        generated_at=data.get("generated_at", datetime.now(timezone.utc).isoformat()),
        mode=mode_enum,
        outline_used=data.get("outline_used"),
        plans=[ExportPlan.from_dict(plan) for plan in data.get("plans") or []],
    )
    renderer = PdfRenderer(workspace)
    return renderer.render_collection(collection)
