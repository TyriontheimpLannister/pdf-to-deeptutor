"""Find figures that look visually like known banner templates.

Use a perceptual hash (dhash 16x16) on a known banner reference and
find all images within hamming distance 12-15 that are likely
banner-style decorations.
"""
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(r"projects/高思竞赛数学课本三年级")
ASSETS = ROOT / "assets"
registry = json.loads((ROOT / "normalized/assets_registry.json").read_text(encoding="utf-8"))


def dhash(path: Path, hash_size: int = 16) -> int:
    with Image.open(path) as im:
        im = im.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(im.getdata())
    h = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            if left > right:
                h |= 1 << (row * hash_size + col)
    return h


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# Use a few known template-banners as references
# We know these from the user's description: "练习" "作业" "本讲" banners
references = {
    "b7b2a07ee7cc": "练习 banner (pencil+cartoon)",
    "0a0190348dd3": "作业 banner (mail icon)",
    "4120a71c4fbc": "忍者头像 (decor icon)",
}

ref_hashes: dict[str, int] = {}
for aid, desc in references.items():
    p = ASSETS / f"{aid}.jpg"
    if not p.is_file():
        p = ROOT / "assets" / f"{aid}.jpg"
    if p.is_file():
        ref_hashes[aid] = dhash(p)
        print(f"reference: {aid}  desc={desc}")

# Search all assets
print()
print("Searching all assets within hamming distance <= 18 of each reference:")
all_hashes: dict[str, int] = {}
for entry in registry["assets"]:
    aid = entry["asset_id"]
    p = ASSETS / f"{aid}.jpg"
    if not p.is_file():
        continue
    try:
        all_hashes[aid] = dhash(p)
    except Exception:
        pass

# Threshold 18 (out of 256) — finds similar but not identical banners
THRESHOLD = 18
seen: set[str] = set()
for ref_aid, ref_h in ref_hashes.items():
    matches = []
    for aid, h in all_hashes.items():
        if aid in references:
            continue
        d = hamming(ref_h, h)
        if d <= THRESHOLD:
            matches.append((d, aid))
    matches.sort()
    print(f"\n{references[ref_aid]} (ref={ref_aid}):")
    for d, aid in matches[:20]:
        if aid in seen:
            continue
        seen.add(aid)
        size = ""
        for e in registry["assets"]:
            if e["asset_id"] == aid:
                size = f"{e.get('width', '?')}x{e.get('height', '?')}"
                break
        print(f"  hamming={d:3d}  {aid}  {size}")
