"""compat CLI — `python -m compat export …` (플랜 `2026-09-24-v3-merge.md` §5 T1.2 4).

    python -m compat export --date 20260923 --basis morning \\
        --equity-root data/equity --stage-root data/stage --target data/compat/quant.db \\
        [--tables daily_prices,stocks] [--full] [--window-days 730] [--consensus-asof 20260922]

rc 0 정상 · 2 예외. 표별 행수 한 줄을 stdout 에 낸다(`scripts/compat_export.sh` 가 로그로 받는다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .quant_db import CompatError, export


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m compat")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="equity/stage 판 → v3 quant.db 9표 upsert")
    e.add_argument("--date", required=True, help="대상 거래일 YYYYMMDD")
    e.add_argument("--basis", required=True, choices=("evening", "morning"))
    e.add_argument("--equity-root", required=True, type=Path)
    e.add_argument("--stage-root", required=True, type=Path)
    e.add_argument("--target", required=True, type=Path, help="대상 sqlite 경로")
    e.add_argument("--tables", default=None,
                   help="쉼표로 구분한 v3 표 이름. 생략하면 원천이 있는 표 전부")
    e.add_argument("--full", action="store_true", help="증분 대신 창 전체 재적재")
    e.add_argument("--window-days", type=int, default=None, help="가격 창(달력일)")
    e.add_argument("--consensus-asof", default=None,
                   help="컨센서스 as-of YYYYMMDD (기본 --date)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = export(
            equity_root=args.equity_root, stage_root=args.stage_root, date=args.date,
            basis=args.basis, target=args.target,
            tables=[t.strip() for t in args.tables.split(",")] if args.tables else None,
            full=args.full, window_days=args.window_days, consensus_asof=args.consensus_asof)
    except CompatError as e:
        print(f"compat 실패: {e}", file=sys.stderr)
        return 2
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
