# Manual MinerU invocation

## Decision

MinerU is invoked **manually by the user** through its web or desktop
client. The pipeline never calls the MinerU HTTP API. After the user
downloads the MinerU output, they drop the artifact directory into a
fixed inbox path; the pipeline then loads it as a local folder.

## Why

- Removes an unstable external dependency from the preprocessing
  pipeline.
- Keeps the user's source file in their possession end-to-end.
- Lets the user pick whichever MinerU export format (Markdown, JSON,
  HTML, LaTeX, DOCX) is most useful for a given book.
- Pipeline stages stay deterministic and reproducible — the same
  inbox folder produces the same Stage 1 output every run.

## How

- Inbox root: `inbox/` (relative to the project root).
- Each MinerU task occupies one subdirectory named after the MinerU
  task ID or a user-chosen slug.
- `meta.json` is required; `full.md` is required; `layout.json`,
  `images/`, and `source.pdf` are recommended but optional.
- The loader auto-detects http(s) URLs, `file://` URLs, and MinerU-style
  `images/<filename>` relative paths.
- See `docs/PIPELINE.md` Stage 1 for the full contract.

## Trade-offs

- Adds a manual step before each run. Accepted because the user is the
  one who decides when to start processing and which pages to include.
- Requires the user to use a real MinerU export, not an API stub.
  Documented in `scripts/README.md`.