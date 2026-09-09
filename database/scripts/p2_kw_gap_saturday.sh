#!/usr/bin/env bash
# 플랜 P2 Task 2.2 — 키움 13거래일 갭 메우기 일회성 실행. 토요일에만 돈다(v3 daily_all 이 평일만 돌아
# 공유 앱키 사용량이 우리 ≈10,300 뿐 — 검증 한도 20,000 안). 일회성 크론(2026-09-12 11:00 KST)으로 걸고, 끝나면 크론 줄을 제거한다.
#   순서: KRX 09-09~09-11 수집(머지 대조에 필요) → 키움 fetch → 키움 merge → 텔레그램 요약
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
if [ "$(TZ=Asia/Seoul date +%u)" != "6" ]; then echo "토요일이 아니다 — 실행 안 함"; exit 3; fi
exec 9>/tmp/quant_ledger_raw.lock
flock -n 9 || { scripts/notify.sh warn "p2 키움 갭 락 실패" "원장 락 점유 중 — 실행 안 함"; exit 3; }
export QL_RAW_LOCK_HELD=1
D=$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')
LOG=logs/p2/kw_gap_$(TZ=Asia/Seoul date +%Y%m%d).log
scripts/notify.sh info "p2 키움 갭 메우기 시작" "D=$D 유니버스×4 TR ≈10,300콜"
{
echo "==== KRX 2026-09-09 ~ ${D:0:4}-${D:4:2}-${D:6:2} ===="
$PY src/backfill_krx.py --from 2026-09-09 --to "${D:0:4}-${D:4:2}-${D:6:2}"; RC_K=$?
echo "==== kw fetch D=$D ===="
$PY -m daily.kw_daily --date "$D" --fetch; RC_F=$?
echo "==== kw merge D=$D ===="
$PY -m daily.kw_daily --date "$D" --merge; RC_M=$?
echo "rc krx=$RC_K fetch=$RC_F merge=$RC_M"
} > "$LOG" 2>&1
SUM=$(grep -E "status=|rc krx" "$LOG" | tail -4 | tr '\n' ' ' | cut -c1-700)
if grep -q "rc krx=0 fetch=0 merge=0" "$LOG"; then
  scripts/notify.sh info "p2 키움 갭 메우기 완료" "$SUM"
else
  scripts/notify.sh crit "p2 키움 갭 메우기 실패" "$SUM | 로그 $LOG"
fi
