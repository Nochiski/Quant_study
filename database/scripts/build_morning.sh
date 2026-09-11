#!/usr/bin/env bash
# 아침 확정 빌드 — 플랜 v2 §4 Task B.1. KRX 축으로 stage·equity 확정판(basis=morning)을 짓는다.
#   사용: scripts/build_morning.sh [--date YYYYMMDD] [--dry-run]
#   호출: scripts/daily_build.sh 의 원장 단계(KRX → 외국인 보유 → 머지 → 건전성)가 rc 0 일 때만.
#         원장 게이트가 실패한 날은 부르지 않는다 — 어제 판을 유지하는 쪽이 맞다(v1 Task 4.1 Step 2).
#   D 기본값은 **전 거래일** 이다. 08:10 KST 체인이 다루는 거래일은 오늘이 아니라 T-1 이며,
#   daily_build.sh 가 자기 D 를 그대로 넘겨 준다(--date). 잠정판과 같은 D 여야 잠정 vs 확정 대조가 선다.
#   raw 락은 daily_build 에서 QL_RAW_LOCK_HELD=1 로 물려받고, 빌드 락은 build_chain 이 새로 잡는다.
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 빌드하지 않는다" >&2; exit 4; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
DATE_ARG=""; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
D="${DATE_ARG:-$($PY -c 'import datetime as dt
from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
if [ -z "$D" ]; then
  echo "대상 거래일을 계산하지 못했다 — build_chain 이 오늘로 폴백하지 않게 여기서 멈춘다" >&2
  [ -z "$DRY" ] && scripts/notify.sh crit "확정 빌드 시작 불가 — 대상일 계산 실패" "daily.calendar.prev_trading_day 가 값을 주지 않았다"
  exit 2
fi
mkdir -p logs/morning
echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] build_morning D=$D dry=${DRY:-no} ════" \
  | tee -a "logs/morning/build_${D}.log"
exec bash scripts/build_chain.sh morning --date "$D" ${DRY:+--dry-run}
