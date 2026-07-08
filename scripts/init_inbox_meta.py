"""Initialize a meta.json for a MinerU task dropped into the inbox.

You drop the MinerU output into ``inbox/<book-id>/``, then run this script
to auto-generate ``meta.json``. It will:

  - compute SHA-256 of source.pdf if present;
  - discover full.md, layout.json, images/, etc.;
  - fill a draft meta.json that the loader can validate.

Usage:

    python scripts/init_inbox_meta.py \\
        --inbox-dir inbox/<book-id> \\
        --original-filename "学之舟小学生知识通-数学.pdf" \\
        [--task-id <book-id>] \\
        [--mineru-version "MinerU-VLM-2.x"]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


_KNOWN_PRODUCT_NAMES = {
    "full.md": "markdown",
    "layout.json": "layout_json",
    "middle.json": "middle_json",  # legacy alias
    "full.html": "html",
    "full.tex": "latex",
    "main.tex": "latex",
    "result.docx": "docx",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_count_hint(task_dir: Path) -> int | None:
    """Best-effort page count: prefer PDF, fall back to layout.json length."""
    pdf = task_dir / "source.pdf"
    if pdf.is_file():
        try:
            import pypdf

            reader = pypdf.PdfReader(str(pdf))
            return len(reader.pages)
        except Exception:
            pass
    layout = task_dir / "layout.json"
    if layout.is_file():
        try:
            data = json.loads(layout.read_text(encoding="utf-8"))
            pages = data.get("pages")
            if isinstance(pages, list):
                return len(pages)
        except Exception:
            pass
    return None


def _slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", name).strip("-").lower()
    return s or "task"


def _build_meta(args, task_dir: Path) -> dict:
    # Discover product files
    products: dict[str, str] = {}
    for filename, product_key in _KNOWN_PRODUCT_NAMES.items():
        candidate = task_dir / filename
        if candidate.is_file():
            products[product_key] = filename

    # Source PDF: real or placeholder
    source_pdf = task_dir / "source.pdf"
    placeholder = task_dir / "source.pdf.placeholder.txt"
    has_real_pdf = source_pdf.is_file()
    if has_real_pdf:
        source_sha = _sha256_file(source_pdf)
        page_count = _page_count_hint(task_dir)
    elif placeholder.is_file():
        # Synthetic / fixture sentinel so the loader does not error out.
        source_sha = "0" * 63 + "1"
        page_count = _page_count_hint(task_dir)
    else:
        # No source at all — write a sentinel so the schema is satisfied.
        # The validation report will still warn about the missing source.
        source_sha = "0" * 63 + "2"
        page_count = _page_count_hint(task_dir)

    task_id = args.task_id or _slugify(args.original_filename.rsplit(".", 1)[0])

    meta = {
        "task_id": task_id,
        "source": {
            "original_filename": args.original_filename,
            "sha256": source_sha,
            "page_count": page_count,
        },
        "minerU": {
            "version": args.mineru_version,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "products": products,
    }

    if not has_real_pdf and not placeholder.is_file():
        meta.setdefault("notes", "source.pdf missing; loader will warn.")

    return meta


def main() -> int:
    p = argparse.ArgumentParser(description="Generate meta.json for an inbox task.")
    p.add_argument("--inbox-dir", required=True, help="Path to inbox/<book-id>/")
    p.add_argument("--original-filename", required=True, help="e.g. 教辅书.pdf")
    p.add_argument("--task-id", default=None, help="Override task_id (default: slug of filename)")
    p.add_argument(
        "--mineru-version",
        default="MinerU-VLM-2.x",
        help="MinerU version label",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing meta.json instead of bailing out.",
    )
    args = p.parse_args()

    task_dir = Path(args.inbox_dir)
    if not task_dir.is_dir():
        print(f"ERROR: inbox dir does not exist: {task_dir}", file=sys.stderr)
        return 2

    meta_path = task_dir / "meta.json"
    if meta_path.exists() and not args.overwrite:
        print(f"ERROR: {meta_path} already exists (use --overwrite to replace)", file=sys.stderr)
        return 2

    meta = _build_meta(args, task_dir)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK wrote {meta_path}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())