#!/usr/bin/env bash
# v3 점수 대조 — 플랜 `docs/plans/2026-09-24-v3-merge.md` T1.3 / 게이트 G-M2 ②③·G-M3 ①.
#   순서: 인자 확인 → `python -m model.compare`(두 sqlite 를 ?mode=ro 로만 연다) → 리포트 → 알림
#   산출: logs/model_compare/<D>_<표>.md · .json, 실행 로그 logs/model_compare/<D>.log
#   크론 없음 — M1 그림자 실행(오케스트레이터 수동)과 M2 이식 동등성 확인에 쓴다.
#   rc: 0 pass(info) · 1 fail(warn — 차이 원인 분류를 사람이 본다) · 그 외 오류(crit)
#   사용: model_compare.sh --date YYYY-MM-DD|YYYYMMDD --left V3_QUANT_DB --right OUR_DB [--table score_history|score_history_v2|both]
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 2; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
DATE_ARG=""; LEFT=""; RIGHT=""; TABLE="both"
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --left) LEFT="$2"; shift 2 ;;
    --right) RIGHT="$2"; shift 2 ;;
    --table) TABLE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$DATE_ARG" ] || [ -z "$LEFT" ] || [ -z "$RIGHT" ]; then
  echo "usage: model_compare.sh --date YYYY-MM-DD --left V3_QUANT_DB --right OUR_DB [--table …]" >&2
  exit 2
fi
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
# 로그·리포트 파일명은 한 형식으로 — YYYYMMDD 로 들어와도 v3 score_date 형식으로 맞춘다(파이썬도 같은 변환).
if [[ "$DATE_ARG" =~ ^[0-9]{8}$ ]]; then
  D="${DATE_ARG:0:4}-${DATE_ARG:4:2}-${DATE_ARG:6:2}"
else
  D="$DATE_ARG"
fi
OUT=logs/model_compare
mkdir -p "$OUT"
LOG="$OUT/${D}.log"
RCF=$(mktemp)          # 파이프(`| tee`) 안의 RC 는 서브셸에 갇힌다 — 파일로 꺼낸다(wics_weekly 09-20 교훈)
{
echo "════ [$(kst)] model_compare 시작 D=$D table=$TABLE ════"
echo "  왼쪽(v3) $LEFT / 오른쪽 $RIGHT"
$PY -m model.compare --date "$D" --left "$LEFT" --right "$RIGHT" --out "$OUT" --table "$TABLE"
RC=$?
echo "════ 종료 rc=$RC $(kst) ════"
echo "$RC" > "$RCF"
} 2>&1 | tee -a "$LOG"
RC=$(cat "$RCF" 2>/dev/null || echo 9); rm -f "$RCF"
SUMMARY=$(grep -E "verdict=|✗|오류" "$LOG" | tail -8 | tr '\n' ' ' | cut -c1-900)
case "$RC" in
  0) scripts/notify.sh info "점수 대조 D=$D 통과" "$SUMMARY | 리포트 $OUT/${D}_*.md" ;;
  1) scripts/notify.sh warn "점수 대조 D=$D 불일치" "게이트 미달 — 원인 분류를 리포트에서 확인 | $SUMMARY | 리포트 $OUT/${D}_*.md" ;;
  *) scripts/notify.sh crit "점수 대조 D=$D 실패 rc=$RC" "$SUMMARY | 로그 $LOG" ;;
esac
exit "$RC"
