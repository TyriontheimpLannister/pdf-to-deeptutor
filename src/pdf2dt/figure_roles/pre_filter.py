"""Phase 1.5 — Provider-Independent Pre-Filter (pure core).

This module implements the **dry-run core** of the pre-filter
documented in ``docs/decisions/2026-07-15-phase15-prefilter-design-freeze.md``.

Scope of THIS module:

- Immutable data structures (``Evidence``, ``Candidate``,
  ``PreFilterDecision``).
- ``(asset, item/context)`` candidate granularity.
- Two outputs only: ``decor`` or ``defer``.
- Three candidate rules: ``decor_phrase_in_context``,
  ``tiny_icon_size``, ``extreme_aspect_ratio``.
- ``inputs_hash`` and ``rules_config_hash`` deterministic
  serialisation.
- A small in-memory runner that walks a list of candidates and
  applies the rules; it does NOT call any provider, does NOT
  read or write the workspace, and does NOT touch the
  ``reports/`` directory. Persistence is layered on top in a
  later commit.

Explicitly NOT in this module:

- No wiring into ``review/figure_roles.py`` /
  ``scripts/classify_image_roles.py``.
- No CLI flags.
- No real-provider integration.
- No call to any VLM.
- No use of the cluster planner for savings (cluster_audit is
  reported by the persistence layer; here we only emit a
  ``PreFilterDecision`` per candidate).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

# ---------------------------------------------------------------------- #
# Constants
# ---------------------------------------------------------------------- #


SCHEMA_VERSION = "pre_filter_report/v1"

# Default rule versions. A rule's `rule_version` MUST be bumped
# whenever the rule's decisive predicate changes, so that
# `rules_config_hash` reflects the rule's behaviour, not just
# the rule's config knobs.
DEFAULT_RULE_VERSIONS: dict[str, str] = {
    "decor_phrase_in_context": "v1",
    "tiny_icon_size": "v1",
    "extreme_aspect_ratio": "v1",
}

# Default thresholds. These are the conservative defaults from the
# mock provider heuristics. They are NOT calibrated.
DEFAULT_TINY_AREA_PX = 10_000
DEFAULT_BANNER_AR_MAX = 5.0
DEFAULT_BANNER_AR_MIN = 0.2

# Decisive phrases (provider-independent OCR-text signals). These
# mirror the existing `DECOR_CONTEXT_PATTERNS` from
# `review/figure_roles.py`; we do NOT import from there to keep
# this module free of the review/ PIL cascade.
DECOR_PHRASES: tuple[str, ...] = (
    "微信公众号 教辅资料站",
    "微信公众号",
    "教辅资料站",
    "学而思",
    "新东方",
    "高思教育",
)


# ---------------------------------------------------------------------- #
# Immutable data structures
# ---------------------------------------------------------------------- #


DecisionT = Literal["decor", "defer"]


@dataclass(frozen=True)
class Evidence:
    """One piece of rule-specific proof. Immutable.

    The value field is restricted to hashable primitives so the
    entire evidence tuple is hashable and JSON-round-trippable
    without lossy conversion.
    """

    key: str
    value: str | int | float | bool

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        if "key" not in d or "value" not in d:
            raise ValueError(f"Evidence missing key/value: {d!r}")
        v = d["value"]
        if not isinstance(v, (str, int, float, bool)):
            raise ValueError(f"Evidence.value must be a primitive, got {type(v).__name__}")
        return cls(key=d["key"], value=v)


@dataclass(frozen=True)
class Candidate:
    """One (asset, item/context) tuple the pre-filter can decide on.

    Fields are immutable. ``context_text`` is the OCR text window
    used by rules that inspect text. ``width`` / ``height`` are
    pixel dimensions; rules that need them do not have to open
    the image themselves. ``asset_content_hash`` is the hex
    SHA-256 of the asset's bytes; the on-disk path is NOT a
    field (it is environment-specific and would make
    ``inputs_hash`` non-reproducible across machines).
    """

    asset_id: str
    item_id: str
    context_hash: str
    context_text: str
    width: int
    height: int
    asset_content_hash: str  # hex SHA-256 of the asset's bytes

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("Candidate.asset_id must be non-empty")
        if not self.item_id:
            raise ValueError("Candidate.item_id must be non-empty")
        if not self.context_hash:
            raise ValueError("Candidate.context_hash must be non-empty")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"Candidate dimensions must be positive: {self.width}x{self.height}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "item_id": self.item_id,
            "context_hash": self.context_hash,
            "context_text": self.context_text,
            "width": self.width,
            "height": self.height,
            "asset_content_hash": self.asset_content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        return cls(
            asset_id=d["asset_id"],
            item_id=d["item_id"],
            context_hash=d["context_hash"],
            context_text=d.get("context_text", ""),
            width=int(d["width"]),
            height=int(d["height"]),
            asset_content_hash=d["asset_content_hash"],
        )


@dataclass(frozen=True)
class PreFilterDecision:
    """One pre-filter verdict for one candidate.

    ``decision`` is exactly ``"decor"`` or ``"defer"``. The
    pre-filter MUST NOT emit ``"content"``. ``evidence`` is
    empty iff ``decision == "defer"``.
    """

    asset_id: str
    item_id: str
    context_hash: str
    decision: DecisionT
    rule_id: str  # "" when decision == "defer"
    evidence: tuple[Evidence, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("PreFilterDecision.asset_id must be non-empty")
        if not self.item_id:
            raise ValueError("PreFilterDecision.item_id must be non-empty")
        if not self.context_hash:
            raise ValueError("PreFilterDecision.context_hash must be non-empty")
        if self.decision not in ("decor", "defer"):
            raise ValueError(
                f"PreFilterDecision.decision must be 'decor' or 'defer', "
                f"got {self.decision!r}"
            )
        if self.decision == "defer":
            if self.rule_id != "":
                raise ValueError("defer decisions must have rule_id == ''")
            if self.evidence:
                raise ValueError("defer decisions must have empty evidence")
        else:  # decor
            if not self.rule_id:
                raise ValueError("decor decisions must have a non-empty rule_id")
            if not self.evidence:
                raise ValueError("decor decisions must have non-empty evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "item_id": self.item_id,
            "context_hash": self.context_hash,
            "decision": self.decision,
            "rule_id": self.rule_id,
            "evidence": [e.to_dict() for e in self.evidence],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PreFilterDecision:
        return cls(
            asset_id=d["asset_id"],
            item_id=d["item_id"],
            context_hash=d["context_hash"],
            decision=d["decision"],
            rule_id=d.get("rule_id", ""),
            evidence=tuple(Evidence.from_dict(e) for e in d.get("evidence", [])),
            reason=d.get("reason", ""),
        )


# ---------------------------------------------------------------------- #
# PreFilterConfig
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleConfig:
    """Per-rule threshold / static config."""

    rule_id: str
    rule_version: str
    # Rule-specific thresholds. Kept as a typed mapping to avoid
    # lossy round-trips.
    threshold: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "threshold", MappingProxyType(dict(self.threshold)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "threshold": dict(self.threshold),
        }


@dataclass(frozen=True)
class PreFilterConfig:
    """Frozen config driving a dry-run.

    The config participates in `rules_config_hash`. Rule
    implementations are NOT part of the hash directly; the
    `rule_version` field of each `RuleConfig` is the implementer's
    honest declaration of "the rule is the same code as the last
    time this hash was produced".
    """

    schema_version: str = SCHEMA_VERSION
    rules: tuple[RuleConfig, ...] = ()
    enabled_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("schema_version must be non-empty")
        rule_ids = {r.rule_id for r in self.rules}
        for rid in self.enabled_rules:
            if rid not in rule_ids:
                raise ValueError(
                    f"enabled_rules references unknown rule_id: {rid!r}"
                )

    @classmethod
    def default(cls) -> PreFilterConfig:
        rules = (
            RuleConfig(
                rule_id="decor_phrase_in_context",
                rule_version=DEFAULT_RULE_VERSIONS["decor_phrase_in_context"],
                threshold={"phrases": list(DECOR_PHRASES)},
            ),
            RuleConfig(
                rule_id="tiny_icon_size",
                rule_version=DEFAULT_RULE_VERSIONS["tiny_icon_size"],
                threshold={"max_area_px": DEFAULT_TINY_AREA_PX},
            ),
            RuleConfig(
                rule_id="extreme_aspect_ratio",
                rule_version=DEFAULT_RULE_VERSIONS["extreme_aspect_ratio"],
                threshold={
                    "ar_max": DEFAULT_BANNER_AR_MAX,
                    "ar_min": DEFAULT_BANNER_AR_MIN,
                },
            ),
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            rules=rules,
            enabled_rules=tuple(r.rule_id for r in rules),
        )


# ---------------------------------------------------------------------- #
# Deterministic serialisation
# ---------------------------------------------------------------------- #


def _canonical_json(obj: Any) -> bytes:
    """UTF-8, no insignificant whitespace, keys sorted, no trailing newline."""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _candidate_sort_key(c: Candidate) -> tuple[bytes, bytes, bytes]:
    return (
        c.asset_id.encode("utf-8"),
        c.item_id.encode("utf-8"),
        c.context_hash.encode("utf-8"),
    )


def compute_inputs_hash(candidates: Sequence[Candidate]) -> str:
    """SHA-256 over a canonical encoding of the candidate list.

    The hash payload is the full sorted candidate list encoded
    as canonical JSON. The payload includes, per candidate, ALL
    of the following fields (no field may be elided without
    breaking the reproducibility contract):

    - ``asset_id``           — str
    - ``item_id``            — str
    - ``context_hash``       — str
    - ``context_text``       — str (OCR text window)
    - ``width``              — int
    - ``height``             — int
    - ``asset_content_hash`` — str (hex SHA-256 of the asset's
      bytes; the on-disk path is intentionally NOT included
      because it is environment-specific and would make the
      hash non-reproducible across machines)

    The candidate list is sorted by
    ``(asset_id, item_id, context_hash)`` ascending with UTF-8
    byte ordering. ``context_text`` is preserved in its
    ORIGINAL order within each tuple (it is NOT re-sorted).

    Hash sensitivity contract: changing ``context_text`` or
    ``asset_content_hash`` MUST change the hash. This is
    asserted by `test_p1_5_t13` in
    `tests/test_figure_role_prefilter.py`.
    """
    sorted_candidates = sorted(candidates, key=_candidate_sort_key)
    payload = [c.to_dict() for c in sorted_candidates]
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def compute_rules_config_hash(config: PreFilterConfig) -> str:
    """SHA-256 over a canonical encoding of the rules config.

    Includes ``schema_version``, the sorted rule list (with
    ``rule_version`` per rule), the sorted enabled set. Does
    NOT include human review state (e.g. ``approved_rules``),
    because that is not a run input.
    """
    payload = {
        "schema_version": config.schema_version,
        "rules": [r.to_dict() for r in sorted(config.rules, key=lambda r: r.rule_id)],
        "enabled_rules": sorted(config.enabled_rules),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


# ---------------------------------------------------------------------- #
# Rules
# ---------------------------------------------------------------------- #


def _phrase_evidence(phrase: str) -> tuple[Evidence, ...]:
    return (Evidence("matched_phrase", phrase),)


def _size_evidence(width: int, height: int) -> tuple[Evidence, ...]:
    return (
        Evidence("width", width),
        Evidence("height", height),
        Evidence("area_px", width * height),
    )


def _aspect_evidence(width: int, height: int) -> tuple[Evidence, ...]:
    ar = max(width, height) / min(width, height)
    return (
        Evidence("width", width),
        Evidence("height", height),
        Evidence("aspect_ratio", ar),
    )


def _rule_decor_phrase_in_context(
    candidate: Candidate, config: RuleConfig
) -> PreFilterDecision | None:
    phrases = tuple(config.threshold.get("phrases", ()))
    for phrase in phrases:
        if phrase and phrase in candidate.context_text:
            return PreFilterDecision(
                asset_id=candidate.asset_id,
                item_id=candidate.item_id,
                context_hash=candidate.context_hash,
                decision="decor",
                rule_id="decor_phrase_in_context",
                evidence=_phrase_evidence(phrase),
                reason=f"OCR context contains decor phrase {phrase!r}",
            )
    return None


def _rule_tiny_icon_size(
    candidate: Candidate, config: RuleConfig
) -> PreFilterDecision | None:
    max_area = int(config.threshold.get("max_area_px", DEFAULT_TINY_AREA_PX))
    area = candidate.width * candidate.height
    if area < max_area:
        return PreFilterDecision(
            asset_id=candidate.asset_id,
            item_id=candidate.item_id,
            context_hash=candidate.context_hash,
            decision="decor",
            rule_id="tiny_icon_size",
            evidence=_size_evidence(candidate.width, candidate.height),
            reason=f"image area {area}px < {max_area}px",
        )
    return None


def _rule_extreme_aspect_ratio(
    candidate: Candidate, config: RuleConfig
) -> PreFilterDecision | None:
    ar_max = float(config.threshold.get("ar_max", DEFAULT_BANNER_AR_MAX))
    ar_min = float(config.threshold.get("ar_min", DEFAULT_BANNER_AR_MIN))
    ar = max(candidate.width, candidate.height) / min(candidate.width, candidate.height)
    if ar > ar_max or ar < ar_min:
        return PreFilterDecision(
            asset_id=candidate.asset_id,
            item_id=candidate.item_id,
            context_hash=candidate.context_hash,
            decision="decor",
            rule_id="extreme_aspect_ratio",
            evidence=_aspect_evidence(candidate.width, candidate.height),
            reason=f"aspect ratio {ar:.3f} outside [{ar_min}, {ar_max}]",
        )
    return None


_RULE_DISPATCH: dict[str, Any] = {
    "decor_phrase_in_context": _rule_decor_phrase_in_context,
    "tiny_icon_size": _rule_tiny_icon_size,
    "extreme_aspect_ratio": _rule_extreme_aspect_ratio,
}


# ---------------------------------------------------------------------- #
# Pure runner
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleStats:
    """Per-rule counters for one dry-run."""

    rule_id: str
    hits: int = 0
    defers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "hits": self.hits,
            "defers": self.defers,
            "ground_truth_comparison": {"status": "unverified"},
            "spot_check_result": "",
        }


@dataclass(frozen=True)
class PreFilterRunResult:
    """The pure in-memory result of one dry-run.

    This is a dry-run artifact only. The persistence layer
    (added in a later commit) wraps it in a JSON file at
    ``reports/pre_filter_dry_run.json``.

    The result distinguishes three quantities that the report
    MUST keep separate:

    - ``candidate_hits`` (derived): per-rule count of candidates
      on which the rule's predicate fired. Independent of the
      ``approved_rule_ids`` gate.
    - ``approved_decor_decisions`` (derived): the number of
      ``PreFilterDecision`` records with ``decision == "decor"``
      — i.e. the number of candidates the runner would actually
      skip a VLM call for under the current approval set.
    - ``projected_saved_calls`` (derived, informational): the
      number of distinct candidate tuples on which at least one
      candidate rule fired. This is what the rule set WOULD save
      if every candidate rule were approved. It is reported as an
      informational projection only; it does NOT change actual
      decisions and it is independent of the cluster layer.
    """

    decisions: tuple[PreFilterDecision, ...]
    rule_stats: Mapping[str, RuleStats]
    candidates_total: int
    unique_decor_tuples: tuple[tuple[str, str, str], ...]
    inputs_hash: str
    rules_config_hash: str
    schema_version: str
    # Per-tuple decorator rule ids. A tuple may be flagged by more
    # than one rule; this captures the union of those rule ids
    # so the persistence layer can show how many candidates a given
    # rule fired on without recomputing.
    decor_rule_ids: Mapping[tuple[str, str, str], tuple[str, ...]]
    # Candidate-level view of which rules fired on which candidate.
    # Different from ``decor_rule_ids`` (which only counts
    # approved-and-emitted rules): this captures ALL rules whose
    # predicate fired, including unapproved ones, and is the
    # basis for ``projected_saved_calls`` (distinct key count).
    # Stored as an immutable, deterministically-ordered tuple
    # of (key, frozenset) pairs so the frozen dataclass
    # invariant is real (no mutable mapping hidden behind it)
    # and so cross-run comparisons are stable.
    _candidate_fired_rules: tuple[tuple[tuple[str, str, str], frozenset[str]], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_stats", MappingProxyType(dict(self.rule_stats)))
        object.__setattr__(
            self,
            "decor_rule_ids",
            MappingProxyType(dict(self.decor_rule_ids)),
        )

    @property
    def candidate_hits(self) -> Mapping[str, int]:
        """Per-rule count of candidates on which the rule's
        predicate fired, regardless of approval. This is the
        raw "would-have-fired" count the reviewer uses to size
        the candidate rule set. Multiple rules firing on the
        SAME candidate each contribute to their own
        ``candidate_hits[rule_id]``; the candidate is counted
        per rule, not deduped here."""
        return MappingProxyType(
            {rule_id: stats.hits for rule_id, stats in self.rule_stats.items()}
        )

    @property
    def approved_decor_decisions(self) -> int:
        """Number of ``PreFilterDecision`` records whose
        ``decision == "decor"`` — i.e. the count the runner
        would actually let skip a VLM call under the current
        ``approved_rule_ids``."""
        return sum(1 for d in self.decisions if d.decision == "decor")

    @property
    def projected_saved_calls(self) -> int:
        """Informational projection: the number of DISTINCT
        ``(asset_id, item_id, context_hash)`` tuples on which
        AT LEAST ONE candidate rule's predicate fired. This is
        what the rule set WOULD save (in calls) if every
        candidate rule were approved and we counted each
        candidate at most once.

        This is NOT ``sum(candidate_hits.values())``: if two
        rules fire on the same candidate, the candidate is
        counted once here, twice in ``candidate_hits``. The
        candidate-level view is what matters for cost: skipping
        a candidate once is one saved call, regardless of how
        many rules would have classified it as ``decor``.

        Reported as an informational number only; it does NOT
        change actual decisions and it is independent of the
        cluster layer."""
        return len(self._candidate_fired_rules)

    @property
    def candidate_fired_rule_ids(
        self,
    ) -> tuple[tuple[tuple[str, str, str], frozenset[str]], ...]:
        """Read-only, deterministically-ordered view of the
        per-candidate fired-rule set. Each entry is a
        ``(key, rule_ids)`` pair where ``key`` is
        ``(asset_id, item_id, context_hash)`` and ``rule_ids``
        is a ``frozenset`` of rule_ids whose predicate fired
        on that candidate. The outer tuple is sorted by key
        ascending (UTF-8 byte ordering) so two runs over the
        same candidate list produce structurally identical
        tuples. Useful for the persistence layer to show
        "which rules flagged this candidate" without
        recomputing.
        """
        return self._candidate_fired_rules


def run_pre_filter(
    candidates: Sequence[Candidate],
    config: PreFilterConfig | None = None,
    *,
    approved_rule_ids: Sequence[str] = (),
) -> PreFilterRunResult:
    """Walk every candidate, apply every enabled rule, return a result.

    Pure function: no I/O, no provider calls, no workspace access.
    Each candidate is decided by the **first** rule that fires;
    remaining enabled rules are NOT consulted for that candidate.
    This is consistent with the design's "two outputs only"
    invariant — a candidate is either classified by a single rule
    or deferred.

    The set of rules consulted is the intersection of
    ``config.enabled_rules`` and the rules actually implemented
    here. Any enabled rule_id that has no implementation here is
    silently skipped (this should not happen in production; tests
    in ``tests/test_figure_role_prefilter.py`` assert it does not).

    ``approved_rule_ids`` is a runtime parameter that gates
    which rules are allowed to actually emit a ``decor`` decision.
    It is NOT part of ``PreFilterConfig`` and does NOT participate
    in ``rules_config_hash``. It defaults to an empty tuple, which
    means **no rule is approved**: every rule that would otherwise
    fire will instead produce a ``defer`` decision (the rule's
    `hits` are still recorded in `rule_stats` so the reviewer
    can see what the rule would have caught). This is the safe
    default required by the design freeze: candidate rules must
    NOT auto-emit ``decor`` until a human review approves them
    by name.

    The ``approved_rule_ids`` set is also NOT inherited from a
    previous report or persisted state. Each dry-run call must
    pass the approved set explicitly. The persistence layer
    (added in a later commit) is the one place that maps a
    reviewer's previous approval to the next run's
    ``approved_rule_ids`` argument.
    """
    if config is None:
        config = PreFilterConfig.default()

    rules_by_id: dict[str, RuleConfig] = {r.rule_id: r for r in config.rules}
    approved = frozenset(approved_rule_ids)

    decisions: list[PreFilterDecision] = []
    rule_stats: dict[str, RuleStats] = {
        rid: RuleStats(rule_id=rid) for rid in rules_by_id
    }
    decor_tuples_set: set[tuple[str, str, str]] = set()
    decor_rule_ids: dict[tuple[str, str, str], list[str]] = {}
    # candidate-level view: for each (asset, item/context) key,
    # the set of rule_ids whose predicate fired. Used to compute
    # `projected_saved_calls` as the COUNT OF DISTINCT KEYS, not
    # the sum of per-rule hits. Overlapping rules on the same
    # candidate MUST NOT double-count.
    candidate_fired_rules: dict[tuple[str, str, str], set[str]] = {}

    for candidate in candidates:
        decided = False
        for enabled_rid in config.enabled_rules:
            if enabled_rid not in rules_by_id:
                continue  # silently skip unknown rule_id
            rcfg = rules_by_id[enabled_rid]
            rule_fn = _RULE_DISPATCH.get(enabled_rid)
            if rule_fn is None:
                continue
            decision = rule_fn(candidate, rcfg)
            if decision is None:
                # Rule did not fire on this candidate (its predicate
                # returned None). Count it as a defer for stats.
                rule_stats[enabled_rid] = RuleStats(
                    rule_id=rule_stats[enabled_rid].rule_id,
                    hits=rule_stats[enabled_rid].hits,
                    defers=rule_stats[enabled_rid].defers + 1,
                )
                continue
            # Rule fired. The candidate would be classified as
            # `decor` IF the rule is approved. Otherwise it is
            # recorded as a "would-have-fired" hit (stats-wise)
            # and the candidate is deferred. The rule is consulted
            # for ranking (which rule fires first) but the final
            # decision respects the approval gate.
            rule_stats[enabled_rid] = RuleStats(
                rule_id=rule_stats[enabled_rid].rule_id,
                hits=rule_stats[enabled_rid].hits + 1,
                defers=rule_stats[enabled_rid].defers,
            )
            # Record candidate-level fired-rule set, regardless of
            # approval. This is the basis for `projected_saved_calls`.
            key = (candidate.asset_id, candidate.item_id, candidate.context_hash)
            candidate_fired_rules.setdefault(key, set()).add(enabled_rid)
            if enabled_rid in approved:
                decisions.append(decision)
                decor_tuples_set.add(key)
                decor_rule_ids.setdefault(key, []).append(enabled_rid)
                decided = True
                break  # first approved rule wins
            # Not approved → record the would-have-fired hit, but
            # keep looking at later rules. This lets a later
            # approved rule still produce a decor decision.
            # If no later rule fires, the defer below handles it.

        if not decided:
            decisions.append(
                PreFilterDecision(
                    asset_id=candidate.asset_id,
                    item_id=candidate.item_id,
                    context_hash=candidate.context_hash,
                    decision="defer",
                    rule_id="",
                    evidence=(),
                    reason="no approved rule produced a decor decision",
                )
            )

    # Stable ordering of decisions: preserve the input candidate
    # order. The unique_decor_tuples ordering is also stable
    # because Python dicts preserve insertion order and the
    # candidates are processed in input order.
    inputs_hash = compute_inputs_hash(candidates)
    rules_config_hash = compute_rules_config_hash(config)

    # Build the immutable, deterministically-ordered view of
    # the per-candidate fired-rule set. Sorted by candidate key
    # ascending (UTF-8 byte ordering) so two runs over the same
    # candidate list produce structurally identical tuples.
    sorted_fired = tuple(
        sorted(candidate_fired_rules.items(), key=lambda kv: kv[0])
    )
    frozen_fired = tuple((k, frozenset(v)) for k, v in sorted_fired)

    return PreFilterRunResult(
        decisions=tuple(decisions),
        rule_stats=rule_stats,
        candidates_total=len(candidates),
        unique_decor_tuples=tuple(decor_tuples_set),
        inputs_hash=inputs_hash,
        rules_config_hash=rules_config_hash,
        schema_version=config.schema_version,
        decor_rule_ids={k: tuple(v) for k, v in decor_rule_ids.items()},
        _candidate_fired_rules=frozen_fired,
    )


# ---------------------------------------------------------------------- #
# Asset content hashing helper
# ---------------------------------------------------------------------- #


def compute_asset_content_hash(path: str | Path) -> str:
    """SHA-256 of the asset's bytes, hex digest. Used for
    `Candidate.asset_content_hash`."""

    p = Path(path)
    data = p.read_bytes()
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_RULE_VERSIONS",
    "DEFAULT_TINY_AREA_PX",
    "DEFAULT_BANNER_AR_MAX",
    "DEFAULT_BANNER_AR_MIN",
    "DECOR_PHRASES",
    "Evidence",
    "Candidate",
    "PreFilterDecision",
    "RuleConfig",
    "PreFilterConfig",
    "RuleStats",
    "PreFilterRunResult",
    "compute_inputs_hash",
    "compute_rules_config_hash",
    "compute_asset_content_hash",
    "run_pre_filter",
]
