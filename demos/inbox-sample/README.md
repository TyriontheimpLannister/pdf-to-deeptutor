# inbox-sample

Synthetic MinerU output used to exercise Stages 1-3 of the pipeline.

This is a fixture, not a real MinerU export. The image URLs in `full.md`
and `layout.json` are intentionally fake (`https://mineru.example/tmp/...`)
so that Stage 2 asset localization can be tested without contacting any
real service.

## Layout

```text
inbox-sample/
├── README.md                       ← this file
└── g8-triangle-ch03/               ← one MinerU task fixture
    ├── meta.json                   ← required: task metadata
    ├── full.md                     ← required: Markdown main product
    ├── layout.json                 ← optional but recommended: structured blocks
    ├── images/                     ← MinerU-provided image cache (also fake URLs)
    │   ├── img_p001_001.png
    │   ├── img_p002_001.png
    │   ├── img_p003_001.png
    │   └── img_p004_001.png
    └── source.pdf.placeholder.txt  ← not a real PDF; see file for details
```

## What this fixture exercises

- **Stage 1 inbox validation**: a complete task with both `full.md` and
  `layout.json`. `meta.json` declares both products under `products`.
- **Stage 2 asset localization**: four remote image URLs that must be
  downloaded and rewritten to local asset IDs.
- **Stage 3 normalization**: headings, definitions, theorems, worked
  examples, exercises, solutions, chapter summary — a complete type mix.
- **Stage 4 misc fallback**: the "鸡兔同笼" line at the end of the
  chapter is intentionally off-topic. When run with a topic outline
  (e.g. elementary-math-v1.yaml), this content is routed to the
  `_misc` export, exercising the default fallback strategy.

## Using this fixture

Treat it like a real MinerU export. Drop it into `inbox/` if you want the
loader to pick it up, or point the loader at it directly:

```bash
math-pp run --inbox inbox-sample/g8-triangle-ch03 \
            --project demo-g8-triangle \
            --mode B
```

## Replacing with a real MinerU export

When a real MinerU export becomes available:

1. Replace `full.md`, `layout.json`, and the `images/` cache with the
   real files.
2. Rewrite `meta.json` > `source.sha256` after computing it from the
   real PDF.
3. Replace `source.pdf.placeholder.txt` with the actual PDF.
4. Re-run Stage 1; the loader's fingerprint check will see new hashes
   and reprocess automatically.