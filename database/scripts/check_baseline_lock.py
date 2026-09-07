#!/usr/bin/env python3
"""equity baseline 확정본 대조 — `baseline_locked.json` 과 대상 파일이 **바이트 동일**한가.

S22 가 고정한 규약: 서버 `~/quant-ledger/data/equity/baseline.json` 은
`database/src/equity/baseline_locked.json` 의 사본이어야 한다. 설치 경로는 하나뿐이다.

    scp database/src/equity/baseline_locked.json \
        kael-server:~/quant-ledger/data/equity/baseline.json

검사:

    scp kael-server:~/quant-ledger/data/equity/baseline.json /tmp/server_baseline.json
    uv run python database/scripts/check_baseline_lock.py /tmp/server_baseline.json

바이트가 다르면 상수 차이를 함께 찍는다(`thresholds.<G>` 는 `threshold_<G>` 로 펴서 비교) —
차이가 메타(`_measured`·`_lock`)뿐인지 **게이트가 읽는 상수**인지 가르기 위해서다.
종료 코드 0 = 동일, 1 = 다름, 2 = 인자·파일 오류.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "src" / "equity" / "baseline_locked.json"


def constants(raw: dict[str, object]) -> dict[str, object]:
    """`{table: {metric: value}}` 를 `"table.metric" -> value` 로 편다. 메타 키는 뺀다."""
    flat: dict[str, object] = {}
    for table, block in raw.items():
        if table.startswith("_") or not isinstance(block, dict):
            continue
        for metric, value in block.items():
            if metric == "thresholds" and isinstance(value, dict):
                for gate, gv in value.items():
                    flat[f"{table}.threshold_{gate}"] = gv
            else:
                flat[f"{table}.{metric}"] = value
    return flat


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path,
                    help="대조할 baseline.json (보통 서버에서 받아온 사본)")
    ap.add_argument("--lock", type=Path, default=LOCK, help=f"확정본 (기본 {LOCK})")
    a = ap.parse_args(argv)

    for p in (a.lock, a.target):
        if not p.exists():
            print(f"파일 없음: {p}", file=sys.stderr)
            return 2

    lock_bytes, target_bytes = a.lock.read_bytes(), a.target.read_bytes()
    lock_sha = hashlib.sha256(lock_bytes).hexdigest()
    target_sha = hashlib.sha256(target_bytes).hexdigest()
    print(f"lock   {lock_sha}  {len(lock_bytes):,}B  {a.lock}")
    print(f"target {target_sha}  {len(target_bytes):,}B  {a.target}")
    if lock_bytes == target_bytes:
        print("OK — 바이트 동일")
        return 0

    print("MISMATCH — 바이트가 다르다")
    lc, tc = constants(json.loads(lock_bytes)), constants(json.loads(target_bytes))
    keys = sorted(set(lc) | set(tc))
    diffs = [(k, lc.get(k, "<없음>"), tc.get(k, "<없음>")) for k in keys if lc.get(k) != tc.get(k)]
    if not diffs:
        print("  상수는 전부 같다 — 차이는 메타(_measured·_lock 등)뿐이다. "
              "확정본을 그대로 설치하면 된다.")
        return 1
    print(f"  게이트가 읽는 상수 차이 {len(diffs)}건 — 재빌드·재판정 범위를 "
          f"EQUITY_HANDOFF.md §5 로 판단할 것:")
    for k, lv, tv in diffs:
        print(f"    {k}: lock={lv!r} target={tv!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
