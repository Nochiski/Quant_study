"""compat CLI — `python -m compat export …` (플랜 `2026-09-24-v3-merge.md` §5 T1.2 4).

    python -m compat export --date 20260923 --basis morning \\
        --equity-root data/equity --stage-root data/stage --target data/compat/quant.db \\
        [--tables daily_prices,stocks] [--full] [--window-days 730] [--consensus-asof 20260922] \\
        [--builds-from data/deliver/history/20260923_morning.json] \\
        [--model-universe all|estimates] [--builds-from-missing error|current] [--in-place] \\
        [--model-root data/model] \\
        [--postclose-db data/raw/postclose.db --kiwoom-db data/raw/kiwoom.db] [--calendar-dir DIR]

점수 두 표(score_history·score_history_v2)는 --model-root 의 그날·그 basis 모델 판이 원천이다(QL-C).
표를 고르지 않으면 점수 표도 들어가므로 --model-root 가 필요하다.
--basis evening 의 daily_prices·investor_detail_flows 는 T 행을 두 원장에서 만든다(QL-D) —
--postclose-db·--kiwoom-db 가 필요하다. D' 는 판정 달력(--calendar-dir, 없으면 daily.calendar 기본
경로)으로 센다.

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
    e.add_argument("--builds-from", default=None, type=Path,
                   help="인계 이력 JSON(data/deliver/history/<D>_<basis>.json) 의 판으로 고정")
    e.add_argument("--builds-from-missing", default="error", choices=("error", "current"),
                   help="--builds-from 의 판이 없을 때: error(멈춤) | current(current_build 폴백)")
    e.add_argument("--model-universe", default="all", choices=("all", "estimates"),
                   help="estimates 면 당해 12월기 WISE 추정치가 없는 종목의 "
                        "stocks.market_cap 을 NULL 로 둔다(사용자 결정 09-24). 그림자 전용")
    e.add_argument("--in-place", action="store_true",
                   help="v3 quant.db 제자리 반영 — --model-universe 는 all 만 허용(T-19)")
    e.add_argument("--model-root", default=None, type=Path,
                   help="모델 판 루트(data/model) — score_history·score_history_v2 의 원천. "
                        "--date·--basis 의 판으로 고정한다(T-16)")
    e.add_argument("--postclose-db", default=None, type=Path,
                   help="장 마감 판 T 행 ① 원천 — 15:41 수집 원장(data/raw/postclose.db). "
                        "--basis evening 전용")
    e.add_argument("--kiwoom-db", default=None, type=Path,
                   help="장 마감 판 T 행 ② 원천 — 키움 원장(data/raw/kiwoom.db, 21:05 저녁 T 행). "
                        "--basis evening 전용")
    e.add_argument("--calendar-dir", default=None, type=Path,
                   help="T 직전 거래일 D' 를 셀 판정 달력 폴더(kis_holidays_<YYYY>.json). "
                        "없으면 daily.calendar 기본 경로")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = export(
            equity_root=args.equity_root, stage_root=args.stage_root, date=args.date,
            basis=args.basis, target=args.target,
            tables=[t.strip() for t in args.tables.split(",")] if args.tables else None,
            full=args.full, window_days=args.window_days, consensus_asof=args.consensus_asof,
            builds_from=args.builds_from, builds_from_missing=args.builds_from_missing,
            model_universe=args.model_universe, in_place=args.in_place,
            model_root=args.model_root, postclose_db=args.postclose_db,
            kiwoom_db=args.kiwoom_db, calendar_dir=args.calendar_dir)
    except CompatError as e:
        print(f"compat 실패: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  # reason: 크론이 rc 로만 보므로 어떤 예외든 원인을 남긴다
        print(f"compat 실패(예상 밖): {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
