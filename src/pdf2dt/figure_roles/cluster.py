"""Perceptual-hash clustering for figure-role classification.

Role in the pipeline
--------------------
This module is the **auxiliary layer** of the figure-role
classification pipeline. It does not by itself reduce the VLM
call budget enough to hit the historical "< 200 calls" target —
the 2026-07-15 proxy verification on
``projects/高思竞赛数学课本三年级`` showed the cluster pipeline
on its own projects 447 / 474 VLM calls (5.7 % savings at the
Phase 1 default Hamming 4). The dominant cost is *per-page
decor jpegs that MinerU re-encodes distinctly*, which a
perceptual-hash clusterer cannot collapse.

The cluster layer is kept because it provides four real
benefits even when the savings curve is small:

1. **Cache consistency** — cluster members share the same
   ``(asset_sha256, model_id, prompt_hash)`` VLM cache key
   once the representative is classified, so re-runs with a
   new provider stay cheap.
2. **Mixed-cluster detection** — when two assets share a
   pHash family but fail the visual-equivalence predicate, the
   ``cluster_visual_equivalent=False`` audit field tells the
   reviewer that the system intentionally refused to inherit
   across them. This is the safety net the sensenova ground
   truth asked for.
3. **Partial visual-dedup** — when decor images *do* repeat
   (a few page-banner templates, re-used section icons), the
   clusterer collapses them, saving 4-12 % on real corpora.
4. **Audit trail** — ``cluster_id`` and ``cluster_source_sha``
   make the review surface explainable: a reviewer can ask
   "why is this figure flagged the same role as figure 47?"
   and get a deterministic answer.

The **next phase** (2026-07-15 decision record) is a
provider-agnostic pre-filter layer that uses
:func:`pdf2dt.review.template_decor.find_template_decor_assets`
plus text/layout context (currently only the mock provider
uses them) to skip high-confidence decor before any VLM call.
That work is tracked separately and **does not** require any
change to this module.

Why the visual-equivalence check exists
---------------------------------------
A pHash Hamming match alone is a *deduplication* signal, not a
*role-equivalence* signal. The sensenova ground-truth run
surfaced mixed-label pHash clusters: the same hash family but
different real content — e.g. a blank square frame vs. a
labeled 2×2 counting grid. Calling them the same role would
mis-embed or mis-drop math content.

To stay safe we only inherit across members that are also
*visually equivalent*:

* aspect-ratio similarity: ``min(dim) / max(dim) > VISUAL_AR_MIN``
* size spread: ``max(file_bytes) / min(file_bytes) < VISUAL_SIZE_SPREAD``

If either check fails, the member gets its own VLM call. This
is the safer failure mode (false negative: a human would call
two images "the same banner"; the system still calls the VLM
and pays for it, but no math figure is dropped).

API surface
-----------
* :class:`AssetDescriptor` — input row (asset_id, local_path, bytes).
* :class:`ClusterPlanner` / :func:`build_cluster_planner` — compute
  pHash + size + dimensions for an iterable of descriptors.
* :func:`plan_clusters` — produce a list of :class:`ClusterDecision`
  with the per-cluster representative, the per-member
  ``cluster_id``, and the ``visual_equivalent`` flag.
* :class:`ClusterDecision` — the per-cluster view exposed to the
  figure-role annotator.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------- #
# Defaults — agreed in the Phase 1 plan
# ---------------------------------------------------------------------- #

# pHash Hamming distance threshold for cluster membership. 4 is the
# conservative default (vs 8 used by the original template-decor
# clusterer). Tighter = safer equivalence guarantee; loses a small
# amount of cross-resolution grouping.
DEFAULT_PHASH_HAMMING = 4

# Visual-equivalence predicate thresholds.
DEFAULT_VISUAL_AR_MIN = 0.5
DEFAULT_VISUAL_SIZE_SPREAD = 5.0

# pHash grid size. 16x16 = 256 bits matches the existing
# template-decor clusterer in :mod:`pdf2dt.review.template_decor`.
_DEFAULT_PHASH_SIZE = 16


# ---------------------------------------------------------------------- #
# Data classes
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class AssetDescriptor:
    """One figure asset we want to consider for clustering.

    ``asset_id`` is the book-view asset_id. ``path`` is the on-disk
    image file (used to compute pHash, dimensions, byte size).
    ``size_bytes`` is an optional pre-computed file size hint that
    callers can supply to skip an extra ``stat()`` round trip; when
    missing the planner reads it from ``path``.
    """

    asset_id: str
    path: Path
    size_bytes: int | None = None


@dataclass
class _AssetFingerprint:
    """Computed pHash + size + dimensions for one descriptor."""

    asset_id: str
    path: Path
    size_bytes: int
    width: int
    height: int
    phash: int


@dataclass
class ClusterDecision:
    """One cluster's decision.

    Attributes
    ----------
    cluster_id
        Stable identifier of the form ``c{N}`` (e.g. ``c0``, ``c1``).
    representative
        The asset_id that the VLM should classify. Defaults to the
        member with the largest on-disk file size (a coarse proxy
        for "least lossy compression" → most informative copy).
    members
        All asset_ids in the cluster, including the representative.
    member_visual_equivalent
        Map of asset_id → bool. ``False`` means "same pHash family
        but not visually equivalent" — caller must run its own VLM
        call. ``True`` means safe to inherit the representative's
        role.
    visual_equivalent
        Convenience flag: ``True`` iff every member passes the
        visual-equivalence check. When ``False``, callers should
        fall back to per-member VLM calls.
    """

    cluster_id: str
    representative: str
    members: list[str] = field(default_factory=list)
    member_visual_equivalent: dict[str, bool] = field(default_factory=dict)
    visual_equivalent: bool = False


# ---------------------------------------------------------------------- #
# pHash + fingerprint helpers
# ---------------------------------------------------------------------- #


def _dhash(path: Path, hash_size: int = _DEFAULT_PHASH_SIZE) -> int | None:
    """Compute a 16x16 dhash as a 256-bit int. ``None`` on failure."""
    try:
        with Image.open(path) as im:
            gray = im.convert("L").resize((hash_size + 1, hash_size))
            pixels = list(gray.getdata())
    except (OSError, ValueError):
        return None
    h = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            if left > right:
                h |= 1 << (row * hash_size + col)
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size  # (width, height)
    except (OSError, ValueError):
        return None


def _fingerprint(descriptor: AssetDescriptor) -> _AssetFingerprint | None:
    """Return a fingerprint, or ``None`` if the asset is unreadable."""
    path = descriptor.path
    if not path.is_file():
        return None
    try:
        size = (
            descriptor.size_bytes
            if descriptor.size_bytes is not None
            else path.stat().st_size
        )
    except OSError:
        return None
    dims = _image_dimensions(path)
    if dims is None:
        return None
    width, height = dims
    phash = _dhash(path)
    if phash is None:
        return None
    return _AssetFingerprint(
        asset_id=descriptor.asset_id,
        path=path,
        size_bytes=int(size),
        width=int(width),
        height=int(height),
        phash=phash,
    )


# ---------------------------------------------------------------------- #
# Visual-equivalence predicate
# ---------------------------------------------------------------------- #


def _visual_equivalent(
    fingerprints: list[_AssetFingerprint],
    *,
    ar_min: float = DEFAULT_VISUAL_AR_MIN,
    size_spread: float = DEFAULT_VISUAL_SIZE_SPREAD,
) -> dict[str, bool]:
    """Compute per-member visual-equivalence flag against the cluster.

    The predicate is:

    * aspect-ratio similarity: ``min(dim) / max(dim) > ar_min``
      (default 0.5) — i.e. the longer side is at most 2x the shorter.
    * size similarity: ``max(bytes) / min(bytes) < size_spread``
      (default 5x) — members must not differ by more than the spread.

    The cluster passes only when *every* member satisfies both checks.
    """
    if not fingerprints:
        return {}
    out: dict[str, bool] = {}
    # Use byte size for the spread (it scales linearly with the amount
    # of PNG data; we are not looking for exact bytes, just
    # "approximately the same encoding weight"). Empty members
    # (zero bytes) are rejected as not visually equivalent.
    sizes = [max(1, f.size_bytes) for f in fingerprints]
    max_size = max(sizes)
    min_size = min(sizes)
    size_ratio = max_size / min_size if min_size else float("inf")
    size_ok = size_ratio < size_spread
    for f in fingerprints:
        longer = max(f.width, f.height)
        shorter = min(f.width, f.height) if min(f.width, f.height) > 0 else 1
        ar_ok = (shorter / longer) > ar_min
        # An asset with zero or negative size is an obvious fail.
        member_size_ok = f.size_bytes > 0
        out[f.asset_id] = ar_ok and size_ok and member_size_ok
    return out


# ---------------------------------------------------------------------- #
# Union-Find pHash clustering
# ---------------------------------------------------------------------- #


def _union_find_clusters(
    fingerprints: list[_AssetFingerprint],
    *,
    hamming_threshold: int = DEFAULT_PHASH_HAMMING,
) -> list[list[str]]:
    """Group fingerprints by transitive pHash Hamming proximity."""
    if not fingerprints:
        return []
    parent: dict[str, str] = {f.asset_id: f.asset_id for f in fingerprints}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    n = len(fingerprints)
    for i in range(n):
        a = fingerprints[i]
        for j in range(i + 1, n):
            b = fingerprints[j]
            if _hamming(a.phash, b.phash) <= hamming_threshold:
                union(a.asset_id, b.asset_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for f in fingerprints:
        groups[find(f.asset_id)].append(f.asset_id)
    return [sorted(g) for g in groups.values()]


# ---------------------------------------------------------------------- #
# Planner / public API
# ---------------------------------------------------------------------- #


@dataclass
class ClusterPlanner:
    """Configurable planner that turns descriptors into cluster decisions.

    Use :func:`build_cluster_planner` for the default
    Phase-1 configuration; the dataclass is exposed so tests can
    inject different thresholds.
    """

    hamming_threshold: int = DEFAULT_PHASH_HAMMING
    ar_min: float = DEFAULT_VISUAL_AR_MIN
    size_spread: float = DEFAULT_VISUAL_SIZE_SPREAD

    def plan(
        self,
        descriptors: Iterable[AssetDescriptor],
    ) -> list[ClusterDecision]:
        """Build a list of :class:`ClusterDecision` for ``descriptors``.

        Each ``ClusterDecision`` covers a single pHash cluster. The
        representative is the largest member (proxy for "least lossy
        copy" of the image). Members that pass both the aspect-ratio
        and size-spread checks get ``visual_equivalent=True``;
        others are flagged ``False`` so the caller knows to issue a
        per-member VLM call.

        Descriptors that fail to fingerprint (missing file, corrupt
        image, unreadable) are returned as singletons with
        ``cluster_id="<asset_id>__solo"`` and
        ``visual_equivalent=False`` so the caller still classifies
        them directly.
        """
        fingerprints: list[_AssetFingerprint] = []
        solo: list[str] = []
        for d in descriptors:
            fp = _fingerprint(d)
            if fp is None:
                solo.append(d.asset_id)
                continue
            fingerprints.append(fp)
        groups = _union_find_clusters(
            fingerprints, hamming_threshold=self.hamming_threshold
        )
        # Build a lookup from asset_id → fingerprint for the per-cluster
        # representative + visual-equivalence pass.
        fp_by_id: dict[str, _AssetFingerprint] = {f.asset_id: f for f in fingerprints}
        decisions: list[ClusterDecision] = []
        for idx, members in enumerate(sorted(groups, key=lambda g: g[0])):
            cluster_id = f"c{idx}"
            member_fps = [fp_by_id[m] for m in members]
            # Pick the largest member as the representative.
            representative = max(
                member_fps, key=lambda f: (f.size_bytes, f.width * f.height)
            ).asset_id
            equiv = _visual_equivalent(
                member_fps,
                ar_min=self.ar_min,
                size_spread=self.size_spread,
            )
            # A cluster only earns ``visual_equivalent=True`` when
            # there is *something to compare* (≥ 2 members) and every
            # member passes the visual-equivalence predicate. A
            # singleton cluster has no cross-member check to perform;
            # reporting True for it would suggest the cluster
            # "passed" an equivalence check that never ran.
            if len(members) >= 2:
                all_equivalent = all(equiv.values()) if equiv else False
            else:
                all_equivalent = False
            decisions.append(
                ClusterDecision(
                    cluster_id=cluster_id,
                    representative=representative,
                    members=list(members),
                    member_visual_equivalent=dict(equiv),
                    visual_equivalent=all_equivalent,
                )
            )
        for asset_id in sorted(solo):
            decisions.append(
                ClusterDecision(
                    cluster_id=f"{asset_id}__solo",
                    representative=asset_id,
                    members=[asset_id],
                    member_visual_equivalent={asset_id: False},
                    visual_equivalent=False,
                )
            )
        return decisions


def build_cluster_planner(
    *,
    hamming_threshold: int = DEFAULT_PHASH_HAMMING,
    ar_min: float = DEFAULT_VISUAL_AR_MIN,
    size_spread: float = DEFAULT_VISUAL_SIZE_SPREAD,
) -> ClusterPlanner:
    """Construct a :class:`ClusterPlanner` with explicit thresholds.

    Defaults are the auxiliary-layer conservative settings: Hamming
    4, aspect-ratio 0.5, size spread 5x. They are conservative
    because the layer's value comes from correctness (mixed-cluster
    refusal, audit trail), not from savings; tightening a threshold
    costs a small amount of cross-resolution grouping but never
    misclassifies a real content figure as decor.
    """
    return ClusterPlanner(
        hamming_threshold=hamming_threshold,
        ar_min=ar_min,
        size_spread=size_spread,
    )


def plan_clusters(
    descriptors: Iterable[AssetDescriptor],
    *,
    hamming_threshold: int = DEFAULT_PHASH_HAMMING,
    ar_min: float = DEFAULT_VISUAL_AR_MIN,
    size_spread: float = DEFAULT_VISUAL_SIZE_SPREAD,
) -> list[ClusterDecision]:
    """Convenience wrapper around :class:`ClusterPlanner.plan`."""
    return build_cluster_planner(
        hamming_threshold=hamming_threshold,
        ar_min=ar_min,
        size_spread=size_spread,
    ).plan(descriptors)


__all__ = [
    "AssetDescriptor",
    "ClusterDecision",
    "ClusterPlanner",
    "build_cluster_planner",
    "plan_clusters",
    "DEFAULT_PHASH_HAMMING",
    "DEFAULT_VISUAL_AR_MIN",
    "DEFAULT_VISUAL_SIZE_SPREAD",
]
