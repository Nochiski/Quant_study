#!/usr/bin/env bash
# 커밋된 equity 29표를 확정 baseline 으로 재판정한다(빌드 없음·폐기 없음).
# 서버 전용. baseline 상수를 바꾼 뒤 '재빌드가 필요 없는 변경' 임을 확인할 때 쓴다
#   (재빌드가 필요한 상수 목록은 EQUITY_HANDOFF.md §5-1).
set -uo pipefail
export LC_ALL=C
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
cd "$QL_HOME"
OUT=logs/equity/gate_all
mkdir -p "$OUT"
: > "$OUT/summary.txt"
ORDER="trading_calendar corp security security_span corp_ticker index_daily price_daily corp_event adj_factor price_adj_daily universe_daily universe_policy flow_daily short_daily credit_daily disclosure_version fin_std holder_daily ownership_snapshot audit_opinion shares_outstanding treasury_stock dividend_event consensus_daily opinion_daily opinion_broker_daily dataset_profile factor_readiness"
for t in $ORDER; do
  .venv/bin/python -m equity --root data/equity --stage-root data/stage \
    --baseline data/equity/baseline.json gate "$t" > "$OUT/$t.log" 2>&1
  rc=$?
  n_fail=$(grep -c ' fail ' "$OUT/$t.log" || true)
  echo "$t rc=$rc fail=$n_fail $(grep -oE '^  EG[0-9A-Za-z_]+ +[a-z]+' "$OUT/$t.log" | tr -s ' ' | tr '\n' ',')" >> "$OUT/summary.txt"
done
echo DONE >> "$OUT/summary.txt"
