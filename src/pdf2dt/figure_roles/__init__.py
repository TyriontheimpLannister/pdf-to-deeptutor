"""Figure-role clustering utilities (auxiliary layer).

This package groups visually-near-identical figure assets by perceptual
hash (dhash, 16x16, 256-bit). It is an auxiliary layer in the
figure-role classification pipeline: it does not by itself
collapse the VLM call budget, but it provides cache-consistency
and an audit trail for reviewers (cluster_id /
cluster_visual_equivalent / cluster_source_sha) and refuses to
inherit roles across pHash-family members that fail the
visual-equivalence predicate.

The historical "< 200 VLM calls" target attached to this module
was based on a flawed assumption (per-page decor jpegs do not
repeat). On the current corpus the cluster layer alone projects
~5 % savings — see
``docs/decisions/2026-07-15-phase1-cluster-pipeline-diagnostic.md``
for the measurement. The next phase is a provider-independent
pre-filter layer that the cluster layer feeds into; until that
work lands, callers should treat the cluster pipeline as a
consistency / dedup helper, not a cost lever.

Cluster membership is a dedupe / candidate-list signal, **not** a
role-inheritance signal. Two members of the same pHash cluster may
still have different roles (e.g. a blank square frame vs. a labeled
2x2 counting grid share a pHash family). Callers must consult
:meth:`ClusterDecision.visual_equivalent` before propagating a role.
"""
from __future__ import annotations

from .cluster import (
    DEFAULT_PHASH_HAMMING,
    DEFAULT_VISUAL_AR_MIN,
    DEFAULT_VISUAL_SIZE_SPREAD,
    AssetDescriptor,
    ClusterDecision,
    ClusterPlanner,
    build_cluster_planner,
    plan_clusters,
)

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
