"""Tests for the figure-role cluster planner (:mod:`pdf2dt.figure_roles.cluster`).

These tests pin down the **standalone** behaviour of the cluster
planner: pHash grouping, the visual-equivalence predicate, missing /
corrupt file handling, and the default thresholds exposed through
:mod:`pdf2dt.figure_roles`. They do **not** exercise the integration
with the figure-role annotator; that integration is not on main
and is tracked separately.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pdf2dt.figure_roles import (
    DEFAULT_PHASH_HAMMING,
    AssetDescriptor,
    ClusterPlanner,
    build_cluster_planner,
    plan_clusters,
)

# ---------------------------------------------------------------------- #
# Image builders for fixture assets
# ---------------------------------------------------------------------- #


def _write_banner(path: Path, color: str = "skyblue", seed: int = 0) -> None:
    """A 128x96 horizontal banner — used to build "same family" members.

    The aspect ratio (96 / 128 = 0.75) is chosen so the cluster's
    visual-equivalence predicate (``min(dim) / max(dim) > 0.5``)
    passes, matching the auxiliary-layer default thresholds.
    """
    w, h = 128, 96
    im = Image.new("RGB", (w, h), color=color)
    draw = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        r = int(100 + 80 * t + seed * 3) % 256
        g = int(150 + 60 * t)
        b = int(200 - 40 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    draw.ellipse([10, 10, 30, 30], fill=(255, 200, 100), outline=(200, 150, 50))
    draw.ellipse(
        [w - 30, h - 30, w - 10, h - 10],
        fill=(100, 200, 255),
        outline=(50, 150, 200),
    )
    im.save(path, "PNG")


def _write_blank_square(path: Path, color: str = "white") -> None:
    """A 64x64 blank-ish square — same pHash family as a labeled
    2x2 counting grid but a different *real* image. Used to fake
    the sensenova "mixed cluster" scenario.
    """
    im = Image.new("RGB", (64, 64), color=color)
    draw = ImageDraw.Draw(im)
    draw.rectangle([2, 2, 62, 62], outline="black", width=1)
    im.save(path, "PNG")


def _write_outlier(path: Path, color: str = "white", shape: str = "triangle") -> None:
    """A clearly different (large diagram-like) image — should not cluster
    with the banners above. ``shape`` and ``color`` vary the pHash
    so each outlier is its own singleton cluster unless the test
    explicitly asks for matching outliers.
    """
    w, h = 800, 600
    im = Image.new("RGB", (w, h), color=color)
    draw = ImageDraw.Draw(im)
    if shape == "triangle":
        draw.polygon([(400, 100), (100, 500), (700, 500)], outline="black", width=3)
    elif shape == "rectangle":
        draw.rectangle([150, 150, 650, 450], outline="navy", width=4)
        draw.line([(150, 150), (650, 450)], fill="navy", width=2)
        draw.line([(650, 150), (150, 450)], fill="navy", width=2)
    elif shape == "circle":
        draw.ellipse([200, 100, 600, 500], outline="darkred", width=5)
        draw.ellipse([350, 250, 450, 350], fill="darkred")
    else:
        raise ValueError(f"unknown shape: {shape}")
    for x in range(0, w, 50):
        draw.line([(x, 0), (x, h)], fill=(230, 230, 230))
    for y in range(0, h, 50):
        draw.line([(0, y), (w, y)], fill=(230, 230, 230))
    im.save(path, "PNG")


# ---------------------------------------------------------------------- #
# Cluster planner — unit tests
# ---------------------------------------------------------------------- #


def test_plan_clusters_groups_near_identical_banners(tmp_path: Path) -> None:
    """Three near-identical banners → one cluster with 3 members."""
    a1 = tmp_path / "a1.png"
    a2 = tmp_path / "a2.png"
    a3 = tmp_path / "a3.png"
    a4 = tmp_path / "outlier.png"
    _write_banner(a1)
    _write_banner(a2)
    _write_banner(a3)
    _write_outlier(a4)
    descs = [
        AssetDescriptor(asset_id="a1", path=a1),
        AssetDescriptor(asset_id="a2", path=a2),
        AssetDescriptor(asset_id="a3", path=a3),
        AssetDescriptor(asset_id="a4", path=a4),
    ]
    decisions = plan_clusters(descs)
    # Find the multi-member cluster.
    multi = [d for d in decisions if len(d.members) > 1]
    assert len(multi) == 1
    assert set(multi[0].members) == {"a1", "a2", "a3"}
    assert multi[0].visual_equivalent is True
    # The outlier is a singleton.
    singles = [d for d in decisions if len(d.members) == 1]
    assert any(d.representative == "a4" for d in singles)


def test_plan_clusters_refuses_to_merge_mixed_size_cluster(tmp_path: Path) -> None:
    """Mixed cluster: two images that share a pHash family (Hamming
    forced to 255) but fail the size-spread predicate must be flagged
    as **not** visually equivalent so the caller still issues a
    per-member VLM call.

    This is the same failure mode the sensenova ground truth
    surfaced for the 高思竞赛数学课本三年级 corpus. We use a
    tight ``size_spread=1.5`` to make the size check the binding
    constraint without depending on PNG encoder quirks.
    """
    a1 = tmp_path / "small_blank.png"
    a2 = tmp_path / "wide_banner.png"
    _write_blank_square(a1)
    _write_banner(a2)
    descs = [
        AssetDescriptor(asset_id="small_blank", path=a1),
        AssetDescriptor(asset_id="wide_banner", path=a2),
    ]
    # Force permissive Hamming so they would *cluster* on pHash, and
    # a tight size spread so the equivalence check rejects them.
    decisions = plan_clusters(
        descs, hamming_threshold=255, size_spread=1.5
    )
    assert len(decisions) == 1
    d = decisions[0]
    assert set(d.members) == {"small_blank", "wide_banner"}
    # They landed in the same pHash cluster …
    # … but the visual-equivalence check refused to merge.
    assert d.visual_equivalent is False
    # At least one member is flagged as not visually equivalent.
    assert d.member_visual_equivalent != {
        "small_blank": True,
        "wide_banner": True,
    }


def test_plan_clusters_singleton_with_no_match(tmp_path: Path) -> None:
    """A single asset is a singleton cluster."""
    p = tmp_path / "lonely.png"
    _write_banner(p, color="magenta")
    decisions = plan_clusters([AssetDescriptor("lonely", p)])
    assert len(decisions) == 1
    assert decisions[0].members == ["lonely"]
    assert decisions[0].cluster_id == "c0"
    assert decisions[0].visual_equivalent is False


def test_plan_clusters_handles_missing_file(tmp_path: Path) -> None:
    """A descriptor whose file is missing is returned as a __solo singleton."""
    p = tmp_path / "real.png"
    _write_banner(p)
    descs = [
        AssetDescriptor("real", p),
        AssetDescriptor("missing", tmp_path / "missing.png"),
    ]
    decisions = plan_clusters(descs)
    solo = {d.representative for d in decisions if d.cluster_id.endswith("__solo")}
    assert "missing" in solo
    real = [d for d in decisions if d.representative == "real"]
    assert len(real) == 1
    assert real[0].visual_equivalent is False


def test_plan_clusters_handles_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "real.png"
    _write_banner(p)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    decisions = plan_clusters(
        [AssetDescriptor("real", p), AssetDescriptor("bad", bad)]
    )
    assert any(d.representative == "bad" and d.cluster_id.endswith("__solo") for d in decisions)


def test_plan_clusters_empty_input() -> None:
    assert plan_clusters([]) == []


def test_cluster_planner_repr_picks_largest(tmp_path: Path) -> None:
    """The representative is the largest on-disk member."""
    small = tmp_path / "small.png"
    large = tmp_path / "large.png"
    _write_banner(small)
    _write_banner(large)
    # Same seed → same pHash family → same cluster.
    descs = [
        AssetDescriptor("small", small),
        AssetDescriptor("large", large),
    ]
    decisions = plan_clusters(descs, hamming_threshold=4)
    assert len(decisions) == 1
    assert decisions[0].representative == "large"


def test_default_thresholds_match_phase1_plan() -> None:
    """Sanity check: the default Hamming threshold is 4 (auxiliary layer)."""
    assert DEFAULT_PHASH_HAMMING == 4
    planner = build_cluster_planner()
    assert isinstance(planner, ClusterPlanner)
    assert planner.hamming_threshold == 4
