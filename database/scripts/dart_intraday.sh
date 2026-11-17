#!/usr/bin/env bash
# 낮 분할 수집 — 그날 접수된 DART 공시를 장중에 미리 받아 둔다(거래일 KST 12:30 · 14:30 · 16:15 · 17:15).
#   왜: 정기보고서의 94~97% 가 17:30 전에 접수되는데 18:05 저녁 체인이 한 번에 받으면 몰리는 날 88분이 걸렸다
#       (11-13 실측 — 메인 엑셀 20:01 발송). 플랜 docs/plans/2026-11-14-dart-intraday-split.md.
#   하는 일: `daily.dart_daily --date D(오늘 KST) --intraday` 한 번 — 스윕 → 상세 → 본문. 같은 D 를 다시 돌면 받지 않은
#       유닛만 부른다. 물량 관문은 판정하지 않고 런 로그 source 는 dart_intraday 다.
#   하지 않는 일: 저녁 인계 파일(ledger_evening.json) · 저녁 판 훅 · 키움 — 18:05 daily_evening.sh 몫 그대로다.
#   원장 락: 다른 원장 작업(앞 회차 낮 수집 · 배포)이 쥐고 있으면 끝날 때까지 기다렸다 이어서 돈다 — 회차를 건너뛰지
#       않는다(완료 감지, P9 · scripts/raw_lock.sh 공용 규칙 그대로: 대기자는 하나, 자정을 넘기면 crit).
#       같은 낮 수집이 이미 기다리는 중이면 rc 3 — 먼저 기다린 회차가 더 늦게 시작해 같은 공시를 다 받는다.
#       기다리는 사이 그 D 의 18:05 저녁 수집이 먼저 돌았으면 수집 없이 끝낸다(저녁 런이 그날 접수분을 다 받았다).
#       이 런이 18:05 를 넘기면 저녁 체인이 원장 락을 기다렸다 이어서 돈다.
#   알림: rc≠0 이면 warn 한 줄(18:05 런이 다시 부른다). 대기는 raw_lock.sh 가 info 를 남긴다. 휴장은 로그에만 남긴다.
#   사용: dart_intraday.sh [--date YYYYMMDD]
set -uo pipefail
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4)
PY=.venv/bin/python
DATE_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"
LOG="logs/dart_intraday_${D}.log"
mkdir -p logs
echo "════ [$(kst)] dart_intraday 시작 D=$D ════" >> "$LOG"
# rc 0 거래일 · 1 휴장 · 2 판정 불가(daily_evening.sh 와 같은 삼분기)
$PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
try:
    ok = c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])))
except Exception as e:  # noqa: BLE001  # reason: 어떤 예외든 "판정 불가" 로 올려 crit 을 내야 한다
    print(f"calendar error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
sys.exit(0 if ok else 1)' "$D" >> "$LOG" 2>&1; TD=$?
if [ "$TD" -eq 2 ]; then
  echo "  ✗ 캘린더 판정 불가 — 중단 $(kst)" >> "$LOG"
  scripts/notify.sh crit "dart_intraday 중단 — 캘린더 판정 불가" "D=$D | 로그 $LOG"
  exit 2
elif [ "$TD" -eq 1 ]; then
  echo "  D=$D 는 거래일이 아니다 — 건너뜀" >> "$LOG"
  exit 0
fi
. scripts/raw_lock.sh
raw_lock_acquire dart_intraday "" || { LRC=$?; echo "  원장 락을 잡지 못함(rc $LRC) — 종료 $(kst)" >> "$LOG"; exit "$LRC"; }
if [ -n "$LOCK_WAITED" ]; then
  echo "  원장 락 대기 $LOCK_WAITED" >> "$LOG"
  # 기다리는 사이 그 D 의 저녁 수집(source dart)이 시작했으면 rc 1 — 그 런이 그날 접수분을 다 받는다
  $PY -c 'import os, sys
from daily import runlog
from daily.dart_daily import RUN_SOURCE
db = os.path.join(os.environ["QL_HOME"], "data", "raw", "daily_run.db")
sys.exit(1 if runlog.first_started_utc(db, source=RUN_SOURCE, date=sys.argv[1]) is not None else 0)' "$D" >> "$LOG" 2>&1; EV=$?
  if [ "$EV" -eq 1 ]; then
    echo "  기다리는 사이 D=$D 저녁 수집이 먼저 돌았다 — 수집 없이 종료 $(kst)" >> "$LOG"
    exit 0
  fi
fi
$PY -m daily.dart_daily --date "$D" --intraday >> "$LOG" 2>&1; RC=$?
echo "════ 종료 rc=$RC $(kst) ════" >> "$LOG"
if [ "$RC" -ne 0 ]; then
  scripts/notify.sh warn "dart_intraday rc=$RC" \
    "D=$D | 낮 분할 수집이 rc $RC 로 끝났다 — 18:05 저녁 체인이 받지 않은 유닛을 다시 부른다 | 로그 $LOG"
fi
exit "$RC"
