"""Tests for the Phase 1.5 pre-filter pure core.

These tests pin down the **standalone** behaviour of
:mod:`pdf2dt.figure_roles.pre_filter`: per-rule decisions,
immutable data structures, deterministic hash serialisation,
candidate-level granularity, and the dry-run runner's
non-invocation of any provider. They do NOT touch
:mod:`pdf2dt.review.figure_roles` or any persistence / CLI
concerns; those are added in later commits.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from pdf2dt.figure_roles.pre_filter import (
    Candidate,
    PreFilterConfig,
    PreFilterDecision,
    PreFilterRunResult,
    RuleConfig,
    compute_asset_content_hash,
    compute_inputs_hash,
    compute_rules_config_hash,
    run_pre_filter,
)

# The P1.5 catalogue (per the design document §4) uses stable IDs
# of the form ``P1.5-Tn``. Pytest's default collection only
# discovers functions whose name starts with ``test_``. We
# register the ``P1.5-Tn``-named functions explicitly via the
# module-level ``__test__`` dict, which is the documented pytest
# mechanism for collecting non-``test_``-prefixed functions
# without renaming them.
#
# Note: ``__test__`` is filled at the bottom of this file
# (after all test functions are defined). The forward reference
# here is intentional; pytest reads the module's globals at
# collection time, so by the time pytest inspects ``__test__``
# all the named functions exist.



# ---------------------------------------------------------------------- #
# Image builders
# ---------------------------------------------------------------------- #


def _write_image(path: Path, width: int, height: int, color: str = "white") -> None:
    Image.new("RGB", (width, height), color=color).save(path, "PNG")


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _make_candidate(
    tmp_path: Path,
    *,
    asset_id: str = "asset-1",
    item_id: str = "item-1",
    context_hash: str = "ctx-1",
    context_text: str = "",
    width: int = 400,
    height: int = 300,
    color: str = "white",
) -> Candidate:
    p = tmp_path / f"{asset_id}_{item_id}_{context_hash}.png"
    _write_image(p, width, height, color=color)
    return Candidate(
        asset_id=asset_id,
        item_id=item_id,
        context_hash=context_hash,
        context_text=context_text,
        width=width,
        height=height,
        asset_content_hash=compute_asset_content_hash(p),
    )


# ---------------------------------------------------------------------- #
# Constants
# ---------------------------------------------------------------------- #


# All three candidate rules approved. Tests that need an
# approved rule to actually emit `decor` pass this constant.
ALL_RULES_APPROVED = (
    "decor_phrase_in_context",
    "tiny_icon_size",
    "extreme_aspect_ratio",
)


# ---------------------------------------------------------------------- #
# P1.5-T1..T6 — Per-rule output (covers §2.2)
# ---------------------------------------------------------------------- #


def test_p1_5_t1_decor_phrase_in_context_flags_decor(tmp_path: Path) -> None:
    c = _make_candidate(tmp_path, context_text="本资源来自微信公众号 教辅资料站")
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    decor = [d for d in result.decisions if d.decision == "decor"]
    assert len(decor) == 1
    d = decor[0]
    assert d.rule_id == "decor_phrase_in_context"
    assert d.evidence
    matched = [e for e in d.evidence if e.key == "matched_phrase"]
    assert len(matched) == 1
    assert matched[0].value == "微信公众号 教辅资料站"


def test_p1_5_t2_decor_phrase_absent_defers(tmp_path: Path) -> None:
    c = _make_candidate(tmp_path, context_text="函数 y = 2x + 1 的图像")
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    d = result.decisions[0]
    assert d.decision == "defer"
    assert d.rule_id == ""
    assert d.evidence == ()


def test_p1_5_t3_tiny_icon_flags_decor(tmp_path: Path) -> None:
    c = _make_candidate(tmp_path, width=64, height=64, context_text="math content")
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    # No decor phrase → tiny_icon_size fires.
    d = result.decisions[0]
    assert d.decision == "decor"
    assert d.rule_id == "tiny_icon_size"
    ev = {e.key: e.value for e in d.evidence}
    assert ev["width"] == 64
    assert ev["height"] == 64
    assert ev["area_px"] == 4096


def test_p1_5_t4_large_icon_defers(tmp_path: Path) -> None:
    c = _make_candidate(tmp_path, width=800, height=600, context_text="math content")
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    d = result.decisions[0]
    assert d.decision == "defer"
    assert d.evidence == ()


def test_p1_5_t5_extreme_aspect_ratio_flags_decor(tmp_path: Path) -> None:
    c = _make_candidate(
        tmp_path, width=1000, height=100, context_text="math content"
    )
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    d = result.decisions[0]
    assert d.decision == "decor"
    assert d.rule_id == "extreme_aspect_ratio"
    ev = {e.key: e.value for e in d.evidence}
    assert ev["width"] == 1000
    assert ev["height"] == 100
    assert abs(ev["aspect_ratio"] - 10.0) < 1e-9


def test_p1_5_t6_normal_aspect_defers(tmp_path: Path) -> None:
    c = _make_candidate(
        tmp_path, width=400, height=300, context_text="math content"
    )
    result = run_pre_filter([c], approved_rule_ids=ALL_RULES_APPROVED)
    d = result.decisions[0]
    assert d.decision == "defer"
    assert d.evidence == ()


# ---------------------------------------------------------------------- #
# P1.5-T7..T9 — Aggregation
# ---------------------------------------------------------------------- #


def test_p1_5_t7_overlapping_rules_dedupe_at_tuple_level(tmp_path: Path) -> None:
    """Two rules both fire on the same candidate → the tuple
    appears exactly once in `unique_decor_tuples`. The per-rule
    `hits` are still independent because we count decisions per
    rule; the union at the tuple level is what `unique_decor_tuples`
    represents. With the current "first rule wins" runner, only
    one rule records a hit per candidate. We exercise that:
    decor_phrase_in_context fires first, tiny_icon_size does not
    get a chance. Per-rule hits: 1 for the phrase rule, 0 for
    size. `unique_decor_tuples` has length 1.

    Then a SECOND candidate that does NOT match the phrase rule
    but DOES match tiny_icon_size also exists. The two candidates
    together have `unique_decor_tuples` of length 2.
    """
    c1 = _make_candidate(
        tmp_path,
        asset_id="a1",
        item_id="i1",
        context_hash="c1",
        context_text="微信公众号 教辅资料站",
        width=64,
        height=64,
    )
    c2 = _make_candidate(
        tmp_path,
        asset_id="a2",
        item_id="i2",
        context_hash="c2",
        context_text="normal text",
        width=64,
        height=64,
    )
    result = run_pre_filter([c1, c2], approved_rule_ids=ALL_RULES_APPROVED)
    assert len(result.unique_decor_tuples) == 2
    # c1 was classified by decor_phrase_in_context (first rule);
    # c2 was classified by tiny_icon_size (second rule, since
    # the phrase rule did not fire).
    rules = {tuple_[-1] for tuple_ in result.decor_rule_ids.values()}
    assert {"decor_phrase_in_context", "tiny_icon_size"} == rules
    # Sanity: a single candidate classified by the same tuple
    # twice in a row would still appear once in unique_decor_tuples.
    solo = run_pre_filter([c1, c1], approved_rule_ids=ALL_RULES_APPROVED)
    assert len(solo.unique_decor_tuples) == 1

    # Default rejection: no approved rules → no decor decisions,
    # but per-rule `hits` are still recorded so the reviewer can
    # see what each rule would have caught. Without the approval
    # gate, every rule that fires on each candidate is recorded;
    # c1 is 64x64 so it fires BOTH `decor_phrase_in_context` and
    # `tiny_icon_size`, c2 (64x64, no phrase) fires only
    # `tiny_icon_size`.
    no_approval = run_pre_filter([c1, c2])
    assert all(d.decision == "defer" for d in no_approval.decisions)
    assert no_approval.unique_decor_tuples == ()
    assert no_approval.rule_stats["decor_phrase_in_context"].hits == 1
    assert no_approval.rule_stats["tiny_icon_size"].hits == 2
    assert no_approval.rule_stats["extreme_aspect_ratio"].hits == 0
    # Distinguish candidate hits from approved outputs:
    # `approved_decor_decisions` is 0 (no rule approved), but
    # `projected_saved_calls` is the candidate-level distinct
    # count. It is NOT the sum of per-rule `candidate_hits`:
    # c1 fires BOTH phrase and tiny, but it is one candidate
    # and counts as one potential saved call, not two.
    # Per-rule `candidate_hits` (phrase=1, tiny=2) sum to 3,
    # but distinct candidate count is 2 (c1 and c2).
    assert no_approval.approved_decor_decisions == 0
    assert no_approval.candidate_hits == {
        "decor_phrase_in_context": 1,
        "tiny_icon_size": 2,
        "extreme_aspect_ratio": 0,
    }
    assert no_approval.projected_saved_calls == 2
    # Overlapping rules on the same candidate do NOT
    # double-count: c1 fired both phrase and tiny but shows
    # up once in `candidate_fired_rule_ids`.
    fired = no_approval.candidate_fired_rule_ids
    # The view is a tuple of (key, frozenset) pairs sorted by
    # key ascending. Index by position rather than `in dict`.
    fired_by_key = dict(fired)
    c1_key = ("a1", "i1", "c1")
    c2_key = ("a2", "i2", "c2")
    assert c1_key in fired_by_key
    assert c2_key in fired_by_key
    assert fired_by_key[c1_key] == frozenset(
        {"decor_phrase_in_context", "tiny_icon_size"}
    )
    assert fired_by_key[c2_key] == frozenset({"tiny_icon_size"})
    assert len(fired) == 2
    # The outer sequence is sorted ascending by key.
    assert [k for k, _ in fired] == [c1_key, c2_key]

    # Immutability contract: callers MUST NOT be able to
    # mutate the result through the property. The outer tuple
    # rejects item assignment; each inner frozenset rejects
    # add/update; the inner key tuple is itself a tuple.
    try:
        fired[0] = (("X",), frozenset())  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("candidate_fired_rule_ids outer tuple is mutable")
    inner_set = fired_by_key[c1_key]
    try:
        inner_set.add("some_new_rule")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    else:
        raise AssertionError("candidate_fired_rule_ids inner set is mutable")

    for mapping, label in (
        (no_approval.rule_stats, "rule_stats"),
        (no_approval.decor_rule_ids, "decor_rule_ids"),
        (no_approval.candidate_hits, "candidate_hits"),
    ):
        try:
            mapping["unexpected"] = None  # type: ignore[index,assignment]
        except TypeError:
            pass
        else:
            raise AssertionError(f"{label} mapping is mutable")


def test_p1_5_t8_report_aggregates_rule_stats(tmp_path: Path) -> None:
    c1 = _make_candidate(
        tmp_path, asset_id="a1", context_text="微信公众号", width=400, height=300
    )
    c2 = _make_candidate(
        tmp_path,
        asset_id="a2",
        context_text="normal",
        width=64,
        height=64,
    )
    c3 = _make_candidate(
        tmp_path,
        asset_id="a3",
        context_text="normal",
        width=400,
        height=300,
    )
    result = run_pre_filter(
        [c1, c2, c3], approved_rule_ids=ALL_RULES_APPROVED
    )
    stats = result.rule_stats
    assert stats["decor_phrase_in_context"].hits == 1
    # c1 → phrase rule fires, then loop breaks. So tiny_icon_size
    # and extreme_aspect_ratio see only c2 and c3.
    # c2: tiny_icon_size fires (64*64=4096 < 10000), so the other
    # rules are NOT consulted for c2.
    # c3: nothing fires → defer.
    assert stats["tiny_icon_size"].hits == 1
    assert stats["extreme_aspect_ratio"].hits == 0
    # Every rule entry in `rule_stats` carries
    # `ground_truth_comparison.status == "unverified"`.
    for s in stats.values():
        assert s.to_dict()["ground_truth_comparison"]["status"] == "unverified"

    # Explicit partial approval: only the phrase rule is
    # approved. tiny_icon_size still records `hits` (on c2),
    # extreme_aspect_ratio does not (c3 is 400x300, normal
    # aspect). The only decor decision comes from the phrase
    # rule on c1.
    partial = run_pre_filter(
        [c1, c2, c3],
        approved_rule_ids=("decor_phrase_in_context",),
    )
    decor = [d for d in partial.decisions if d.decision == "decor"]
    assert len(decor) == 1
    assert decor[0].rule_id == "decor_phrase_in_context"
    assert partial.rule_stats["tiny_icon_size"].hits == 1
    assert partial.rule_stats["extreme_aspect_ratio"].hits == 0

    # A second call with the same candidates and NO approval
    # must defer everything, proving the approval is per-call
    # and not inherited from any prior call. rules_config_hash
    # is unchanged between the two calls. `candidate_hits`
    # are also unchanged (the rule predicates are independent
    # of approval), but `approved_decor_decisions` flips from
    # 1 (partial) to 0 (no approval) — the gate is the only
    # thing that differs.
    no_approval = run_pre_filter([c1, c2, c3])
    assert all(d.decision == "defer" for d in no_approval.decisions)
    assert no_approval.rules_config_hash == partial.rules_config_hash
    assert no_approval.approved_decor_decisions == 0
    assert no_approval.candidate_hits == partial.candidate_hits
    assert no_approval.projected_saved_calls == partial.projected_saved_calls


def test_p1_5_t9_no_decision_carries_pseudo_confidence(tmp_path: Path) -> None:
    c = _make_candidate(tmp_path, context_text="微信公众号", width=64, height=64)
    result = run_pre_filter([c])

    forbidden = ("confidence", "score")
    threshold_strings = (">= 0.85", ">=0.85", "0.85")

    # The dataclass source must not mention `confidence` or `score`.
    import inspect

    from pdf2dt.figure_roles import pre_filter as pf_mod

    src = inspect.getsource(pf_mod)
    for needle in forbidden:
        assert needle not in src, (
            f"pre_filter.py source mentions forbidden string {needle!r}"
        )
    for s in threshold_strings:
        assert s not in src, (
            f"pre_filter.py source mentions forbidden threshold {s!r}"
        )

    # The decision payload and the result must not contain them.
    for d in result.decisions:
        d_json = json.dumps(d.to_dict(), sort_keys=True, ensure_ascii=False)
        for needle in forbidden:
            assert needle not in d_json
    result_json = json.dumps(
        {
            "rule_stats": {k: v.to_dict() for k, v in result.rule_stats.items()},
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    for needle in forbidden:
        assert needle not in result_json


# ---------------------------------------------------------------------- #
# P1.5-T10..T12 — Safety (covers §2.3, §6 Tier 1.5)
# ---------------------------------------------------------------------- #


def test_p1_5_t10_dry_run_does_not_invoke_provider(tmp_path: Path) -> None:
    """The pre-filter runner must not import or touch any provider
    module. We assert this by checking the module's source does
    not import from `pdf2dt.geometry.vlm` (the VLM provider
    layer)."""
    import pdf2dt.figure_roles.pre_filter as pf_mod

    src = pf_mod.__file__
    assert src is not None
    src_text = Path(src).read_text(encoding="utf-8")
    for needle in ("from pdf2dt.geometry.vlm", "import vlm"):
        assert needle not in src_text, (
            f"pre_filter.py imports VLM provider: {needle!r}"
        )

    # A run completes without raising even when the candidate
    # has only string fields (no on-disk file involved).
    c = Candidate(
        asset_id="a",
        item_id="i",
        context_hash="c",
        context_text="微信公众号",
        width=64,
        height=64,
        asset_content_hash="0" * 64,
    )
    result = run_pre_filter([c])
    assert isinstance(result, PreFilterRunResult)
    assert result.candidates_total == 1


def test_p1_5_t11_cluster_audit_does_not_change_savings_pct(tmp_path: Path) -> None:
    """The pure core has no concept of cluster_audit; the runner
    never imports or consults the cluster layer. We assert this
    by checking that `PreFilterRunResult` does not carry a
    cluster_audit field at all. (The persistence layer added in
    a later commit will carry `cluster_audit` separately; the
    pure core deliberately omits it.)"""
    c = _make_candidate(tmp_path, context_text="微信公众号", width=64, height=64)
    result = run_pre_filter([c])
    assert not hasattr(result, "cluster_audit")
    # The hash fields do NOT depend on any cluster state.
    h1 = compute_inputs_hash([c])
    h2 = compute_inputs_hash([c])
    assert h1 == h2


def test_p1_5_t12_legacy_workspace_without_assets_registry_defers_all(tmp_path: Path) -> None:
    """A candidate that has empty context_text and normal
    dimensions must defer (no rule fires). This models the
    fallback when the assets_registry is absent: the runner
    never infers 'decor' from absence of metadata."""
    c = _make_candidate(tmp_path, context_text="", width=400, height=300)
    result = run_pre_filter([c])
    d = result.decisions[0]
    assert d.decision == "defer"
    assert d.rule_id == ""
    assert d.evidence == ()
    assert result.rule_stats["decor_phrase_in_context"].hits == 0
    assert result.rule_stats["tiny_icon_size"].hits == 0
    assert result.rule_stats["extreme_aspect_ratio"].hits == 0


# ---------------------------------------------------------------------- #
# P1.5-T13..T15 — Granularity & persistence-shape
# ---------------------------------------------------------------------- #


def test_p1_5_t13_inputs_hash_deterministic_and_canonical(tmp_path: Path) -> None:
    """Re-running on the same inputs MUST produce the same
    `inputs_hash`. Different orderings of the same candidates
    MUST also produce the same hash (because the hash is over
    the sorted list). Changing a candidate's context_text MUST
    change the hash. Changing a candidate's bytes (and thus
    `asset_content_hash`) MUST change the hash."""
    c1 = _make_candidate(
        tmp_path,
        asset_id="a1",
        item_id="i1",
        context_hash="c1",
        context_text="math content",
        width=400,
        height=300,
    )
    c2 = _make_candidate(
        tmp_path,
        asset_id="a2",
        item_id="i2",
        context_hash="c2",
        context_text="other content",
        width=500,
        height=400,
    )
    h1 = compute_inputs_hash([c1, c2])
    h2 = compute_inputs_hash([c2, c1])  # different input order
    assert h1 == h2

    # Change c1's context_text and re-hash; must differ.
    c1b = Candidate(
        asset_id=c1.asset_id,
        item_id=c1.item_id,
        context_hash=c1.context_hash,
        context_text="DIFFERENT",
        width=c1.width,
        height=c1.height,
        asset_content_hash=c1.asset_content_hash,
    )
    h3 = compute_inputs_hash([c1b, c2])
    assert h3 != h1

    # Change c1's bytes (new content_hash) and re-hash; must differ.
    c1c = Candidate(
        asset_id=c1.asset_id,
        item_id=c1.item_id,
        context_hash=c1.context_hash,
        context_text=c1.context_text,
        width=c1.width,
        height=c1.height,
        asset_content_hash="f" * 64,  # fake but different
    )
    h4 = compute_inputs_hash([c1c, c2])
    assert h4 != h1


def test_p1_5_t14_decision_keys_on_asset_item_context_tuple(tmp_path: Path) -> None:
    """The same `asset_id` referenced by two different `item_id`s
    yields two distinct `PreFilterDecision` records with the
    same `asset_id` but different `(item_id, context_hash)`."""
    c1 = _make_candidate(
        tmp_path, asset_id="shared-asset", item_id="item-A", context_hash="cA"
    )
    c2 = _make_candidate(
        tmp_path, asset_id="shared-asset", item_id="item-B", context_hash="cB"
    )
    result = run_pre_filter([c1, c2])
    assert len(result.decisions) == 2
    ids = {(d.asset_id, d.item_id, d.context_hash) for d in result.decisions}
    assert ids == {
        ("shared-asset", "item-A", "cA"),
        ("shared-asset", "item-B", "cB"),
    }


def test_p1_5_t15_decision_never_collapses_to_asset_id(tmp_path: Path) -> None:
    """`PreFilterDecision` has no asset-only shortcut. Its
    `__post_init__` rejects empty `asset_id`, `item_id`, or
    `context_hash`."""
    # Empty asset_id is rejected.
    try:
        PreFilterDecision(
            asset_id="",
            item_id="i",
            context_hash="c",
            decision="defer",
            rule_id="",
            evidence=(),
            reason="",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty asset_id should be rejected")

    # Empty item_id is rejected at Candidate level.
    try:
        Candidate(
            asset_id="a",
            item_id="",
            context_hash="c",
            context_text="",
            width=10,
            height=10,
            asset_content_hash="0" * 64,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty item_id should be rejected")

    # Empty context_hash is rejected at Candidate level.
    try:
        Candidate(
            asset_id="a",
            item_id="i",
            context_hash="",
            context_text="",
            width=10,
            height=10,
            asset_content_hash="0" * 64,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty context_hash should be rejected")

    # Invalid `decision` value is rejected.
    try:
        PreFilterDecision(
            asset_id="a",
            item_id="i",
            context_hash="c",
            decision="content",  # type: ignore[arg-type]
            rule_id="",
            evidence=(),
            reason="",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("'content' decision must be rejected")

    # `decor` with empty evidence is rejected.
    try:
        PreFilterDecision(
            asset_id="a",
            item_id="i",
            context_hash="c",
            decision="decor",
            rule_id="tiny_icon_size",
            evidence=(),
            reason="",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("decor with empty evidence must be rejected")

    # `defer` with non-empty rule_id is rejected.
    try:
        PreFilterDecision(
            asset_id="a",
            item_id="i",
            context_hash="c",
            decision="defer",
            rule_id="tiny_icon_size",
            evidence=(),
            reason="",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("defer with non-empty rule_id must be rejected")


# ---------------------------------------------------------------------- #
# Bonus: rules_config_hash contract
# ---------------------------------------------------------------------- #


def test_rules_config_hash_stable_and_sensitive(tmp_path: Path) -> None:
    """Re-running with the same config MUST produce the same
    `rules_config_hash`. Changing `schema_version`, the
    threshold, the rule list, or the enabled set MUST change
    the hash."""
    cfg1 = PreFilterConfig.default()
    cfg2 = PreFilterConfig.default()
    assert compute_rules_config_hash(cfg1) == compute_rules_config_hash(cfg2)

    # Change schema_version.
    cfg3 = PreFilterConfig(
        schema_version="pre_filter_report/v2",
        rules=cfg1.rules,
        enabled_rules=cfg1.enabled_rules,
    )
    assert compute_rules_config_hash(cfg1) != compute_rules_config_hash(cfg3)

    # Drop a rule from enabled_rules.
    cfg4 = PreFilterConfig(
        schema_version=cfg1.schema_version,
        rules=cfg1.rules,
        enabled_rules=tuple(r for r in cfg1.enabled_rules if r != "tiny_icon_size"),
    )
    assert compute_rules_config_hash(cfg1) != compute_rules_config_hash(cfg4)

    # Bump a rule_version.
    bumped = tuple(
        RuleConfig(
            rule_id=r.rule_id,
            rule_version="v999" if r.rule_id == "tiny_icon_size" else r.rule_version,
            threshold=r.threshold,
        )
        for r in cfg1.rules
    )
    cfg5 = PreFilterConfig(
        schema_version=cfg1.schema_version,
        rules=bumped,
        enabled_rules=cfg1.enabled_rules,
    )
    assert compute_rules_config_hash(cfg1) != compute_rules_config_hash(cfg5)

    # human review state does NOT participate; this is checked by
    # the fact that `compute_rules_config_hash` takes only the
    # config and ignores any external review dict. (Sanity.)
    assert "approved_rules" not in PreFilterConfig.__dataclass_fields__
