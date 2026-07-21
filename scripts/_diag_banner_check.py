import json

d = json.loads(open(r"projects/高思竞赛数学课本三年级/review/figure_roles.json", encoding="utf-8").read())
for f in d["figures"]:
    if f["asset_id"] in ("b7b2a07ee7cc", "a79d2e81ac89", "d2f41b538cca", "1d44296f739a", "07c39c133300"):
        print(f'  {f["asset_id"]}  role={f["role"]}  reason={f["reason"]}')
