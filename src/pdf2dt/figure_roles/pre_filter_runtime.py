"""Workspace adapter for the Phase 1.5 pre-filter dry-run.

The rule engine remains in :mod:`pre_filter`; this module only translates
the existing workspace representation into candidates and persists the
reviewable, provider-free report.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from ..project import ProjectWorkspace
from ..review.figure_roles import (
    _iter_figure_candidates,
    build_image_to_local_contexts,
    build_image_to_preceding_heading,
    load_assets_registry,
    load_book_view,
    resolve_asset_path,
)
from ..review.template_decor import find_template_decor_assets
from .pre_filter import (
    Candidate,
    PreFilterConfig,
    PreFilterRunResult,
    RuleStats,
    compute_asset_content_hash,
    run_pre_filter,
)

REPORT_FILENAME = "pre_filter_dry_run.json"


def _context_hash(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()


def build_prefilter_candidates(workspace: ProjectWorkspace) -> tuple[list[Candidate], bool]:
    """Build deterministic candidates and return ``(candidates, has_registry)``.

    Missing or unreadable assets are represented by conservative placeholder
    metadata. The caller disables rules for that legacy fallback, so absence
    of an asset registry can never turn into an automatic ``decor`` decision.
    """
    book_view = load_book_view(workspace)
    registry = load_assets_registry(workspace)
    headings = build_image_to_preceding_heading(workspace)
    local_contexts = build_image_to_local_contexts(workspace)
    candidates: list[Candidate] = []
    for figure in _iter_figure_candidates(book_view, headings, local_contexts):
        asset_path = resolve_asset_path(workspace, registry, figure.asset_id)
        if asset_path is None:
            width, height = 400, 300
            content_hash = f"missing:{figure.asset_id}"
        else:
            try:
                with Image.open(asset_path) as image:
                    width, height = image.size
                content_hash = compute_asset_content_hash(asset_path)
            except (OSError, ValueError):
                width, height = 400, 300
                content_hash = f"unreadable:{figure.asset_id}"
        candidates.append(
            Candidate(
                asset_id=figure.asset_id,
                item_id=figure.item_id or figure.figure_id,
                context_hash=_context_hash(figure.local_context),
                context_text=figure.local_context,
                width=width,
                height=height,
                asset_content_hash=content_hash,
            )
        )
    return candidates, bool(registry)


def _rule_stats_payload(stats: dict[str, RuleStats]) -> dict[str, dict[str, Any]]:
    return {rule_id: stats[rule_id].to_dict() for rule_id in sorted(stats)}


def _candidate_keys(result: PreFilterRunResult) -> list[dict[str, str]]:
    return [
        {
            "asset_id": key[0],
            "item_id": key[1],
            "context_hash": key[2],
            "rule_ids": sorted(rule_ids),
        }
        for key, rule_ids in result.candidate_fired_rule_ids
    ]


def build_template_decor_audit(
    workspace: ProjectWorkspace,
    candidates: list[Candidate],
) -> dict[str, Any]:
    """Compute template-decor coverage without affecting pre-filter savings.

    This audit is intentionally separate from ``PreFilterRunResult``. The
    detector is a promising high-precision signal, but it is not approved to
    emit provider skips until its report has been manually reviewed.
    """
    registry = load_assets_registry(workspace)
    if not registry:
        return {
            "status": "unavailable",
            "rule_id": "template_decor",
            "reason": "assets_registry_missing_or_unreadable",
            "affects_projected_savings": False,
            "approved_for_provider_skip": False,
        }

    asset_paths = [
        (asset_id, path)
        for asset_id in sorted(registry)
        if (path := resolve_asset_path(workspace, registry, asset_id)) is not None
    ]
    matched_asset_ids = sorted(find_template_decor_assets(asset_paths))
    matched_keys = sorted(
        {
            (candidate.asset_id, candidate.item_id, candidate.context_hash)
            for candidate in candidates
            if candidate.asset_id in set(matched_asset_ids)
        }
    )
    total = len(candidates)
    projected = len(matched_keys)
    return {
        "status": "computed",
        "rule_id": "template_decor",
        "detector": "find_template_decor_assets",
        "assets_total": len(asset_paths),
        "matched_asset_count": len(matched_asset_ids),
        "matched_asset_ids": matched_asset_ids,
        "candidate_hits": projected,
        "projected_saved_calls": projected,
        "projected_savings_pct": (100.0 * projected / total) if total else 0.0,
        "affects_projected_savings": False,
        "approved_for_provider_skip": False,
    }


def build_prefilter_report(
    result: PreFilterRunResult,
    *,
    config: PreFilterConfig | None = None,
    cluster_audit: dict[str, Any] | None = None,
    template_decor_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a result without adding provider or visual-inference claims."""
    config = config or PreFilterConfig.default()
    now = result.candidates_total
    saved = result.projected_saved_calls
    after = now - saved
    unique = _candidate_keys(result)
    audit = dict(cluster_audit or {"status": "not_applied"})
    audit["affects_projected_savings"] = False
    template_audit = dict(
        template_decor_audit
        or {
            "status": "not_applied",
            "rule_id": "template_decor",
            "affects_projected_savings": False,
            "approved_for_provider_skip": False,
        }
    )
    return {
        "schema_version": result.schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs_hash": result.inputs_hash,
        "rules_config_hash": result.rules_config_hash,
        "config_snapshot": {
            "schema_version": config.schema_version,
            "rules_enabled": list(config.enabled_rules),
            "thresholds": {
                rule.rule_id: dict(rule.threshold) for rule in config.rules
            },
        },
        "candidates_total": now,
        "rule_stats": _rule_stats_payload(result.rule_stats),
        "unique_decor_tuples": unique,
        "total_unique_decor": saved,
        "projected_vlm_calls_now": now,
        "projected_vlm_calls_after": after,
        "total_vlm_calls_now": now,
        "projected_savings_pct": (100.0 * saved / now) if now else 0.0,
        "cluster_audit": audit,
        "template_decor_audit": template_audit,
        "provider_calls": 0,
        "notes": {
            "real_corpus": "",
            "approved_rules": [],
            "dry_run_only": True,
            "ground_truth": "unverified",
        },
    }


def run_prefilter_dry_run(
    workspace: ProjectWorkspace,
    *,
    cluster_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the safe dry-run and refuse to overwrite an existing report."""
    report_path = workspace.reports_dir / REPORT_FILENAME
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {report_path}")

    candidates, has_registry = build_prefilter_candidates(workspace)
    config = PreFilterConfig.default() if has_registry else PreFilterConfig()
    result = run_pre_filter(candidates, config)
    template_audit = build_template_decor_audit(workspace, candidates)
    report = build_prefilter_report(
        result,
        config=config,
        cluster_audit=cluster_audit,
        template_decor_audit=template_audit,
    )
    report["notes"]["real_corpus"] = workspace.root.name

    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        if report_path.exists():
            raise FileExistsError(
                f"refusing to overwrite existing report: {report_path}"
            )
        temporary.replace(report_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return report


__all__ = [
    "REPORT_FILENAME",
    "build_prefilter_candidates",
    "build_prefilter_report",
    "build_template_decor_audit",
    "run_prefilter_dry_run",
]
