#!/usr/bin/env bash
# WICS 주간 스냅샷 — 토요일 03:00 KST (재시도 10:00). 플랜 `docs/plans/2026-09-20-wics-weekly.md` T1.
#   순서: raw 락 → dt = 직전 거래일(금요일, daily.calendar) → wics_snapshot(38콜, 멱등) → 알림
#   크론(서버 TZ=UTC): 0 18 * * 5 … scripts/wics_weekly.sh            # 03:00 KST
#                     0 1  * * 6 … scripts/wics_weekly.sh --retry    # 10:00 KST — 03:00 이 전부 빈 응답(rc 4)이었을 때만 콜
#   rc: 0 완료 · 3 락 실패 · 4 전부 빈 응답(구성 미공표·휴장일 — 재시도 대상) · 1/2 실패
#   사용: wics_weekly.sh [--date YYYYMMDD] [--retry] [--dry-run]
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
LOCK=/tmp/quant_ledger_raw.lock
DATE_ARG=""; DRY=""; RETRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --retry) RETRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
exec 9>"$LOCK"
if ! flock -n 9; then
  [ -z "$DRY" ] && scripts/notify.sh warn "wics_weekly 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
  exit 3
fi
D="${DATE_ARG:-$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
LOG="logs/wics_weekly_${D:-unknown}.log"
{
echo "════ [$(kst)] wics_weekly 시작 dt=$D dry=${DRY:-no} retry=${RETRY:-no} ════"
if [ -z "$D" ]; then
  echo "  ✗ 대상 거래일 산출 실패(캘린더 오류) — 중단"; RC=2
else
  if [ -n "$RETRY" ] && [ -z "$DRY" ]; then
    # 재시도는 03:00 실행이 "전부 빈 응답" 이었을 때만 의미가 있다 — 이미 행>0 스냅샷이 있으면 콜 0 (멱등 skip)
    echo "  재시도 모드 — 행>0 판본이 있는 코드는 건너뛴다"
  fi
  $PY -m wics_snapshot --dt "$D" --codes all --db data/raw/wiseindex.db $DRY
  RC=$?
  echo "──── wics_snapshot 종료 rc=$RC $(kst) ────"
fi
echo "════ 종료 rc=$RC $(kst) ════"
} 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
SUMMARY=$(grep -E "^WICS 스냅샷|L1 검산|차 [+-]|fail|empty=" "$LOG" | tail -6 | tr '\n' ' ' | cut -c1-900)
if [ -n "$DRY" ]; then exit "$RC"; fi
case "$RC" in
  0) scripts/notify.sh info "WICS 주간 스냅샷 dt=$D 완료" "$SUMMARY | 로그 $LOG" ;;
  4) scripts/notify.sh warn "WICS 주간 스냅샷 dt=$D 전부 빈 응답" "구성 미공표 또는 휴장일 — 10:00 재시도 대기 | $SUMMARY | 로그 $LOG" ;;
  *) scripts/notify.sh crit "WICS 주간 스냅샷 dt=$D 실패 rc=$RC" "$SUMMARY | 로그 $LOG" ;;
esac
exit "$RC"
