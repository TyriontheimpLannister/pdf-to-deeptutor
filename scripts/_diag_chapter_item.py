import json

d = json.loads(open(r"projects/高思竞赛数学课本三年级/book_view/book_view.json", encoding="utf-8").read())
for ch in d["chapters"]:
    if ch["title"] != "间隔问题":
        continue
    for sec in ch.get("sections", []):
        if sec.get("title") == "<root>":
            for it in sec.get("items", []):
                print(f'item_id={it["item_id"]} type={it["item_type"]} title={it.get("title")!r}')
                print(f'  text[:200]={it.get("text", "")[:200]!r}')
                print(f'  assets:')
                for ar in it.get("asset_refs", []):
                    print(f'    {ar["asset_id"]}  caption={ar.get("caption", "")!r}')
            break
