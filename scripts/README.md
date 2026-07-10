# Scripts

Helper entry points for the MVP pipeline. Each script is runnable directly
from the project root.

## init_inbox_meta.py

Auto-generate `meta.json` for a MinerU task directory that the user just
dropped into the inbox.

### Usage

```bash
# 1. Drop MinerU output under inbox/<book-id>/
mkdir -p inbox/my-book
cp /path/to/MinerU-export/full.md        inbox/my-book/
cp /path/to/MinerU-export/layout.json    inbox/my-book/
cp -r /path/to/MinerU-export/images      inbox/my-book/
cp /path/to/sample-document.pdf          inbox/my-book/source.pdf

# 2. Generate meta.json
python scripts/init_inbox_meta.py \
    --inbox-dir inbox/my-book \
    --original-filename "sample-document.pdf" \
    --mineru-version "MinerU-VLM-2.x"
```

### What it does

- Computes SHA-256 of `source.pdf` if present.
- Discovers `full.md`, `layout.json`, `images/` and fills the `products`
  block accordingly.
- Reads page count from PDF or layout.json.
- Writes `meta.json` next to the other files. Loader Stage 1 will accept
  it without further edits.

### Notes

- If `source.pdf` is missing, the script writes a sentinel SHA-256
  (`0000...0001`) so the loader does not block on the missing source.
  The validation report will warn that the source PDF is missing.
- `--task-id` is optional. Default is a slug of the filename stem.
- Use `--overwrite` to regenerate an existing `meta.json`.

## run_pipeline.py

End-to-end pipeline runner for Stages 0-2. See top-level `README.md` for
the full project description; this script is the normal entry point for
turning a MinerU task into a project workspace.

### Usage

```bash
python scripts/run_pipeline.py \
    --inbox inbox/my-book \
    --project-root projects/my-book \
    --project-id my-book \
    --title "My scanned book" \
    --downloader http
```

`--downloader local` exists for fixture-based testing; for real MinerU
output always use `--downloader http`.