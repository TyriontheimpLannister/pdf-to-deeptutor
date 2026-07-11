# Outline examples

Small, focused outlines used for **schema demonstrations and
end-to-end tests**. These are NOT real textbook outlines — do not
point a project at them.

| File | Demonstrates |
|---|---|
| `chapter-noise-fixture.yaml` | The `chapter_stopwords` topic field — the loader round-trip and matcher scrub chain is verified against this file by `tests/test_outline_loader.py::test_chapter_stopwords_end_to_end_against_shipped_fixture`. |

When you add a new outline schema field, mirror the workflow:
1. Add the field to `Topic` (or whichever dataclass owns it).
2. Extend `_parse_*` in `outline.py` and the JSON schema.
3. Add a fixture here that uses the field in a non-trivial shape.
4. Add an end-to-end test in `tests/test_outline_loader.py` that
   loads the fixture and exercises the downstream matcher / planner
   against it. Synthesising dataclasses in tests is fine for unit
   checks, but a real-YAML round-trip is the only thing that catches
   "field was added to Topic but not to _parse_topic".
