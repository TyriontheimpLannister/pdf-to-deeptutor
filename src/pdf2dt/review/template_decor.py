"""Identify template / banner decor assets by perceptual-hash clustering.

For every asset in the workspace, compute a 16x16 difference hash.
Group assets whose dhash hamming distance is <= 6 — these are
visually-near-identical (the OCR pipeline tends to re-encode the
same publisher template with very small jitter). When a cluster
contains 3 or more members, the cluster is almost certainly a
decorative template (cartoon banner, character portrait, etc.) and
all its members should be flagged decor.

Returns a set of asset_ids.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from PIL import Image


def _dhash(path: Path, hash_size: int = 16) -> int:
    with Image.open(path) as im:
        im = im.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(im.getdata())
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


def find_template_decor_assets(
    asset_paths: Iterable[tuple[str, Path]],
    *,
    hamming_threshold: int = 6,
    min_cluster_size: int = 3,
) -> set[str]:
    """Return asset_ids that look like repeated template decorations.

    The dhash threshold of 6 over a 16x16 (256-bit) hash is tight
    enough that *different* math diagrams do not match each other
    but loose enough that the same banner re-encoded by OCR ends
    up in the same cluster.
    """
    hashes: dict[str, int] = {}
    for aid, p in asset_paths:
        if not p.is_file():
            continue
        try:
            hashes[aid] = _dhash(p)
        except Exception:
            continue
    # Group by transitive hamming threshold.
    parent: dict[str, str] = {aid: aid for aid in hashes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    aids = list(hashes.keys())
    for i, a in enumerate(aids):
        for b in aids[i + 1 :]:
            if _hamming(hashes[a], hashes[b]) <= hamming_threshold:
                union(a, b)
    clusters: dict[str, list[str]] = defaultdict(list)
    for aid in aids:
        clusters[find(aid)].append(aid)
    out: set[str] = set()
    for cluster in clusters.values():
        if len(cluster) >= min_cluster_size:
            out.update(cluster)
    return out
