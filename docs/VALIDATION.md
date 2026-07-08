# Validation

## Severity levels

- `error` — export is blocked.
- `warning` — export is allowed only after review or explicit override.
- `info` — improvement suggestion.

## Blocking checks

- Source file missing or checksum mismatch.
- Required MinerU output missing.
- Remote temporary image URL remains in final content.
- Referenced local asset missing.
- Image cannot be decoded.
- Export PDF cannot be opened.
- Export contains no selectable text.
- Required structured figure omitted from an item that depends on it.
- Unreviewed `visual_inference` promoted to a confirmed condition.

## Warning checks

- Formula delimiter imbalance.
- Suspicious OCR character sequences.
- Figure referenced by “如图” but no figure description is present.
- Figure has labels not mentioned in extracted structure.
- Text states a figure analysis relation not found in the structured data.
- One item spans an unusually large page range.
- Question and answer appear separated.
- Image resolution is too low for reliable inspection.
- Duplicate or near-duplicate figures exist.

## Informational checks

- Missing topic tags.
- Missing difficulty metadata.
- Missing source publication details.
- Export filename does not follow the naming convention.

## Export readiness

An export is ready when:

- no blocking errors remain;
- warnings are reviewed or explicitly accepted;
- all assets are local and embedded;
- native text is present;
- manifest checksums match generated files.

