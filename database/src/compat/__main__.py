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
    python -m compat v3-tables --v3-db <v3 quant.db> --date D --basis B [--no-scores]
                                                         # 반영 표(T-34·T-38), 쉼표 구분
    python -m compat export … --target <스테이징> --in-place --tables <반영 표>
    python -m compat apply --staging <스테이징> --v3-db <v3 quant.db> --date D --basis B
                           [--shadow] [--allow-older] [--commit-flag PATH] [--no-scores]
                           [--first-after-restore]

`--no-scores`(T-38 — 21:05 원장 뒤 재반영 refill)는 v3-tables·apply 에 같이 준다 — 점수 두 표를 뺀 7표.
`--basis evening` 전용이다(아침이면 rc 2 — 아침 반영 표는 T-34 가 정한다).
`--first-after-restore`(T-46)는 v3 표 복원 뒤 첫 제자리 반영에서 사람만 apply 에 준다 — 체인은 넘기지 않는다.

v3 되돌리기(QL-I, `scripts/v3_backup.sh`·`scripts/v3_restore.sh` 가 부른다 — `compat.v3_restore`):

    python -m compat backup --v3-db <v3 quant.db> --dest <경로1> --dest <경로2> --stamp <YYYYMMDDTHHMMSS>
                            [--file <V3-A~E 대상 파일> …]
    python -m compat restore --backup <백업 quant_<stamp>.db> --v3-db <v3 quant.db> [--tables a,b]
                             [--with-scores] [--dry-run]

복원 기본은 점수 두 표를 뺀 7표다(T-42). 점수 두 표는 `--with-scores` 일 때만(되돌리는 이유가 점수 오류일 때).

rc 0 정상 · 2 예외(apply 는 게이트 실패 포함 — v3 본 파일 무변경). 표별 행수 한 줄을 stdout 에 낸다
(`scripts/compat_export.sh`·`scripts/v3_post.sh` 가 로그로 받는다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import v3_post, v3_restore
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
                   help="T 직전 거래일 D' 와 증분 창(D + 앞 10거래일)을 셀 판정 달력 폴더"
                        "(kis_holidays_<YYYY>.json). 없으면 daily.calendar 기본 경로")
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
    t.add_argument("--no-scores", action="store_true",
                   help="점수 두 표를 뺀 7표(T-38 — 21:05 refill, evening 전용)")
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
    a.add_argument("--no-scores", action="store_true",
                   help="점수 두 표를 뺀 7표로 게이트·반영(T-38) — v3-tables 와 같이 준다")
    a.add_argument("--first-after-restore", action="store_true",
                   help="복원 뒤 첫 제자리 반영의 사람 표식(T-46) — 체인은 넘기지 않는다. 복원 뒤 첫 반영을 "
                        "기다리는 본 파일이 아니면 거부(오용 방지)")
    b = sub.add_parser("backup", help="v3 quant.db 고정 백업 2벌 + V3-A~E 대상 파일 사본(QL-I)")
    b.add_argument("--v3-db", required=True, type=Path, help="v3 quant.db(읽기 전용으로 연다)")
    b.add_argument("--dest", required=True, type=Path, action="append",
                   help="백업 경로 — 서로 다른 둘(두 번 준다, T-21)")
    b.add_argument("--stamp", required=True, help="이름 꼬리표(KST YYYYMMDDTHHMMSS)")
    b.add_argument("--file", default=[], type=Path, action="append",
                   help="<이름>.bak.<stamp> 로 함께 남길 파일(여러 번)")
    r = sub.add_parser("restore", help="백업 → v3 quant.db 표 단위 복원, 한 트랜잭션(QL-I)")
    r.add_argument("--backup", required=True, type=Path,
                   help="백업 파일 — 같은 폴더 SHA256SUMS 와 먼저 대조한다")
    r.add_argument("--v3-db", required=True, type=Path)
    r.add_argument("--tables", default=None,
                   help="쉼표로 구분한 표 — compat 9표 안에서만(기본 점수 두 표를 뺀 7표, T-42)")
    r.add_argument("--with-scores", action="store_true",
                   help="점수 두 표도 되돌린다(기본 9표, 점수 표를 --tables 로 고를 때도 필요) — 점수 오류일 때만")
    r.add_argument("--dry-run", action="store_true", help="표별 행 수만 — 쓰지 않는다")
    return p


def _v3_post(args: argparse.Namespace) -> int:
    """stage · apply · backup · restore 하위 명령. 게이트 실패는 실패 사유를 한 줄씩 stderr 에 낸다."""
    if args.cmd == "backup":
        res = v3_restore.backup(args.v3_db, args.dest, args.stamp, args.file)
        print("\n".join(res.lines()))
        return 0
    if args.cmd == "restore":
        tables = [t.strip() for t in args.tables.split(",")] if args.tables is not None else None
        report = v3_restore.restore(args.backup, args.v3_db, tables, dry_run=args.dry_run,
                                    with_scores=args.with_scores)
        print("\n".join(report.lines()))
        return 0
    if args.cmd == "stage":
        v3_post.snapshot(args.v3_db, args.out)
        print(f"compat stage {args.v3_db} → {args.out} ({args.out.stat().st_size} bytes)")
        return 0
    if args.cmd == "v3-tables":
        print(",".join(v3_post.tables_for(args.v3_db, args.date, args.basis,
                                          scores=not args.no_scores)))
        return 0
    try:
        report = v3_post.apply(args.staging, args.v3_db, args.date, args.basis,
                               shadow=args.shadow, allow_older=args.allow_older,
                               commit_flag=args.commit_flag, scores=not args.no_scores,
                               first_after_restore=args.first_after_restore)
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
    if args.cmd in ("stage", "v3-tables", "apply", "backup", "restore"):
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
