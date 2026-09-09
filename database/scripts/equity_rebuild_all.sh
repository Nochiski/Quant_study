#!/usr/bin/env bash
# equity 28표 전량 재빌드 — 의존 순서대로 1회 지으며 표별 content_hash 를 기록한다.
# 서버 전용(경로가 ~/quant-ledger 고정). 배포: scp 로 서버 scripts/ 에 두고 실행.
# 사용: equity_rebuild_all.sh <pass-label>   (예: pass1 / pass2)
# 재현성 검사 = 두 pass 의 logs/equity/rebuild_<pass>/summary.tsv content_hash 열 비교
#   (EG5a 는 전량 재빌드에서 inputs_changed 로 빠진다 — EQUITY_HANDOFF.md §7 ⑦)
set -uo pipefail
export LC_ALL=C
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
cd "$QL_HOME"
# 2026-09-09 (플랜 P0 Task 0.2): 락은 여기서 한 번만 잡고 자식 run_equity.sh 는 QL_BUILD_LOCK_HELD=1 로 생략한다.
LOCK=/tmp/quant_ledger_build.lock
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  flock -n 9 || { echo "another build is running — lock $LOCK"; exit 3; }
  export QL_BUILD_LOCK_HELD=1
fi
PASS="${1:?pass label required}"
OUT="logs/equity/rebuild_${PASS}"
mkdir -p "$OUT"
SUM="$OUT/summary.tsv"
: > "$SUM"

ORDER="trading_calendar corp security security_span corp_ticker index_daily price_daily corp_event adj_factor price_adj_daily universe_daily universe_policy flow_daily short_daily credit_daily disclosure_version fin_std holder_daily ownership_snapshot audit_opinion shares_outstanding treasury_stock dividend_event consensus_daily opinion_daily opinion_broker_daily dataset_profile factor_readiness"

T_ALL0=$(date +%s)
for t in $ORDER; do
  T0=$(date +%s)
  echo "=== [$PASS] build $t  $(date -u +%FT%TZ)"
  scripts/run_equity.sh "$t" --threads 3 --memory-limit 8GB > "$OUT/$t.log" 2>&1
  RC=$?
  T1=$(date +%s)
  H=$(.venv/bin/python scripts/equity_manifest_row.py "$t")
  printf '%s\t%s\t%s\t%s\n' "$t" "$RC" "$((T1-T0))" "$H" >> "$SUM"
  echo "    rc=$RC  $((T1-T0))s  $H"
  if [ "$RC" -ne 0 ]; then echo "!!! FAILED $t (rc=$RC) — 중단"; echo "FAILED $t" >> "$OUT/STATUS"; exit "$RC"; fi
done
T_ALL1=$(date +%s)
echo "TOTAL $((T_ALL1-T_ALL0))s" | tee "$OUT/STATUS"
