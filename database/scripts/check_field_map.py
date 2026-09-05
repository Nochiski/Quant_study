#!/usr/bin/env python3
"""EQUITY_FIELD_MAP.md ↔ 워크벤치 팩터 레지스트리 문서(backend/FACTORS.md)의 field_id 집합 차를 검사한다.

S00 통과 조건: 레지스트리가 요구하는 field_id 집합 − 대응표 field_id 집합 = ∅.
반대 방향(대응표에만 있는 id)은 경고로만 출력한다 — equity 내부 스코프(`price.adj_close` 등)가 있을 수 있다.

사용: python database/scripts/check_field_map.py [--registry backend/FACTORS.md]
                                                     [--map database/docs/EQUITY_FIELD_MAP.md]
종료 코드 0 = 차집합 없음, 1 = 누락 있음, 2 = 파일 문제.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FIELD_RE = re.compile(r"`((?:price|financial|consensus|flow|short|credit|event|benchmark|classification)\.[a-z_0-9]+)`")
FACTOR_ID_RE = re.compile(r"^\| *\d+ *\| *`([a-z]+\.[a-z_0-9]+)` *\|")


def registry_fields(path: Path) -> set[str]:
    """레지스트리 표에서 'Required Equity fields' 열의 field_id 만 모은다 (팩터 ID 열은 제외)."""
    fields: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = FACTOR_ID_RE.match(line)
        if not m:
            continue
        factor_id = m.group(1)
        for f in FIELD_RE.findall(line):
            if f != factor_id:
                fields.add(f)
    return fields


def map_fields(path: Path) -> set[str]:
    """대응표 §2 의 첫 열 field_id. `a`·`b` 처럼 한 행에 둘 이상이면 전부 센다."""
    fields: set[str] = set()
    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 2."):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.startswith("| `"):
            first_cell = line.split("|")[1]
            fields.update(FIELD_RE.findall(first_cell))
    return fields


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="backend/FACTORS.md")
    ap.add_argument("--map", default="database/docs/EQUITY_FIELD_MAP.md")
    args = ap.parse_args()
    reg, mp = Path(args.registry), Path(args.map)
    if not reg.exists() or not mp.exists():
        print(f"file missing — registry={reg} exists={reg.exists()} map={mp} exists={mp.exists()}")
        return 2
    r, m = registry_fields(reg), map_fields(mp)
    missing = sorted(r - m)
    extra = sorted(m - r)
    print(f"registry fields={len(r)} map fields={len(m)} missing={len(missing)} extra={len(extra)}")
    for f in missing:
        print(f"  MISSING in map: {f}")
    for f in extra:
        print(f"  extra in map (ok if equity-internal): {f}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
