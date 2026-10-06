"""model CLI — `python -m model build …`.

    python -m model build --date 20260928 --basis morning \\
        [--fi-build latest|<factor_inputs build_id>] [--specs all|<spec_id,…>] \\
        [--primary v4_rank@0.1] [--root data/model] [--fi-root data/factor_inputs] \\
        [--min-prices-on-d 2000] [--min-ranked 100] [--keep 60]

rc 0 판 커밋 · 1 게이트 FAIL(판 안 올림, `_failed/<build_id>.json`) · 2 입력·인자 오류·예외.
기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — factor_inputs CLI 와 같은 규약.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from model import gates
from model.build import BASES, KEEP_DEFAULT, PRIMARY_DEFAULT, ModelBuildError, build


def _parser() -> argparse.ArgumentParser:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    p = argparse.ArgumentParser(prog="python -m model")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="factor_inputs 판 → 레지스트리 spec 점수 판")
    b.add_argument("--date", required=True, help="판 기준일 D (YYYYMMDD, factor_inputs 판의 D)")
    b.add_argument("--basis", required=True, choices=BASES)
    b.add_argument("--fi-build", default="latest",
                   help="factor_inputs 판 id(기본 latest = latest_<basis>.json)")
    b.add_argument("--specs", default="all", help="all 또는 spec_id 쉼표 목록")
    b.add_argument("--primary", default=PRIMARY_DEFAULT,
                   help="인계 대표 모델(선택한 spec 안에 있어야 한다)")
    b.add_argument("--root", type=Path, default=base / "data" / "model")
    b.add_argument("--fi-root", type=Path, default=base / "data" / "factor_inputs")
    b.add_argument("--min-prices-on-d", type=int, default=gates.MIN_PRICES_ON_D,
                   help="MG4 — 전체 fi_prices 에서 D 종가가 있는 종목 수 하한")
    b.add_argument("--min-ranked", type=int, default=gates.MIN_RANKED,
                   help="MG1 — v4 계열 순위 종목 수 하한")
    b.add_argument("--keep", type=int, default=KEEP_DEFAULT, help="spec 별 MANIFEST 에 남길 판 수")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    specs = "all" if args.specs == "all" else [s for s in args.specs.split(",") if s]
    try:
        result = build(args.date, args.basis, args.root, args.fi_root, fi_build=args.fi_build,
                       specs=specs, primary=args.primary, min_prices_on_d=args.min_prices_on_d,
                       min_ranked=args.min_ranked, keep=args.keep)
    except ModelBuildError as e:
        print(f"model 실패: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  # reason: 체인이 rc 로만 보므로 어떤 예외든 원인을 남긴다
        print(f"model 실패(예상 밖): {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(result.summary())
    for sid, rs in result.gates.items():
        for g in gates.failed(rs):
            print(f"  {sid} {g.name} FAIL: {g.detail}", file=sys.stderr)
    for sid, s in result.specs.items():
        if "error" in s:                         # 엔진 예외로 뺀 비교 모델(D-01)
            print(f"  {sid} 엔진 오류: {s['error']}", file=sys.stderr)
    if result.excluded:
        print(f"  비교 모델 제외(주 모델 판은 올림): {', '.join(result.excluded)}", file=sys.stderr)
    if not result.ok:
        print(f"  보고서 {result.failed_report}", file=sys.stderr)
        if result.run_manifest_kept:
            print(f"  같은 날 성공 기록 {result.run_manifest} 은 그대로 둔다(실패는 보고서에만)",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
