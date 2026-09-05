"""S26 표본 프리패스 캐시 → stg_doc_meta G4 골든 픽스처 (DOC_DESIGN §1.1 표와 대조한 뒤 커밋).

  PYTHONPATH=src python scripts/doc_fixtures_from_sample.py \
      --cache <stage_root>/_tmp/doc/<snapshot> --out src/stage/fixtures/stg_doc_meta.json

문서마다 main 멤버(없으면 역할 우선순위상 첫 멤버) 1행에서 아래 열의 값을 `expect` 로 뽑는다. 값은
G4 가 `CAST(col AS VARCHAR)` 로 비교하므로 stage 표현(DATE → YYYY-MM-DD, BOOL → true/false)으로.
표와 다르면 파서 결함이다 — 픽스처를 고치지 말고 파서를 고친다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROLE_PRIORITY = {"main": 0, "audit_cons": 1, "audit": 2, "other": 3}
_TEXT = ("gen", "byte_enc", "doc_acode", "formula_version")
_NUM = ("toc_n", "n_xbrl_groups")
_BOOL = ("has_correction_page",)
_DATE = ("period_from", "period_to")


def _stage_repr(col: str, v: str | None) -> str | None:
    if v is None:
        return None
    if col in _DATE:
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else None
    return v


def build_fixtures(cache: Path, measured_at: str) -> list[dict[str, object]]:
    rows: list[dict[str, str | None]] = []
    for f in sorted((cache / "stg_doc_meta").glob("year=*.jsonl")):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line]
    by_doc: dict[str, list[dict[str, str | None]]] = {}
    for r in rows:
        by_doc.setdefault(str(r["rcept_no"]), []).append(r)
    out: list[dict[str, object]] = []
    for rno in sorted(by_doc):
        r = min(by_doc[rno], key=lambda m: _ROLE_PRIORITY.get(str(m["member_role"]), 3))
        key = {"rcept_no": rno, "member_name": r["member_name"]}
        for col in _TEXT + _NUM + _BOOL + _DATE:
            out.append({"key": key, "column": col, "expect": _stage_repr(col, r.get(col)),
                        "measured_by": "doc_prepass --rcept-list S26", "measured_at": measured_at,
                        "note": f"S26 {r.get('doc_name') or r.get('format')} {r['member_role']}"})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--measured-at", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    a = ap.parse_args(argv)
    fx = build_fixtures(a.cache, a.measured_at)
    if not fx:
        raise FileNotFoundError(f"no stg_doc_meta rows under {a.cache}/stg_doc_meta/year=*.jsonl")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_docs = len({str(f["key"]["rcept_no"]) for f in fx if isinstance(f["key"], dict)})
    print(f"wrote {len(fx)} fixtures for {n_docs} documents → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
