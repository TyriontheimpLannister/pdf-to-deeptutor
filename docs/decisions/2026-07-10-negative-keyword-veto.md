# Negative-keyword veto on outline leaves

**Date:** 2026-07-10
**Status:** Active
**Supersedes:** —

## Context

The outline matcher scores each leaf by counting positive keyword /
pattern hits in an item's searchable text. When a chapter is a true
multi-topic summary (e.g. the geometry chapter's `12.5 小结` section)
it can legitimately mention one stray problem keyword from an
unrelated leaf (e.g. `鸡兔同笼`) while still belonging to the geometry
cluster. Pure positive scoring cannot distinguish "this section *is*
about chickens and rabbits" from "this section *mentions* chickens
and rabbits once". The first run of the matcher on the g8 fixture
showed this clearly: `typical-chickens-rabbits` fired on the geometry
summary because the keyword `鸡兔同笼` matched verbatim, dragging the
item out of its correct cluster.

The earlier handbook tracked this as a known limitation (HANDOFF.md
In Progress, item 2) and the corresponding pytest was marked `xfail`
to keep the aspiration visible without blocking CI.

## Decision

Extend `VocabularyEntry` with two optional fields:

- `negative_keywords`: literal substrings.
- `negative_patterns`: regular expressions.

If any negative rule matches the item's searchable text, the leaf is
**vetoed**: the score is floored below `min_score` so the entry never
enters the candidate set, even though the leaf's positive keywords
also hit. The vetoed evidence is recorded on the `MatchDetail`
(under `negative_keyword_hits` / `negative_pattern_hits`) so reviewers
can see *why* a leaf was suppressed, but vetoed details do not appear
on the surviving `TopicAssignment.match_details` because they were
not chosen.

The motivation is asymmetric: vetoing a leaf is much cheaper than
re-deriving a section's primary topic. The rules should therefore be
narrow (only terms that unambiguously indicate "this section is not
about my leaf") and should *never* include problem-only tokens
(e.g. `笼中有`), which would veto genuine leaf matches too.

## Consequences

- `outlines/elementary-math-v1.yaml` ships v1.0.1 with
  `typical-chickens-rabbits.negative_keywords` set to geometry
  discriminators (`三角形`, `全等`, `判定`, `直角`, `SAS`, `ASA`,
  `SSS`, `HL`).
- The previously xfail test
  `test_chicken_rabbit_item_should_go_to_misc_only` is replaced by
  `test_chicken_rabbit_vetoed_from_geometry_summary` (asserts the
  veto outcome) and `test_chapter_summary_still_classified_as_geometry`
  (asserts the geometry cluster still wins).
- `schemas/outline.schema.json` documents the two new fields;
  `required: ["keywords"]` was relaxed because a vocabulary entry may
  legitimately be veto-only (rare, but possible for `placeholder`
  leaves).
- `outlines/README.md` gains a "Negative keywords and patterns"
  subsection so future outline authors know the contract.

## Alternatives considered

- **Tighten the positive keyword set on `typical-chickens-rabbits`.**
  Rejected: the problem is that the *correct* geometry leaf needs to
  keep its current keywords; the chickens-rabbits keyword is already
  minimal. Tightening positive keywords would not help — `鸡兔同笼` is
  exactly what should match the leaf in genuine chickens-rabbits
  problems.
- **Introduce a section-level stopword list** (chapter-wide context).
  Rejected for v1: requires a second matcher pass that knows the
  chapter context. Negative keywords on the *leaf* get us 90% of the
  win with a one-line YAML change and zero matcher rewrite. A chapter
  context signal can be added later if more cases surface.
- **Hard-delete the xfail test.** Rejected: a regression test for the
  veto behaviour is exactly the artefact we want to keep.
