# Scripts

Helper entry points for the MVP pipeline. Each script is runnable directly
from the project root.

## init_inbox_meta.py

Auto-generate `meta.json` for a MinerU task directory that the user just
dropped into the inbox.

### Usage

```bash
# 1. Drop MinerU output under inbox/<book-id>/
mkdir -p inbox/学之舟-总复习
cp /path/to/MinerU-export/full.md        inbox/学之舟-总复习/
cp /path/to/MinerU-export/layout.json    inbox/学之舟-总复习/
cp -r /path/to/MinerU-export/images      inbox/学之舟-总复习/
cp /path/to/学之舟小学生知识通-数学.pdf inbox/学之舟-总复习/source.pdf

# 2. Generate meta.json
python scripts/init_inbox_meta.py \
    --inbox-dir inbox/学之舟-总复习 \
    --original-filename "学之舟小学生知识通-数学.pdf" \
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
    --inbox inbox/学之舟-总复习 \
    --project-root projects/学之舟-总复习 \
    --project-id 学之舟-总复习 \
    --title "学之舟小学生知识通-数学" \
    --downloader http
```

`--downloader local` exists for fixture-based testing; for real MinerU
output always use `--downloader http`.