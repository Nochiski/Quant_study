#!/bin/bash
# 매일 KST 06:00 — WISE 컨센서스 일일 수집 체인 (2026-09-02 18:00→06:00 변경).
#   ① 키움 마스터 스냅샷(2콜) — 유니버스 최신화 + 관리·정지 상태 이력
#   ② WISE 수집 — 매일 full (전 종목 커버리지 재프로브)
#   (③ v3 미러 sync_v3 는 2026-09-02 중단 — 직접 수집이 같은 값을 커버. 과거분 ~09-02 는 동결 보관)
# 2026-09-09 (플랜 P0 Task 0.1·0.2): 원장 락(flock) + 종료코드 검사 + 텔레그램 알림.
#   락 규약 — 최외곽 스크립트만 잡고, 부모가 QL_RAW_LOCK_HELD=1 을 넘기면 자식은 획득을 생략한다
#   (같은 락 파일을 자식이 다시 열면 별개 open file description 이라 부모와 충돌한다).
set -o pipefail
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger"
LOCK=/tmp/quant_ledger_raw.lock
if [ -z "${QL_RAW_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "daily_wise 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
    exit 3
  fi
  export QL_RAW_LOCK_HELD=1
fi
LOG="logs/daily_wise_$(TZ=Asia/Seoul date +%m%d).log"
RUN=$(mktemp)
RC_M=0
RC_W=0
{
echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] WISE 데일리 ════"
.venv/bin/python src/master_daily.py 2>&1 | grep -v Deprecation
RC_M=${PIPESTATUS[0]}
# 항상 full — 무커버 재프로브가 +3,700요청(+1분)뿐이라, 신규 커버리지 개시를
# 주 1회가 아니라 "다음 날"에 잡는 쪽이 이득이다 (2026-09-01 결정)
MODE=full
echo "──── backfill_wise --mode $MODE ────"
.venv/bin/python src/backfill_wise.py --mode "$MODE" 2>&1 | grep -vE "Deprecation|datetime\.datetime"
RC_W=${PIPESTATUS[0]}
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') rc master=$RC_M wise=$RC_W ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
N_WARN=$(grep -c "⚠⚠" "$RUN" || true)
SUMMARY=$(grep -E "유니버스|요청|n_bad|커버|검증 실패" "$RUN" | tail -4 | tr '\n' ' ' | cut -c1-600)
if [ "$RC_M" -ne 0 ] || [ "$RC_W" -ne 0 ] || [ "$N_WARN" -gt 0 ]; then
  scripts/notify.sh crit "daily_wise 실패" "rc master=$RC_M wise=$RC_W 검증경고=$N_WARN | $SUMMARY | 로그 $LOG"
  rm -f "$RUN"
  exit 1
fi
scripts/notify.sh info "daily_wise 완료" "$SUMMARY"
rm -f "$RUN"
