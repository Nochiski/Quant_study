"""표별 current_build 의 게이트 metrics 를 JSON 으로 덤프 — baseline 상수의 서버 실측 근거용."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/equity")
out = {}
for d in sorted(root.iterdir()):
    mf = d / "MANIFEST.json"
    if not d.is_dir() or d.name.startswith("_") or not mf.exists():
        continue
    m = json.loads(mf.read_text())
    cb = m["current_build"]
    b = next(x for x in m["builds"] if x["build_id"] == cb)
    out[d.name] = {
        "build_id": cb, "content_hash": b["content_hash"], "n_rows": b["n_rows"],
        "rules_version": b["rules_version"],
        "gates": {g["name"]: {"status": g["status"], "detail": g["detail"],
                              "metrics": g.get("metrics", {})} for g in b.get("gates", [])},
    }
print(json.dumps(out, ensure_ascii=False))
