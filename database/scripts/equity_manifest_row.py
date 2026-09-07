"""표 하나의 current_build 요약 한 줄 — build_id, content_hash, n_rows, rules_version, 게이트."""
import json
import sys

t = sys.argv[1]
m = json.load(open(f"data/equity/{t}/MANIFEST.json"))
cb = m.get("current_build")
b = next(x for x in m["builds"] if x["build_id"] == cb)
gs = ",".join(f"{g['name']}={g['status']}" for g in (b.get("gates") or []))
print(f"{cb}\t{b.get('content_hash')}\t{b.get('n_rows')}\t{b.get('rules_version')}\t{gs}")
