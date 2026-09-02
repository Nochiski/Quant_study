#!/usr/bin/env bash
# 6단계 풀 빌드 — 스냅샷 1세트(5 DB)로 61테이블을 의존 순서대로 빌드한다. 서버(~/quant-ledger)에서 실행.
#   scripts/run_stage_all.sh <snapshot-id> [table ...]     (테이블 생략 = 전수)
# 결과: logs/stage_all/<table>.log + logs/stage_all/summary.tsv (status · rows · src · dedup · reject · elapsed)
set -u
cd "$(dirname "$0")/.."
SNAP="$1"; shift
mkdir -p logs/stage_all
SUM=logs/stage_all/summary.tsv
[ -f "$SUM" ] || printf 'table\tstatus\trows\tsrc\tdedup\treject\telapsed_s\tfailed_gates\n' > "$SUM"

# 게이트 임계는 data/stage/baseline.json 의 테이블별 thresholds 에서 읽는다(§9) — 여기 하드코딩 금지.
# 임시 override 가 필요하면 환경변수 STAGE_EXTRA='--g2 0.01' 로 전 테이블에 준다.

ORDER=(stg_rcept_dt_map
  stg_price_daily stg_etf_price_daily stg_index_daily stg_listing_daily stg_ingest_krx
  stg_flow_daily_kiwoom stg_short_daily_kiwoom stg_foreign_daily stg_lending_daily stg_master_daily stg_shards_kiwoom
  stg_flow_split_daily stg_short_daily_kis stg_loan_daily_kis stg_credit_daily stg_delisted_master stg_calls_kis stg_units_kis
  stg_fin stg_dividend stg_shares stg_capital stg_tesstk stg_hyslr stg_audit stg_holder_elestock stg_holder_majorstock
  stg_disclosure stg_company stg_corp_map stg_doc_index stg_calls_dart stg_units_dart
  stg_event_tsstk_aq stg_event_piic stg_event_cvbd_is stg_event_fric stg_event_pifric stg_event_cr stg_event_cmp_mg
  stg_event_cmp_dv stg_event_cmp_dvmg stg_event_stk_extr stg_event_tsstk_dp stg_event_ctrcvs_bgrq stg_event_df_ocr
  stg_event_ds_rs_ocr stg_event_bnk_mngt_pcbg
  stg_consensus_monthly stg_consensus_annual stg_consensus_quarterly stg_consensus_matrix stg_analyst_summary stg_fin_wise
  stg_v3_revision_daily stg_v3_analyst_opinions stg_v3_consensus_annual stg_v3_revision_compare stg_wise_coverage stg_calls_wise)
if [ "$#" -gt 0 ]; then ORDER=("$@"); fi

for T in "${ORDER[@]}"; do
  LOG=logs/stage_all/$T.log
  echo "=== $T $(date -u +%FT%TZ) ${STAGE_EXTRA:-}" | tee "$LOG"
  # shellcheck disable=SC2086  # reason: STAGE_EXTRA 는 의도적으로 단어 분리
  /usr/bin/time -v env PYTHONPATH=src .venv/bin/python -m stage --table "$T" --snapshot-id "$SNAP" ${STAGE_EXTRA:-} >> "$LOG" 2>&1
  RC=$?
  LINE=$(grep -E '^(ok|gate_failed|error)' "$LOG" | tail -1)
  STATUS=$(echo "$LINE" | awk '{print $1}')
  ROWS=$(echo "$LINE" | sed -n 's/.* rows=\([0-9,]*\).*/\1/p'); SRC=$(echo "$LINE" | sed -n 's/.* src=\([0-9,]*\).*/\1/p')
  DEDUP=$(echo "$LINE" | sed -n 's/.* dedup=\([0-9,]*\).*/\1/p'); REJ=$(echo "$LINE" | sed -n 's/.* reject=\([0-9,]*\).*/\1/p')
  EL=$(echo "$LINE" | sed -n 's/.* \([0-9.]*\)s$/\1/p')
  FAILED=$(grep -E '^  G[0-9] fail' "$LOG" | awk '{print $1}' | paste -sd, -)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$T" "${STATUS:-rc=$RC}" "$ROWS" "$SRC" "$DEDUP" "$REJ" "$EL" "$FAILED" >> "$SUM"
  echo "--- $T → ${STATUS:-rc=$RC} rows=$ROWS reject=$REJ ${FAILED:+FAILED:$FAILED}"
done
echo "ALL_DONE $(date -u +%FT%TZ)"
