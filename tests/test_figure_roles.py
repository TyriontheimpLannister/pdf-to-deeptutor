"""Tests for the figure role classifier and override store."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from pdf2dt.geometry.vlm import VlmResponse
from pdf2dt.project import ProjectWorkspace
from pdf2dt.review import (
    PromotionError,
    apply_figure_role_overrides,
)
from pdf2dt.review.figure_roles import (
    FigureRole,
    FigureRoleAnnotator,
    FigureRoleRecord,
    FigureRoleStore,
    _iter_figure_candidates,
    classify_figure_roles,
    effective_role,
    effective_role_for_use,
)
from pdf2dt.review.store import FigureRoleDecision, FigureRoleOverrideStore

# ---------------------------------------------------------------------- #
# Round-trip and enum safety
# ---------------------------------------------------------------------- #


def test_figure_role_record_round_trip() -> None:
    rec = FigureRoleRecord(
        figure_id="fig-1",
        asset_id="asset-1",
        asset_sha256="abc",
        role=FigureRole.DECOR,
        confidence=0.9,
        reason="cartoon panda",
        model_id="mock",
        request_id="req-1",
        classified_at="2026-07-13T00:00:00Z",
    )
    payload = rec.to_dict()
    assert payload["role"] == "decor"
    restored = FigureRoleRecord.from_dict(payload)
    assert restored.role is FigureRole.DECOR
    assert restored.confidence == pytest.approx(0.9)
    assert restored.reason == "cartoon panda"
    assert restored.prefilter_skipped is False


def test_figure_role_record_unknown_role_falls_back_to_ambiguous() -> None:
    rec = FigureRoleRecord.from_dict(
        {"figure_id": "f", "asset_id": "a", "role": "BOGUS", "confidence": "nope"}
    )
    assert rec.role is FigureRole.AMBIGUOUS
    assert rec.confidence == 0.0


def test_iter_candidates_matches_image_marker_after_path_normalization() -> None:
    book_view = {
        "items": [
            {
                "item_id": "item-1",
                "text": "before\n![image](assets/asset-1.jpg)\nafter",
                "asset_refs": [
                    {
                        "asset_id": "asset-1",
                        "local_path": r"assets\asset-1.jpg",
                    }
                ],
            }
        ]
    }

    candidate = next(iter(_iter_figure_candidates(book_view)))

    assert "before" in candidate.local_context
    assert "after" in candidate.local_context


def test_iter_candidates_falls_back_to_asset_filename() -> None:
    book_view = {
        "items": [
            {
                "item_id": "item-1",
                "text": "before\n![image](assets/asset-1.jpg)\nafter",
                "asset_refs": [
                    {
                        "asset_id": "asset-1",
                        "local_path": r"projects\book\assets\asset-1.jpg",
                    }
                ],
            }
        ]
    }

    candidate = next(iter(_iter_figure_candidates(book_view)))

    assert "before" in candidate.local_context
    assert "after" in candidate.local_context


def test_effective_role_precedence() -> None:
    base = {
        "fig-1": FigureRoleRecord(
            figure_id="fig-1",
            asset_id="a",
            asset_sha256="",
            role=FigureRole.CONTENT,
            reason="base",
        )
    }
    overrides = {
        "fig-1": FigureRoleRecord(
            figure_id="fig-1",
            asset_id="a",
            asset_sha256="",
            role=FigureRole.DECOR,
            reason="user",
        )
    }
    # Override wins over base.
    assert effective_role("fig-1", base, overrides).role is FigureRole.DECOR
    # No override → base.
    assert effective_role("fig-1", base).role is FigureRole.CONTENT
    # Nothing → ambiguous.
    miss = effective_role("fig-missing", {})
    assert miss.role is FigureRole.AMBIGUOUS
    assert miss.reason == "no_record"


def test_effective_role_prefers_item_specific_record() -> None:
    global_record = FigureRoleRecord(
        figure_id="fig-1",
        asset_id="asset-1",
        asset_sha256="sha",
        role=FigureRole.CONTENT,
    )
    decor_use = FigureRoleRecord(
        figure_id="fig-1",
        asset_id="asset-1",
        asset_sha256="sha",
        role=FigureRole.DECOR,
        item_id="item-banner",
    )
    assert (
        effective_role_for_use(
            "fig-1",
            "item-banner",
            {("fig-1", "item-banner"): decor_use},
            {"fig-1": global_record},
        ).role
        is FigureRole.DECOR
    )
    assert (
        effective_role_for_use(
            "fig-1", "item-problem", {}, {"fig-1": global_record}
        ).role
        is FigureRole.CONTENT
    )
# ---------------------------------------------------------------------- #
# FigureRoleStore persistence
# ---------------------------------------------------------------------- #


def test_figure_role_store_round_trip(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    ws.review_dir.mkdir(parents=True, exist_ok=True)
    rec = FigureRoleRecord(
        figure_id="f1",
        asset_id="a1",
        asset_sha256="hash",
        role=FigureRole.DECOR,
    )
    FigureRoleStore(ws).save([rec])
    on_disk = FigureRoleStore(ws).load()
    assert len(on_disk) == 1
    assert on_disk[0].figure_id == "f1"
    assert on_disk[0].role is FigureRole.DECOR
    idx = FigureRoleStore(ws).index()
    assert idx["f1"].role is FigureRole.DECOR


# ---------------------------------------------------------------------- #
# FigureRoleOverrideStore
# ---------------------------------------------------------------------- #


def test_apply_figure_role_overrides_persists(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    ws.review_dir.mkdir(parents=True, exist_ok=True)
    applied = apply_figure_role_overrides(
        ws,
        [
            FigureRoleDecision(figure_id="f1", role="decor", reviewer_note="panda"),
            FigureRoleDecision(figure_id="f2", role="content"),
        ],
    )
    assert [d.figure_id for d in applied] == ["f1", "f2"]
    reloaded = FigureRoleOverrideStore(ws).load()
    assert reloaded[0].role == "decor"
    assert reloaded[0].reviewer_note == "panda"
    assert reloaded[0].applied_at  # set by apply_


def test_apply_figure_role_overrides_rejects_unknown_role(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    ws.review_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PromotionError):
        apply_figure_role_overrides(
            ws, [FigureRoleDecision(figure_id="f1", role="BAD")]
        )


def test_apply_figure_role_overrides_requires_figure_id(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    ws.review_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PromotionError):
        apply_figure_role_overrides(
            ws, [FigureRoleDecision(figure_id="", role="content")]
        )


def test_apply_figure_role_overrides_is_noop_when_empty(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    ws.review_dir.mkdir(parents=True, exist_ok=True)
    # No decisions, no file yet → returns empty list and does not create the file.
    out = apply_figure_role_overrides(ws, [])
    assert out == []
    assert not (ws.review_dir / "figure_role_overrides.json").is_file()


# ---------------------------------------------------------------------- #
# Annotator behaviour
# ---------------------------------------------------------------------- #


class _ScriptedProvider:
    """Provider whose response is set per call from a script queue."""

    name = "scripted"
    model = "scripted-model"
    endpoint = ""

    def __init__(self, script: list[dict[str, Any]]):
        self._script = list(script)
        self.calls: list[tuple[Path, str]] = []

    def analyze_image(self, image_path: Path, context: str) -> VlmResponse:
        self.calls.append((image_path, context))
        if not self._script:
            return VlmResponse(error="script exhausted")
        cfg = self._script.pop(0)
        body = json.dumps(cfg)
        return VlmResponse(raw_response=body)


def _make_png(path: Path, color: str = "white") -> None:
    from PIL import Image

    img = Image.new("RGB", (16, 16), color=color)
    img.save(path, "PNG")


def test_annotator_returns_ambiguous_on_provider_error(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    asset = tmp_path / "a.png"
    _make_png(asset)
    annotator = FigureRoleAnnotator(
        provider=_ScriptedProvider([]), workspace=ws, max_provider_retries=0
    )
    role = annotator.classify_one(
        figure_id="f1",
        asset_id="a",
        asset_path=asset,
        item_id="item-err",
        context="ctx",
    )
    assert role.role is FigureRole.AMBIGUOUS
    assert role.reason == "provider_error"
    assert role.item_id == "item-err"
    assert annotator.call_records[0].status == "failed"


def test_annotator_retries_provider_error_then_classifies(
    tmp_path: Path,
) -> None:
    ws = ProjectWorkspace(tmp_path)
    asset = tmp_path / "a.png"
    _make_png(asset)

    class FlakyProvider:
        name = "flaky"
        model = "flaky-model"
        endpoint = ""

        def __init__(self) -> None:
            self.calls = 0

        def analyze_image(self, image_path: Path, context: str) -> VlmResponse:
            self.calls += 1
            if self.calls == 1:
                return VlmResponse(error="temporary 429")
            return VlmResponse(
                raw_response=json.dumps(
                    {"role": "content", "confidence": 0.8, "reason": "ok"}
                )
            )

    provider = FlakyProvider()
    annotator = FigureRoleAnnotator(
        provider=provider,
        workspace=ws,
        max_provider_retries=1,
        retry_backoff_seconds=0,
    )
    role = annotator.classify_one(
        figure_id="f1",
        asset_id="a",
        asset_path=asset,
        item_id="item-retry",
        context="ctx",
    )

    assert provider.calls == 2
    assert role.role is FigureRole.CONTENT
    assert role.item_id == "item-retry"


def test_annotator_returns_ambiguous_on_schema_error(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    asset = tmp_path / "a.png"
    _make_png(asset)
    annotator = FigureRoleAnnotator(
        provider=_ScriptedProvider([{"unrelated": "key"}]),
        workspace=ws,
    )
    role = annotator.classify_one(
        figure_id="f1", asset_id="a", asset_path=asset, context="ctx"
    )
    assert role.role is FigureRole.AMBIGUOUS
    assert role.reason == "schema_error"


def test_annotator_caches_response(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    asset = tmp_path / "a.png"
    _make_png(asset)
    provider = _ScriptedProvider([{"role": "decor", "confidence": 0.9, "reason": "r"}])
    annotator = FigureRoleAnnotator(provider=provider, workspace=ws, cache_dir=tmp_path / "cache")
    r1 = annotator.classify_one(figure_id="f1", asset_id="a", asset_path=asset, context="ctx")
    r2 = annotator.classify_one(figure_id="f1", asset_id="a", asset_path=asset, context="ctx")
    assert r1.role is FigureRole.DECOR
    assert r2.role is FigureRole.DECOR
    assert len(provider.calls) == 1  # second call hit cache


def test_annotator_skips_missing_asset(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    annotator = FigureRoleAnnotator(
        provider=_ScriptedProvider([]), workspace=ws
    )
    role = annotator.classify_one(
        figure_id="f1",
        asset_id="a",
        asset_path=tmp_path / "no-such-file.png",
        context="ctx",
    )
    assert role.role is FigureRole.AMBIGUOUS
    assert role.reason == "asset_unavailable"


def test_annotator_extracts_role_from_wrapped_json(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path)
    asset = tmp_path / "a.png"
    _make_png(asset)

    class WrappedProvider(_ScriptedProvider):
        def analyze_image(self, image_path, context):
            return VlmResponse(
                raw_response=(
                    "noise\n```json\n"
                    "{\"role\": \"content\", \"confidence\": 0.8, "
                    "\"reason\": \"labeled\"}\n```\n"
                )
            )

    annotator = FigureRoleAnnotator(provider=WrappedProvider([]), workspace=ws)
    role = annotator.classify_one(
        figure_id="f1", asset_id="a", asset_path=asset, context="ctx"
    )
    assert role.role is FigureRole.CONTENT
    assert role.confidence == pytest.approx(0.8)
    assert role.reason == "labeled"


# ---------------------------------------------------------------------- #
# classify_figure_roles end-to-end with a hand-built workspace
# ---------------------------------------------------------------------- #


def _seed_workspace(tmp_path: Path) -> ProjectWorkspace:
    """Build a minimal workspace with book_view + assets_registry."""
    ws = ProjectWorkspace(tmp_path)
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    img1 = assets_dir / "asset-1.png"
    img2 = assets_dir / "asset-2.png"
    # Different colors → different SHA-256 → distinct cache keys.
    _make_png(img1, color="white")
    _make_png(img2, color="black")

    (ws.normalized_dir).mkdir(parents=True, exist_ok=True)
    (ws.normalized_dir / "assets_registry.json").write_text(
        json.dumps(
            {
                "assets": [
                    {"asset_id": "asset-1", "local_path": "assets/asset-1.png"},
                    {"asset_id": "asset-2", "local_path": "assets/asset-2.png"},
                ]
            }
        ),
        encoding="utf-8",
    )

    (ws.book_view_dir).mkdir(parents=True, exist_ok=True)
    (ws.book_view_dir / "book_view.json").write_text(
        json.dumps(
            {
                "book_id": "demo",
                "items": [
                    {
                        "item_id": "i1",
                        "item_type": "definition",
                        "title": "三角形的内角和",
                        "text": "求三角形的内角和",
                        "page_refs": [1],
                        "asset_refs": [{"asset_id": "asset-1", "caption": "图"}],
                    },
                    {
                        "item_id": "i2",
                        "item_type": "definition",
                        "title": "题目气泡",
                        "text": "题目",
                        "page_refs": [2],
                        "asset_refs": [{"asset_id": "asset-2", "caption": "卡"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return ws


def test_classify_figure_roles_writes_roles_json(tmp_path: Path) -> None:
    ws = _seed_workspace(tmp_path)
    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "labeled triangle"},
            {"role": "decor", "confidence": 0.85, "reason": "cartoon"},
        ]
    )
    roles = classify_figure_roles(ws, provider=provider)
    assert len(roles) == 2
    by_asset = {r.asset_id: r for r in roles}
    assert by_asset["asset-1"].role is FigureRole.CONTENT
    assert by_asset["asset-2"].role is FigureRole.DECOR

    on_disk = FigureRoleStore(ws).load()
    assert {r.asset_id for r in on_disk} == {"asset-1", "asset-2"}


def test_export_scoped_roles_replace_only_active_export_contexts(tmp_path: Path) -> None:
    ws = _seed_workspace(tmp_path)
    FigureRoleStore(ws).save(
        [
            FigureRoleRecord(
                figure_id="asset-1", asset_id="asset-1", asset_sha256="", role=FigureRole.CONTENT
            ),
            FigureRoleRecord(
                figure_id="asset-2", asset_id="asset-2", asset_sha256="", role=FigureRole.CONTENT
            ),
        ]
    )
    ws.export_plans_dir.mkdir(parents=True, exist_ok=True)
    (ws.export_plans_dir / "plans.json").write_text(
        json.dumps(
            {
                "plans": [
                    {
                        "items": [
                            {
                                "item_id": "i1",
                                "asset_refs": [{"asset_id": "asset-1"}],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = _ScriptedProvider(
        [{"role": "decor", "confidence": 0.9, "reason": "banner"}]
    )

    roles = classify_figure_roles(ws, provider=provider, export_scoped=True)

    assert [role.item_id for role in roles] == ["i1"]
    assert len(provider.calls) == 1
    by_use = FigureRoleStore(ws).index_by_use()
    assert by_use[("asset-1", "i1")].role is FigureRole.DECOR
    assert FigureRoleStore(ws).index()["asset-2"].role is FigureRole.CONTENT
    report = json.loads((ws.reports_dir / "export_scoped_figure_roles.json").read_text())
    assert report["active_export_uses"] == 1
    assert report["provider_candidates"] == 1


def test_export_scoped_roles_exclude_human_overrides_and_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _seed_workspace(tmp_path)
    ws.export_plans_dir.mkdir(parents=True, exist_ok=True)
    (ws.export_plans_dir / "plans.json").write_text(
        json.dumps(
            {
                "plans": [
                    {
                        "items": [
                            {"item_id": "i1", "asset_refs": [{"asset_id": "asset-1"}]},
                            {"item_id": "i2", "asset_refs": [{"asset_id": "asset-2"}]},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    FigureRoleOverrideStore(ws).save([FigureRoleDecision("asset-1", "decor")])
    monkeypatch.setattr(
        "pdf2dt.review.figure_roles.find_template_decor_assets",
        lambda _asset_paths: {"asset-2"},
    )
    provider = _ScriptedProvider([])

    roles = classify_figure_roles(
        ws,
        provider=provider,
        export_scoped=True,
        enable_template_decor_skip=True,
    )

    assert roles == []
    assert provider.calls == []
    report = json.loads((ws.reports_dir / "export_scoped_figure_roles.json").read_text())
    assert report["excluded_human_overrides"] == 1
    assert report["excluded_template_decor"] == 1


def test_template_decor_skip_is_explicit_and_provider_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _seed_workspace(tmp_path)
    monkeypatch.setattr(
        "pdf2dt.review.figure_roles.find_template_decor_assets",
        lambda _asset_paths: {"asset-2"},
    )
    provider = _ScriptedProvider(
        [{"role": "content", "confidence": 0.9, "reason": "triangle"}]
    )

    roles = classify_figure_roles(
        ws,
        provider=provider,
        enable_template_decor_skip=True,
    )

    assert len(provider.calls) == 1
    by_asset = {r.asset_id: r for r in roles}
    assert by_asset["asset-1"].role is FigureRole.CONTENT
    assert by_asset["asset-2"].role is FigureRole.DECOR
    assert by_asset["asset-2"].prefilter_skipped is True
    assert by_asset["asset-2"].prefilter_rule_id == "template_decor"
    assert by_asset["asset-2"].confidence == 0.0
    assert by_asset["asset-2"].prefilter_evidence[0].key == "detector"
    persisted = FigureRoleStore(ws).index()
    assert persisted["asset-2"].prefilter_skipped is True


def test_classify_figure_roles_max_images_caps_run(tmp_path: Path) -> None:
    ws = _seed_workspace(tmp_path)
    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "labeled"},
        ]
    )
    roles = classify_figure_roles(ws, provider=provider, max_images=1)
    assert len(roles) == 1
    assert roles[0].asset_id == "asset-1"


def test_classify_figure_roles_reports_progress(tmp_path: Path) -> None:
    ws = _seed_workspace(tmp_path)
    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "triangle"},
            {"role": "decor", "confidence": 0.8, "reason": "banner"},
        ]
    )
    stream = io.StringIO()

    classify_figure_roles(ws, provider=provider, progress_stream=stream)

    output = stream.getvalue()
    assert "processed=1/2" in output
    assert "processed=2/2 remaining=0" in output
    assert "decor=1" in output
    assert "content=1" in output


def test_classify_figure_roles_preserves_order_with_concurrency(
    tmp_path: Path,
) -> None:
    ws = _seed_workspace(tmp_path)
    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "first"},
            {"role": "decor", "confidence": 0.8, "reason": "second"},
        ]
    )

    roles = classify_figure_roles(ws, provider=provider, max_concurrency=2)

    assert [role.asset_id for role in roles] == ["asset-1", "asset-2"]


def test_cache_enabled_false_skips_disk_cache(tmp_path: Path) -> None:
    """--no-cache must actually disable on-disk caching; otherwise
    iterating on the provider prompt silently keeps returning stale
    results."""
    ws = _seed_workspace(tmp_path)
    cache_dir = ws.root / "providers" / "vlm" / "figure_role_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Pre-seed a stale cache file with the wrong role.
    asset_id = "asset-1"
    asset_path = tmp_path / "assets" / f"{asset_id}.png"
    sha = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    key = hashlib.sha256(
        f"{sha}|scripted-model|{FigureRoleAnnotator._PROMPT_HASH}".encode()
    ).hexdigest()
    stale = {
        "role": "decor",
        "confidence": 0.1,
        "reason": "stale from a prior run",
        "model_id": "scripted-model",
        "classified_at": "1970-01-01T00:00:00Z",
    }
    (cache_dir / f"{key}.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )

    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "fresh"},
            {"role": "content", "confidence": 0.5, "reason": "second"},
        ]
    )
    roles = classify_figure_roles(ws, provider=provider, cache_enabled=False)
    by_asset = {r.asset_id: r for r in roles}
    # Fresh provider wins because cache is bypassed.
    assert by_asset[asset_id].role is FigureRole.CONTENT
    # Provider must have been called once for asset-1; asset-2 stays
    # at default scripted behaviour.
    assert len(provider.calls) == 2


def test_cache_enabled_true_uses_disk_cache(tmp_path: Path) -> None:
    """Default behaviour: a populated cache short-circuits the provider."""
    ws = _seed_workspace(tmp_path)
    asset_path = tmp_path / "assets" / "asset-1.png"

    provider = _ScriptedProvider(
        [
            {"role": "content", "confidence": 0.9, "reason": "first"},
        ]
    )
    annotator = FigureRoleAnnotator(
        provider=provider, workspace=ws, cache_enabled=True
    )
    record1 = annotator.classify_one(
        figure_id="asset-1", asset_id="asset-1", asset_path=asset_path
    )
    assert record1.role is FigureRole.CONTENT
    assert len(provider.calls) == 1

    # Second call should hit the cache, not the provider.
    annotator2 = FigureRoleAnnotator(
        provider=provider, workspace=ws, cache_enabled=True
    )
    record2 = annotator2.classify_one(
        figure_id="asset-1", asset_id="asset-1", asset_path=asset_path
    )
    assert record2.role is FigureRole.CONTENT
    assert len(provider.calls) == 1, "cache hit should not call the provider again"
