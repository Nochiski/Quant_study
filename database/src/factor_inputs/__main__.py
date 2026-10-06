"""factor_inputs CLI — `python -m factor_inputs build …`.

    python -m factor_inputs build --date 20260929 --basis morning \\
        [--root data/factor_inputs] [--stage-root data/stage] [--equity-root data/equity] \\
        [--grace-days 5] [--min-eligible 300] [--keep 3]

rc 0 판 커밋 · 1 게이트 FAIL(판 안 올림, `_failed/<build_id>.json`) · 2 입력·인자 오류·예외.
기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — equity CLI 와 같은 규약.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .build import MIN_ELIGIBLE_DEFAULT, FactorInputsError, build


def _parser() -> argparse.ArgumentParser:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    p = argparse.ArgumentParser(prog="python -m factor_inputs")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="equity/stage 현재 판 → factor_inputs 8표 판")
    b.add_argument("--date", required=True, help="판 기준일 D (YYYYMMDD, 거래일)")
    b.add_argument("--basis", required=True, choices=("evening", "morning"),
                   help="morning 만 구현(evening 은 W1-a 뒤)")
    b.add_argument("--root", type=Path, default=base / "data" / "factor_inputs")
    b.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    b.add_argument("--equity-root", type=Path, default=base / "data" / "equity")
    b.add_argument("--grace-days", type=int, default=None,
                   help="추정치 소멸 유예(거래일, 기본 UniverseRule().coverage_grace_days = 0)")
    b.add_argument("--min-eligible", type=int, default=MIN_ELIGIBLE_DEFAULT,
                   help="FG1 eligible 종목 수 하한")
    b.add_argument("--keep", type=int, default=3, help="표별 MANIFEST 에 남길 판 수")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build(args.date, args.basis, args.root, args.stage_root, args.equity_root,
                       grace_days=args.grace_days, min_eligible=args.min_eligible,
                       keep=args.keep)
    except FactorInputsError as e:
        print(f"factor_inputs 실패: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  # reason: 체인이 rc 로만 보므로 어떤 예외든 원인을 남긴다
        print(f"factor_inputs 실패(예상 밖): {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(result.summary())
    if not result.ok:
        for g in result.gates:
            if g.status.value == "fail":
                print(f"  {g.name} FAIL: {g.detail}", file=sys.stderr)
        print(f"  보고서 {result.failed_report}", file=sys.stderr)
        if result.run_manifest_kept:
            print(f"  같은 날 성공 기록 {result.run_manifest} 은 그대로 둔다(실패는 보고서에만)",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
