# Chapter-scoped stopwords on outline topics

**Date:** 2026-07-10
**Status:** Active
**Supersedes:** —

## Context

The leaf-level `negative_keywords` / `negative_patterns` contract
delivered in outline v1.0.1 suppresses a leaf entirely when an
unambiguous "this is not my topic" signal appears in the item
text. It is the right tool for *disambiguating* leaves but the
wrong tool for *common prose noise* — substrings that appear in
**every** item of a given chapter without telling you anything
about which leaf the item belongs to (e.g. `本章主要学习`,
`考点`, `专题一`, `知识梳理`). These shared tokens inflate the
positive keyword count of every leaf in the chapter, producing
false multi-topic bundles when only the low-information token
is shared.

## Decision

Introduce a new optional field on outline `topic` nodes:

- `chapter_stopwords: tuple[str, ...]`

The values are inherited top-down by every descendant leaf and
applied by `OutlineMatcher._score` as a **keyword-scrub only**:

1. For each leaf, the matcher gathers the union of `chapter_stopwords`
   declared by the leaf itself and every ancestor.
2. Before the keyword substring check, the matcher's local copy
   of `item.searchable` has each stopword occurrence replaced
   with the same number of spaces (so byte offsets stay
   meaningful — patterns still see the original text).
3. Patterns continue to run on the *un*scrubbed text. This keeps
   `chapter_stopwords` low-risk: it can only **remove** a
   keyword hit, never add one, and it cannot affect regex-based
   matches.

The leaf-level `negative_keywords` and the chapter-scoped
`chapter_stopwords` are deliberately separate axes:

- `negative_keywords` answers "is this item's primary topic NOT my
  leaf?" → veto the leaf.
- `chapter_stopwords` answers "would this weak-positive keyword
  pollute my leaf because everyone in this chapter says it?"
  → scrub the keyword from the substring check.

`MatchDetail` gains a `chapter_stopwords_applied: list[str]` field
so the review report can show *which* stopwords fired.

## Consequences

- `Topic` dataclass grows `chapter_stopwords: tuple[str, ...] = ()`;
  `_parse_topic` round-trips it through YAML; `_scrub_stopwords`
  lives on `OutlineMatcher`.
- `Outline.chapter_stopwords_for(leaf_id)` returns the union of
  stopwords along the ancestor chain in top-down order.
- `schemas/outline.schema.json` documents the new field.
- `outlines/README.md` gains a "Chapter-scoped stopwords"
  subsection.
- Outline schema is unchanged at v1.0.1 → bumps to v1.0.2 once a
  real outline is shipped with stopwords.

## Alternatives considered

- **Scrub both keywords and patterns.** Rejected: regex patterns
  are typically hand-tuned around the very tokens this feature
  would remove. Silencing them would risk regressing genuine
  matches whenever authors tune stopword lists.
- **Per-leaf stopwords on `VocabularyEntry`.** Rejected: the
  whole point of this feature is that the same noise is shared
  across the chapter; duplicating the list on every leaf is
  busy-work.
- **Auto-derived stopwords from the matched candidates.**
  Rejected for v1: works only as a post-hoc analysis tool (it
  needs existing assignments to know what's "shared"). It would
  not help unpublished outlines. Could be added later as a
  separate `OutlineAuditor` that *suggests* stopwords the author
  can then promote to the YAML.
