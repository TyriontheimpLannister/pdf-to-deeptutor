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

Figure validation:

* Every figure referenced by an export plan is tracked as either
  successfully embedded or missing.
* :class:`RenderResult` records ``missing_figures`` and a
  ``validation_status`` (``ready`` / ``warning`` / ``blocked``).
* The ``export_manifest.json`` and ``project.json`` exports array
  both carry per-export validation status.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF

from ..project import ProjectWorkspace, StageStatus, record_stage, save_manifest
from ..review.figure_roles import (
    FigureRole,
    FigureRoleRecord,
    FigureRoleStore,
    effective_role_for_use,
)
from ..review.store import FigureRoleOverrideStore
from .planner import ExportPlan, ExportPlanCollection, ReorgMode

logger = logging.getLogger(__name__)

# Try to locate a Chinese system font on Windows.
_CANDIDATE_CJK_FONTS = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]

_INLINE_IMAGE_MARKER_RE = re.compile(r"!\[image\]\([^)]*\)")


def _without_inline_image_markers(text: str) -> str:
    """Remove image markers from body text before figures are rendered.

    Inline image references are materialized from ``asset_refs`` below the
    item body. Leaving the Markdown marker in the paragraph produces a
    literal ``![image](...)`` line in the exported PDF.
    """
    without_markers = _INLINE_IMAGE_MARKER_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", without_markers).strip()


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
        return re.sub(r"[^\u0000-\u007F\u00A0-\u024F]+", "?", text)


class ExportValidationStatus(str):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class RenderResult:
    output_path: Path
    plan_id: str
    item_count: int
    figure_count: int = 0
    """Number of figures successfully embedded."""
    missing_figures: list[str] = field(default_factory=list)
    """Figure IDs that were referenced but could not be embedded."""
    warnings: list[str] = field(default_factory=list)
    """Human-readable warnings generated during rendering."""
    geometry_blocked_figures: list[str] = field(default_factory=list)
    """Figure IDs that have unreviewed visual_inference/unknown relations."""
    dropped_figures: list[dict[str, Any]] = field(default_factory=list)
    """Figures skipped because of role=decor classification."""
    validation_status: str = ExportValidationStatus.READY
    plan_mode: str = ""

    def _compute_validation_status(self) -> str:
        # Geometry evidence rules: any unreviewed
        # ``visual_inference`` / ``unknown`` relation is a hard
        # block.  The relation is excluded from the PDF; the
        # block here prevents the user from uploading the export
        # thinking all relations are confirmed.
        if self.geometry_blocked_figures:
            return ExportValidationStatus.BLOCKED
        if not self.missing_figures:
            return ExportValidationStatus.READY
        # All figures missing → blocked; some missing → warning.
        if self.figure_count == 0:
            return ExportValidationStatus.BLOCKED
        return ExportValidationStatus.WARNING

    def finalise(self) -> None:
        """Compute and set validation_status based on figure results."""
        self.validation_status = self._compute_validation_status()


class PdfRenderer:
    """Render export plans to self-contained PDFs."""

    def __init__(self, workspace: ProjectWorkspace) -> None:
        self._workspace = workspace
        self._cjk_font = _find_cjk_font()
        self._assets_dir = workspace.root / "assets"
        self._assets_registry: dict[str, dict[str, Any]] = {}
        self._geometry_by_asset: dict[str, Any] = {}
        self._roles_by_id: dict[str, FigureRoleRecord] = {}
        self._roles_by_use: dict[tuple[str, str], FigureRoleRecord] = {}
        self._role_overrides_by_id: dict[str, FigureRoleRecord] = {}
        self._load_assets_registry()
        self._load_geometry()
        self._load_figure_roles()

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

        # Mode C bridges.
        for bridge in plan.bridges:
            pdf.write_paragraph("")
            pdf.write_paragraph(bridge.text)

        rendered_figures: set[str] = set()
        missing_figures: list[str] = []
        warnings: list[str] = []
        dropped_figures: list[dict[str, Any]] = []

        for item in plan.items:
            # Carry the plan_id so _render_item can attribute any
            # text-noise drops it makes back to the right plan.
            item_with_ctx = dict(item)
            item_with_ctx.setdefault("_plan_id", plan.plan_id)
            self._render_item(
                pdf, item_with_ctx, rendered_figures, missing_figures, warnings,
                dropped_figures,
            )

        pdf.output(str(out_path))

        result = RenderResult(
            output_path=out_path,
            plan_id=plan.plan_id,
            item_count=len(plan.items),
            figure_count=len(rendered_figures),
            missing_figures=list(missing_figures),
            warnings=warnings,
            plan_mode=plan.mode.value,
            dropped_figures=list(dropped_figures),
        )
        result.geometry_blocked_figures = self._geometry_blocked_for_plan(
            plan
        )
        result.finalise()

        if result.validation_status != ExportValidationStatus.READY:
            logger.warning(
                "Plan %s: %s (%d missing figures)",
                plan.plan_id,
                result.validation_status,
                len(result.missing_figures),
            )

        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_geometry(self) -> None:
        """Load the geometry queue produced by Stage 5.

        We index figures by ``asset_id`` so the figure renderer can
        quickly look up relations for the figure it just embedded.
        Missing or malformed files are silently ignored — the
        renderer must still produce a PDF for layouts that have no
        geometry content.
        """
        geo_path = self._workspace.review_dir / "geometry_figures.json"
        if not geo_path.is_file():
            return
        try:
            data = json.loads(geo_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("geometry_figures.json unreadable: %s", exc)
            return
        for fig in data.get("figures") or []:
            aid = fig.get("asset_id")
            if aid:
                self._geometry_by_asset[str(aid)] = fig

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

    def _load_figure_roles(self) -> None:
        """Load figure role classifications and user overrides.

        Missing or malformed files are silently ignored: the renderer
        must keep producing PDFs for projects that have no role
        annotation yet (e.g. legacy workspaces).
        """
        try:
            role_store = FigureRoleStore(self._workspace)
            self._roles_by_id = role_store.index()
            self._roles_by_use = role_store.index_by_use()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("figure_roles.json unreadable: %s", exc)
            self._roles_by_id = {}
            self._roles_by_use = {}
        try:
            overrides = FigureRoleOverrideStore(self._workspace).load()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("figure_role_overrides.json unreadable: %s", exc)
            overrides = []
        self._role_overrides_by_id = {}
        for d in overrides:
            try:
                role_enum = FigureRole(d.role)
            except ValueError:
                continue
            self._role_overrides_by_id[d.figure_id] = FigureRoleRecord(
                figure_id=d.figure_id,
                asset_id="",
                asset_sha256="",
                role=role_enum,
                reason=d.reviewer_note or "user_override",
                model_id="override",
                classified_at=d.applied_at,
            )

    def _render_item(
        self,
        pdf: _PdfDoc,
        item: dict[str, Any],
        rendered_figures: set[str],
        missing_figures: list[str],
        warnings: list[str],
        dropped_figures: list[dict[str, Any]],
    ) -> None:
        item_type = item.get("item_type") or "other"
        title = item.get("title") or ""
        text = item.get("text") or ""

        # Defence-in-depth: even after the matcher-side noise filter,
        # a stray watermark or page-number item can sneak through if
        # the heuristic was extended after a run was staged. Re-check
        # here so the renderer never produces a PDF containing a
        # noise item.
        from pdf2dt.outlining import classify_noise  # noqa: PLC0415
        noise = classify_noise(item)
        if noise.is_noise:
            dropped_figures.append(
                {
                    "plan_id": item.get("_plan_id", ""),
                    "item_id": item.get("item_id", ""),
                    "role": "text-noise",
                    "reason": f"renderer noise filter: {noise.reason}",
                    "asset_id": None,
                }
            )
            return

        pdf.write_heading(f"[{item_type}] {title}", level=3)

        body = _without_inline_image_markers(text)
        if body.startswith(title):
            body = body[len(title):].lstrip()
        if body:
            pdf.write_paragraph(body)

        page_refs = sorted(set(item.get("page_refs") or []))
        if page_refs:
            pdf.write_caption(f"Source pages: {', '.join(str(p) for p in page_refs)}")

        # Figures.
        for asset in item.get("asset_refs") or []:
            aid = asset.get("asset_id")
            if not aid or aid in rendered_figures:
                continue
            figure_id = str(asset.get("figure_id") or aid)
            outcome = self._render_figure(
                pdf,
                aid,
                asset.get("caption"),
                figure_id=figure_id,
                item_id=str(item.get("item_id") or ""),
            )
            if outcome == "rendered":
                rendered_figures.add(aid)
            elif outcome == "dropped":
                role = effective_role_for_use(
                    figure_id,
                    str(item.get("item_id") or ""),
                    self._roles_by_use,
                    self._roles_by_id,
                    self._role_overrides_by_id,
                )
                dropped_figures.append(
                    {
                        "asset_id": aid,
                        "figure_id": figure_id,
                        "role": role.role.value,
                        "confidence": role.confidence,
                        "reason": role.reason,
                    }
                )
            else:
                missing_figures.append(aid)
                warnings.append(f"Figure {aid} could not be embedded")

    def _render_figure(
        self,
        pdf: _PdfDoc,
        asset_id: str,
        caption: str | None,
        *,
        figure_id: str | None = None,
        item_id: str = "",
    ) -> str:
        """Try to embed the figure image into the PDF.

        Returns one of:

        * ``"rendered"`` — image was embedded.
        * ``"dropped"`` — image was skipped because its figure role
          is ``decor``.
        * ``"missing"`` — image could not be rendered (asset not
          found, render error, etc.).
        """
        # Figure role filter: skip embedding when the role is
        # explicitly ``decor``. ``content`` and ``ambiguous`` (the
        # default when no classification exists) both render.
        fid = figure_id or asset_id
        role = effective_role_for_use(
            fid,
            item_id,
            self._roles_by_use,
            self._roles_by_id,
            self._role_overrides_by_id,
        )
        if role.role == FigureRole.DECOR:
            logger.info("Figure %s dropped (role=decor)", asset_id)
            return "dropped"

        asset = self._assets_registry.get(asset_id)
        if asset is None:
            pdf.write_caption(f"[Figure {asset_id} — metadata not found]")
            logger.warning("Figure %s: metadata not found in registry", asset_id)
            return "missing"

        local_path = asset.get("local_path")
        if not local_path:
            pdf.write_caption(f"[Figure {asset_id} — no local path]")
            logger.warning("Figure %s: no local_path in registry", asset_id)
            return "missing"

        img_path = self._workspace.root / local_path
        if not img_path.is_file():
            alt_path = Path(local_path)
            if alt_path.is_file():
                img_path = alt_path
            else:
                pdf.write_caption(
                    f"[Figure {asset_id} — file not found: {local_path}]"
                )
                logger.warning(
                    "Figure %s: file not found at %s", asset_id, local_path
                )
                return "missing"

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
            logger.warning("Figure %s: render error: %s", asset_id, exc)
            return "missing"

        cap = caption or asset.get("caption") or f"Figure {asset_id}"
        if cap:
            pdf.write_caption(cap)

        # Geometry relations: only embed relations whose review state
        # is ``confirmed`` or ``corrected``.  Visual_inference and
        # unknown evidence must be excluded unless explicitly
        # reviewed — see
        # ``docs/decisions/2026-07-10-geometry-review-stages.md``.
        geo = self._geometry_by_asset.get(asset_id)
        if geo:
            self._render_geometry_relations(pdf, geo)
            self._render_geometry_description(pdf, geo)

        return "rendered"

    def _render_geometry_relations(
        self, pdf: _PdfDoc, geo: dict[str, Any]
    ) -> None:
        """Render geometry relations for an embedded figure.

        Only relations whose review state is ``confirmed`` or
        ``corrected`` are written into the PDF. ``visual_inference``
        and ``unknown`` evidence relations are skipped — see
        ``docs/decisions/2026-07-10-geometry-review-stages.md``.
        """
        for rel in geo.get("relations") or []:
            review_state = rel.get("review_state")
            if review_state not in ("confirmed", "corrected"):
                continue
            line = self._format_relation(rel)
            if line:
                pdf.write_caption(f"  • {line}")

    def _render_geometry_description(
        self, pdf: _PdfDoc, geo: dict[str, Any]
    ) -> None:
        """Render a deterministic natural-language figure description.

        The description is generated from the same
        ``include_only=confirmed/corrected`` set as the bullet
        list.  When no relations survive the filter the
        description is suppressed and the existing caption-only
        behaviour applies.  See
        ``docs/decisions/2026-07-10-figure-descriptions.md``.
        """
        from ..geometry import (
            GeometryFigure,
            describe_figure_block,
        )

        try:
            figure = GeometryFigure.from_dict(geo)
        except (KeyError, ValueError, TypeError):
            # Malformed queue entry — skip the description
            # rather than fail the whole export.  The bullet
            # list is unaffected.
            return
        block = describe_figure_block(figure)
        if not block:
            return
        # Use the same caption style as the bullet list so the
        # PDF stays visually consistent.  Italics would change
        # the font; we keep it as a plain caption.
        pdf.write_caption(f"  {block}")

    def _format_relation(self, rel: dict[str, Any]) -> str:
        rtype = rel.get("type") or "relates"
        entities = rel.get("entities") or []
        evidence = rel.get("evidence") or ""
        joined = ", ".join(str(e) for e in entities if e)
        if not joined:
            return ""
        return f"{rtype} ({evidence}): {joined}"

    def _geometry_blocked_for_plan(self, plan: ExportPlan) -> list[str]:
        """Return figure IDs in ``plan`` with unreviewed geometry relations.

        A figure is considered blocked when at least one of its
        relations has evidence ``visual_inference`` or ``unknown``
        and is not in a ``confirmed``/``corrected`` review state.
        """
        blocked: list[str] = []
        for item in plan.items:
            for asset in item.get("asset_refs") or []:
                aid = asset.get("asset_id")
                if not aid or aid in blocked:
                    continue
                geo = self._geometry_by_asset.get(aid)
                if not geo:
                    continue
                for rel in geo.get("relations") or []:
                    review_state = rel.get("review_state")
                    if review_state in ("confirmed", "corrected"):
                        continue
                    evidence = rel.get("evidence")
                    if evidence in ("visual_inference", "unknown"):
                        blocked.append(aid)
                        break
        return blocked

    def _record_stage(
        self,
        collection: ExportPlanCollection,
        results: list[RenderResult],
    ) -> None:
        # Build export_manifest.json with per-export validation.
        manifest_files: list[dict[str, Any]] = []

        for r in results:
            file_entry: dict[str, Any] = {
                "plan_id": r.plan_id,
                "path": str(r.output_path.relative_to(self._workspace.root)),
                "sha256": hashlib.sha256(r.output_path.read_bytes()).hexdigest(),
                "items": r.item_count,
                "figures": r.figure_count,
                "missing_figures": r.missing_figures,
                "validation_status": r.validation_status,
                "mode": r.plan_mode,
            }
            if r.warnings:
                file_entry["warnings"] = r.warnings
            manifest_files.append(file_entry)

        export_manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested_mode": collection.mode.value,
            "plans_rendered": len(results),
            "files": manifest_files,
        }
        manifest_path = self._workspace.exports_dir / "export_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(export_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Per-export dropped figure report: a flat list across all
        # plans, with plan_id so users can find what was omitted in
        # which output.
        all_drops: list[dict[str, Any]] = []
        for r in results:
            for drop in r.dropped_figures:
                all_drops.append({"plan_id": r.plan_id, **drop})
        drop_report_path = self._workspace.reports_dir / "dropped_figures.json"
        drop_report_path.parent.mkdir(parents=True, exist_ok=True)
        drop_report_path.write_text(
            json.dumps(
                {
                    "schema_version": "dropped_figures/v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "total_dropped": len(all_drops),
                    "drops": all_drops,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Determine overall stage metadata.
        metadata_extra: dict[str, Any] = {}
        blocked_exports = [
            r.plan_id for r in results
            if r.validation_status == ExportValidationStatus.BLOCKED
        ]
        warning_exports = [
            r.plan_id for r in results
            if r.validation_status == ExportValidationStatus.WARNING
        ]
        if blocked_exports:
            metadata_extra["blocked_exports"] = blocked_exports
        if warning_exports:
            metadata_extra["warning_exports"] = warning_exports

        fingerprints: list[str] = []
        for r in results:
            if r.output_path.is_file():
                fingerprints.append(
                    hashlib.sha256(r.output_path.read_bytes()).hexdigest()
                )

        # Record stage7 in the manifest.
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
                "exports_dir": str(
                    self._workspace.exports_dir.relative_to(self._workspace.root)
                ),
                "plans_rendered": len(results),
                "export_manifest": str(
                    manifest_path.relative_to(self._workspace.root)
                ),
                **metadata_extra,
            },
        )

        # Now update the project manifest's exports array.
        # This must happen AFTER record_stage() because record_stage()
        # re-reads from disk and would overwrite any prior changes.
        project_manifest = self._workspace.load_manifest()
        exports_entries = [
            {
                "export_id": r.plan_id,
                "path": str(r.output_path.relative_to(self._workspace.root)),
                "sha256": hashlib.sha256(r.output_path.read_bytes()).hexdigest(),
                "validation_status": r.validation_status,
                "mode": r.plan_mode,
            }
            for r in results
        ]
        project_manifest["exports"] = exports_entries
        save_manifest(self._workspace, project_manifest)


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
    mode_enum = (
        ReorgMode(str(mode_val))
        if not isinstance(mode_val, ReorgMode)
        else mode_val
    )
    collection = ExportPlanCollection(
        project_id=data.get("project_id", workspace.root.name),
        generated_at=data.get("generated_at", datetime.now(timezone.utc).isoformat()),
        mode=mode_enum,
        outline_used=data.get("outline_used"),
        plans=[ExportPlan.from_dict(plan) for plan in data.get("plans") or []],
    )

    # Clear stale PDFs left over from earlier runs whose plans were
    # dropped (Stage 4c dedup) or renumbered. Without this sweep
    # ``exports/deeptutor`` keeps growing PDF files that no plan
    # in the current ``plans.json`` will ever reference, which
    # surfaces in DeepTutor as duplicates / empty / off-topic
    # exports. Idempotent: removing a file that is about to be
    # re-rendered is a no-op for the renderer.
    planned_filenames = {plan.output_filename for plan in collection.plans}
    exports_dir = workspace.exports_dir
    stale: list[str] = []
    if exports_dir.is_dir():
        for pdf in exports_dir.glob("*.pdf"):
            if pdf.name not in planned_filenames:
                stale.append(pdf.name)
                with suppress(OSError):
                    pdf.unlink()

    renderer = PdfRenderer(workspace)
    return renderer.render_collection(collection)
