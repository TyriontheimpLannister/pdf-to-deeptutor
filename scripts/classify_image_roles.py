"""Classify figure roles for an existing project workspace.

Reads ``book_view/book_view.json``, runs the LLM-mediated
:class:`~pdf2dt.review.figure_roles.FigureRoleAnnotator` over every
figure-bound item, and writes ``review/figure_roles.json``.

Usage::

    python scripts/classify_image_roles.py \\
        --project-root projects/高思竞赛数学课本三年级 \\
        --provider minimax-m3 \\
        [--enable-template-decor-skip] \\
        [--max-images 50] \\
        [--cache-dir providers/vlm/figure_role_cache]

The script is idempotent: re-running it will use the on-disk cache
keyed by ``(asset_sha256, model_id, prompt_hash)`` and only call the
provider for figures not yet classified.

Provider credentials are read from environment variables:

* ``MINIMAX_API_KEY`` for ``--provider minimax-m3``
* ``SENSENOVA_*`` for ``--provider sensenova``
* ``MINIMAX_VLM_MODEL`` (optional) overrides the default model

No credential is ever echoed or persisted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make src/ importable without installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2dt.figure_roles.pre_filter_runtime import (  # noqa: E402
    run_prefilter_dry_run,
)
from pdf2dt.geometry.vlm import GeometryVlmProvider, VlmResponse  # noqa: E402
from pdf2dt.project import ProjectWorkspace, StageStatus, record_stage  # noqa: E402
from pdf2dt.review.figure_roles import (  # noqa: E402
    DECOR_CONTEXT_PATTERNS,
    FigureRole,
    classify_figure_roles,
    load_book_view,
)
from pdf2dt.review.template_decor import find_template_decor_assets  # noqa: E402

# Section banner titles. When the image's context contains one of
# these and no example / exercise math follows, the image is almost
# always a publisher template banner (cartoon mascot, star bar,
# "本讲知识汇总" decorative icon, etc.) and should be dropped.
#
# Note: 思考题 / 练习 / 作业 are *not* included here. Those section
# titles in this corpus contain real math problems whose figures
# are problem diagrams (e.g. 思考题 "如图, 有五个完全相同的骰子
# 摆成一排..."), so flagging them as banner would silently drop
# real content.
_SECTION_BANNER_TITLES = frozenset(
    {
        "本讲知识点汇总",
        "本讲例题汇总",
        "本讲知识",
        "本讲总结",
        "知识汇总",
    }
)


class MockFigureRoleProvider(GeometryVlmProvider):
    """Module-level mock so tests can import and assert on its behavior.

    The original mock lived inside ``_build_provider``; that made it
    unreachable from the test suite. Lifting it to module scope keeps
    the script-level ``_build_provider`` simple while letting tests
    drive the heuristic directly.

    The mock now consults the *image file* itself for a tiny
    feature extraction (size + aspect ratio) and parses the
    structured metadata block produced by ``_iter_figure_candidates``
    so it can recognise:
      * section-banner images (actual_section in banner set)
      * tiny decorative icons (area < 10000 px)
      * extreme aspect-ratio banners (ar > 5 or ar < 0.2)
      * **template decor clusters** — sets of images that look
        near-identical (dhash hamming <= 6) and appear 3+ times in
        the workspace. These are publisher template banners,
        cartoon icons, and section header strips that OCR
        re-encodes slightly on every page.
    Real VLM providers are unaffected because they ignore the
    metadata lines and look at the actual image bytes.

    Callers can pre-compute the template decor set by calling
    :func:`pdf2dt.review.template_decor.find_template_decor_assets`
    over the workspace's assets and passing the result to
    ``template_decor_ids`` (or via :meth:`set_template_decor_ids`).
    """

    name = "mock"
    model = "mock-figure-role"

    # Below this area (px²) a figure is almost always a 90x90 / 64x64
    # decorative icon, never a math problem diagram.
    _TINY_AREA_PX = 10_000
    _BANNER_AR_MAX = 5.0
    _BANNER_AR_MIN = 0.2

    def __init__(self, template_decor_ids: set[str] | None = None) -> None:
        self._template_decor_ids: set[str] = (
            template_decor_ids if template_decor_ids is not None else set()
        )

    def set_template_decor_ids(self, ids: set[str]) -> None:
        """Bulk-set the set of asset_ids that are template decorations.

        Replaces any previous value. The provider instance is
        stateful on purpose: ``classify_image_roles.py`` calls this
        once at startup after running
        :func:`find_template_decor_assets` over the workspace.
        """
        self._template_decor_ids = set(ids)

    def analyze_image(self, image_path, context):  # noqa: D401
        ctx = context or ""
        meta = self._parse_meta(ctx)
        section = meta.get("section", "")
        # ``actual_section`` is the heading the image sits under in
        # the source markdown, computed by
        # build_image_to_preceding_heading. It is more reliable than
        # ``section`` because the book view builder often hides the
        # image's true section behind a single chapter item.
        actual_section = meta.get("actual_section", "")

        # 1. Watermark phrase — strongest signal, always wins.
        if any(p in ctx for p in DECOR_CONTEXT_PATTERNS):
            return VlmResponse(
                raw_response=json.dumps(
                    {
                        "role": "decor",
                        "confidence": 0.95,
                        "reason": "mock: OCR watermark pattern in context",
                    }
                )
            )

        # 2. Section-banner image: the actual_section (set by
        # build_image_to_preceding_heading when the caller
        # provides it) is in the known banner set AND the
        # surrounding text is short (banner description only, no
        # real problem text). We prefer actual_section over the
        # item's own title because title is the chapter heading,
        # not the section heading.
        banner_source = actual_section or section
        if banner_source in _SECTION_BANNER_TITLES:
            body_chunk = self._body_chunk(ctx)
            if len(body_chunk) < 80:
                return VlmResponse(
                    raw_response=json.dumps(
                        {
                            "role": "decor",
                            "confidence": 0.86,
                            "reason": (
                                "mock: section banner image (heading "
                                f"{banner_source!r}, short body)"
                            ),
                        }
                    )
                )

        # 3. Visual feature fallback — actually open the image.
        # These are the most reliable signals we have without
        # looking at the image content: tiny icons and extreme
        # aspect-ratio banners are almost always decorative.
        #
        # 3a. Template-decor cluster check. If the caller pre-scanned
        # the workspace and identified a cluster of near-identical
        # images (dhash hamming <= 6, >= 3 members) that look like
        # a publisher template (练习/作业 banner, cartoon icon), and
        # this image is one of them, flag it decor. The asset_id is
        # recovered from the image file stem because the book view
        # asset_ids are derived from the file name.
        if image_path is not None:
            try:
                asset_id = Path(image_path).stem
            except (TypeError, ValueError):
                asset_id = ""
            else:
                if asset_id and asset_id in self._template_decor_ids:
                    return VlmResponse(
                        raw_response=json.dumps(
                            {
                                "role": "decor",
                                "confidence": 0.84,
                                "reason": (
                                    "mock: template-decor cluster match "
                                    f"(asset_id={asset_id!r})"
                                ),
                            }
                        )
                    )

        vfeat = self._visual_features(image_path)
        if vfeat is not None:
            width, height, area = vfeat
            if area < self._TINY_AREA_PX:
                return VlmResponse(
                    raw_response=json.dumps(
                        {
                            "role": "decor",
                            "confidence": 0.78,
                            "reason": (
                                f"mock: tiny decorative icon "
                                f"({width}x{height}, area={area}px²)"
                            ),
                        }
                    )
                )
            ar = width / max(height, 1)
            if ar > self._BANNER_AR_MAX or ar < self._BANNER_AR_MIN:
                return VlmResponse(
                    raw_response=json.dumps(
                        {
                            "role": "decor",
                            "confidence": 0.82,
                            "reason": (
                                f"mock: extreme aspect-ratio banner "
                                f"({width}x{height}, ar={ar:.2f})"
                            ),
                        }
                    )
                )

        # 4. Default — accept as content.
        return VlmResponse(
            raw_response=json.dumps(
                {
                    "role": "content",
                    "confidence": 0.6,
                    "reason": "mock: default content",
                }
            )
        )

    @staticmethod
    def _parse_meta(ctx: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in ctx.splitlines():
            line = line.strip()
            if line.startswith("[item_type:") and line.endswith("]"):
                out["item_type"] = line[len("[item_type:") : -1]
            elif line.startswith("[chapter:") and line.endswith("]"):
                out["chapter"] = line[len("[chapter:") : -1]
            elif line.startswith("[section:") and line.endswith("]"):
                out["section"] = line[len("[section:") : -1]
            elif line.startswith("[actual_section:") and line.endswith("]"):
                out["actual_section"] = line[len("[actual_section:") : -1]
            elif line.startswith("[title:") and line.endswith("]"):
                out["title"] = line[len("[title:") : -1]
        if "title" not in out:
            # Title is the first non-meta, non-empty line.
            for line in ctx.splitlines():
                line = line.strip()
                if not line or line.startswith("["):
                    continue
                out["title"] = line
                break
        return out

    @staticmethod
    def _body_chunk(ctx: str) -> str:
        """Return text *after* the metadata block — i.e. the
        title + surrounding markdown body without [item_type:..]
        markers.
        """
        lines = ctx.splitlines()
        body = []
        seen_meta_end = False
        for line in lines:
            if not seen_meta_end:
                if line.strip().startswith("["):
                    continue
                seen_meta_end = True
            body.append(line)
        return "\n".join(body).strip()

    @staticmethod
    def _visual_features(image_path) -> tuple[int, int, int] | None:
        try:
            from PIL import Image
            with Image.open(image_path) as im:
                w, h = im.size
            return (w, h, w * h)
        except Exception:
            return None


def _build_provider(name: str, workspace: ProjectWorkspace | None = None):
    if name == "minimax-m3":
        from pdf2dt.geometry.vlm import MiniMaxM3Provider
        return MiniMaxM3Provider()
    if name == "sensenova":
        from pdf2dt.geometry.vlm import SenseNovaProvider
        return SenseNovaProvider()
    if name == "mock":
        provider = MockFigureRoleProvider()
        if workspace is not None:
            # Pre-scan the workspace's asset files and identify
            # publisher-template clusters (练习 / 作业 banners,
            # cartoon icons, etc.). Three or more near-identical
            # images are almost certainly a repeated template —
            # the mock has no VLM, so this is the best signal it
            # can produce for a banner-style decoration.
            from pdf2dt.review.figure_roles import load_assets_registry  # noqa: PLC0415

            registry = load_assets_registry(workspace)
            asset_paths: list[tuple[str, Path]] = []
            project_root = workspace.root
            for aid, entry in registry.items():
                rel = entry.get("local_path", "")
                p = project_root / rel
                if not p.is_file():
                    p = project_root / "assets" / f"{aid}.jpg"
                asset_paths.append((aid, p))
            template_ids = find_template_decor_assets(asset_paths)
            provider.set_template_decor_ids(template_ids)
            print(
                f"mock: identified {len(template_ids)} template-decor assets "
                f"across {len(asset_paths)} total"
            )
        return provider
    raise SystemExit(f"unknown provider: {name}")


def _distribution(roles: list) -> dict[str, int]:
    counts: dict[str, int] = {
        FigureRole.CONTENT.value: 0,
        FigureRole.DECOR.value: 0,
        FigureRole.AMBIGUOUS.value: 0,
    }
    for r in roles:
        counts[r.role.value] = counts.get(r.role.value, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, required=True)
    ap.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "minimax-m3", "sensenova"],
        help="VLM provider to use. Default 'mock' is offline.",
    )
    ap.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Process at most this many figures (smoke / cost control).",
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override the default cache directory.",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable on-disk cache for this run (forces provider calls).",
    )
    ap.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Bound concurrent provider calls (default: 1).",
    )
    ap.add_argument(
        "--dry-run-prefilter",
        action="store_true",
        help="Run the provider-free Phase 1.5 pre-filter dry-run.",
    )
    ap.add_argument(
        "--enable-template-decor-skip",
        action="store_true",
        help=(
            "Skip VLM calls for reviewed repeated-template decorations; "
            "all other pre-filter rules remain disabled."
        ),
    )
    ap.add_argument(
        "--export-scoped",
        action="store_true",
        help=(
            "Classify only figure/item uses in current export plans, preserving "
            "human overrides and unrelated prior audit records."
        ),
    )
    args = ap.parse_args()

    ws = ProjectWorkspace(args.project_root)
    if args.dry_run_prefilter:
        try:
            report = run_prefilter_dry_run(ws)
        except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(
            f"wrote {ws.reports_dir / 'pre_filter_dry_run.json'} "
            f"({report['candidates_total']} candidates, "
            f"{report['total_unique_decor']} projected decor skips)"
        )
        return 0

    provider = _build_provider(args.provider, workspace=ws)
    cache_dir = args.cache_dir
    cache_enabled = not args.no_cache

    book_view = load_book_view(ws)
    from pdf2dt.review.figure_roles import _iter_figure_candidates
    candidates = sum(1 for _ in _iter_figure_candidates(book_view))

    if candidates == 0:
        print(
            f"no figure-bound items in {args.project_root / 'book_view' / 'book_view.json'}",
            file=sys.stderr,
        )
        return 1

    roles = classify_figure_roles(
        ws,
        provider=provider,
        cache_dir=cache_dir,
        cache_enabled=cache_enabled,
        max_images=args.max_images,
        enable_template_decor_skip=args.enable_template_decor_skip,
        export_scoped=args.export_scoped,
        progress_stream=sys.stderr,
        max_concurrency=args.max_concurrency,
    )

    dist = _distribution(roles)
    print(f"classified {len(roles)} of {candidates} figures (max_images={args.max_images})")
    print("distribution:")
    for k in (FigureRole.DECOR.value, FigureRole.AMBIGUOUS.value, FigureRole.CONTENT.value):
        pct = (dist[k] / len(roles) * 100.0) if roles else 0.0
        print(f"  {k:<10s} {dist[k]:>5d}  ({pct:5.1f}%)")

    record_stage(
        ws,
        "stage5_figure_roles",
        status=StageStatus.COMPLETED,
        metadata={
            "provider": getattr(provider, "name", args.provider),
            "model": getattr(provider, "model", ""),
            "max_images": args.max_images,
            "export_scoped": args.export_scoped,
            "candidates": candidates,
            "classified": len(roles),
            "distribution": dist,
            "cache_dir": str(cache_dir) if cache_dir else str(
                args.project_root / "providers" / "vlm" / "figure_role_cache"
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
