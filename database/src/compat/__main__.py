"""compat CLI — `python -m compat export …` (플랜 `2026-09-24-v3-merge.md` §5 T1.2 4).

    python -m compat export --date 20260923 --basis morning \\
        --equity-root data/equity --stage-root data/stage --target data/compat/quant.db \\
        [--tables daily_prices,stocks] [--full] [--window-days 730] [--consensus-asof 20260922] \\
        [--builds-from data/deliver/history/20260923_morning.json] \\
        [--model-universe all|estimates] [--builds-from-missing error|current] [--in-place] \\
        [--model-root data/model] \\
        [--postclose-db data/raw/postclose.db --kiwoom-db data/raw/kiwoom.db] \\
        [--calendar-dir DIR] [--allow-older]

점수 두 표(score_history·score_history_v2)는 --model-root 의 그날·그 basis 모델 판이 원천이다(QL-C).
표를 고르지 않으면 점수 표도 들어가므로 --model-root 가 필요하다.
--basis evening 의 daily_prices·investor_detail_flows 는 T 행을 두 원장에서 만든다(QL-D) —
--postclose-db·--kiwoom-db 가 필요하다. D' 는 판정 달력(--calendar-dir, 없으면 daily.calendar 기본
경로)으로 센다. 대상에 이번(T, 장 마감)보다 나중 반영 기록(T-35 순서 — 날짜가 뒤이거나 같은 날
아침)이 있으면 장 마감 판은 멈춘다(재생은 --allow-older).

v3 제자리 반영(QL-F, `scripts/v3_post.sh` 가 부른다 — `compat.v3_post`):

    python -m compat stage --v3-db <v3 quant.db> --out <스테이징>
    python -m compat v3-tables --v3-db <v3 quant.db> --date D --basis B     # 반영 표(T-34), 쉼표 구분
    python -m compat export … --target <스테이징> --in-place --tables <반영 표>
    python -m compat apply --staging <스테이징> --v3-db <v3 quant.db> --date D --basis B
                           [--shadow] [--allow-older] [--commit-flag PATH]

rc 0 정상 · 2 예외(apply 는 게이트 실패 포함 — v3 본 파일 무변경). 표별 행수 한 줄을 stdout 에 낸다
(`scripts/compat_export.sh`·`scripts/v3_post.sh` 가 로그로 받는다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import v3_post
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
    e.add_argument("--allow-older", action="store_true",
                   help="장 마감 판이 대상의 더 나중 반영 기록(T-35 순서)을 무시하고 쓴다 — "
                        "재생 전용")
    s = sub.add_parser("stage", help="v3 quant.db → 스테이징 사본(온라인 백업, QL-F)")
    s.add_argument("--v3-db", required=True, type=Path, help="v3 quant.db(읽기 전용으로 연다)")
    s.add_argument("--out", required=True, type=Path, help="스테이징 경로(있으면 지우고 새로 뜬다)")
    t = sub.add_parser("v3-tables", help="이번 반영 표 목록(T-34) — 쉼표 구분 한 줄(QL-F)")
    t.add_argument("--v3-db", required=True, type=Path)
    t.add_argument("--date", required=True, help="대상 거래일 YYYYMMDD")
    t.add_argument("--basis", required=True, choices=("evening", "morning"))
    a = sub.add_parser("apply", help="스테이징 게이트 → 반영 표 한 트랜잭션 반영(QL-F)")
    a.add_argument("--staging", required=True, type=Path)
    a.add_argument("--v3-db", required=True, type=Path)
    a.add_argument("--date", required=True, help="대상 거래일 YYYYMMDD")
    a.add_argument("--basis", required=True, choices=("evening", "morning"))
    a.add_argument("--shadow", action="store_true",
                   help="게이트까지만 — v3 본 파일에 쓰지 않는다")
    a.add_argument("--allow-older", action="store_true",
                   help="본 파일에 더 나중 반영 기록이 있어도 반영한다(재생 전용, T-35)")
    a.add_argument("--commit-flag", default=None, type=Path,
                   help="COMMIT 직후 만들 표식 파일(셸이 'COMMIT 뒤 실패' 를 가른다)")
    return p


def _v3_post(args: argparse.Namespace) -> int:
    """stage · apply 하위 명령. 게이트 실패는 실패 사유를 한 줄씩 stderr 에 낸다."""
    if args.cmd == "stage":
        v3_post.snapshot(args.v3_db, args.out)
        print(f"compat stage {args.v3_db} → {args.out} ({args.out.stat().st_size} bytes)")
        return 0
    if args.cmd == "v3-tables":
        print(",".join(v3_post.tables_for(args.v3_db, args.date, args.basis)))
        return 0
    try:
        report = v3_post.apply(args.staging, args.v3_db, args.date, args.basis,
                               shadow=args.shadow, allow_older=args.allow_older,
                               commit_flag=args.commit_flag)
    except v3_post.V3PostGateError as e:
        print(e.report.summary())
        for f in e.report.failures:
            print(f"compat 실패: 게이트 — {f}", file=sys.stderr)
        return 2
    print(report.summary())
    print("compat apply 그림자 — v3 본 파일에 쓰지 않았다" if args.shadow
          else f"compat apply 반영 완료 — {len(report.tables)}표 한 트랜잭션 → {args.v3_db}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd in ("stage", "v3-tables", "apply"):
        try:
            return _v3_post(args)
        except Exception as e:  # noqa: BLE001  # reason: 셸이 rc 로만 보므로 어떤 예외든 원인을 남긴다
            print(f"compat 실패({args.cmd}): {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    try:
        result = export(
            equity_root=args.equity_root, stage_root=args.stage_root, date=args.date,
            basis=args.basis, target=args.target,
            tables=[t.strip() for t in args.tables.split(",")] if args.tables else None,
            full=args.full, window_days=args.window_days, consensus_asof=args.consensus_asof,
            builds_from=args.builds_from, builds_from_missing=args.builds_from_missing,
            model_universe=args.model_universe, in_place=args.in_place,
            model_root=args.model_root, postclose_db=args.postclose_db,
            kiwoom_db=args.kiwoom_db, calendar_dir=args.calendar_dir,
            allow_older=args.allow_older)
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
