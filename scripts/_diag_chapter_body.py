import json

d = json.loads(open(r"projects/高思竞赛数学课本三年级/book_view/book_view.json", encoding="utf-8").read())
for aid in ("b7b2a07ee7cc", "a79d2e81ac89", "01c74cbec23b", "23e4690596fd"):
    for ch in d["chapters"]:
        for sec in ch.get("sections", []):
            for it in sec.get("items", []):
                for ar in it.get("asset_refs", []):
                    if ar["asset_id"] == aid:
                        body_len = len((it.get("text") or "").strip())
                        print(f"aid={aid}  chapter={ch['title']!r}  section={sec.get('title')!r}  item_type={it['item_type']!r}  body_len={body_len}")
