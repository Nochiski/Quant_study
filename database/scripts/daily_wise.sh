#!/bin/bash
# 매일 KST 06:00 — 키움 마스터 스냅샷(2콜). 유니버스 최신화 + 관리·정지 상태 이력.
# WISE 스냅샷은 18:05 daily_evening.sh 로 이동(결정 V2-3, 2026-09-11) — WISE 값은 당일 장중에 확정되므로
#   저녁에 받으면 리비전 팩터의 1일 랙이 사라진다. 여기에는 마스터만 남는다.
#   (파일명은 크론·문서 참조가 걸려 있어 그대로 둔다)
#   (v3 미러 sync_v3 는 2026-09-02 중단 — 직접 수집이 같은 값을 커버. 과거분 ~09-02 는 동결 보관)
# 2026-09-09 (플랜 P0 Task 0.1·0.2): 원장 락(flock) + 종료코드 검사 + 텔레그램 알림.
#   락 규약 — 최외곽 스크립트만 잡고, 부모가 QL_RAW_LOCK_HELD=1 을 넘기면 자식은 획득을 생략한다
#   (같은 락 파일을 자식이 다시 열면 별개 open file description 이라 부모와 충돌한다).
set -o pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger
LOCK=/tmp/quant_ledger_raw.lock
if [ -z "${QL_RAW_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "daily_master 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
    exit 3
  fi
  export QL_RAW_LOCK_HELD=1
fi
LOG="logs/daily_wise_$(TZ=Asia/Seoul date +%m%d).log"
RUN=$(mktemp)
RC_M=0
{
echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] 키움 마스터 데일리 ════"
.venv/bin/python src/master_daily.py 2>&1 | grep -v Deprecation
RC_M=${PIPESTATUS[0]}
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') rc master=$RC_M ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
# master_daily.py 는 한 시장이 실패해도 rc 0 으로 끝나고 `✖` 만 찍는다 — 그 줄을 실패로 센다
N_FAIL=$(grep -c "✖" "$RUN" || true)
SUMMARY=$(grep -E "스냅샷|✓|✖|cont-yn" "$RUN" | tail -4 | tr '\n' ' ' | cut -c1-600)
if [ "$RC_M" -ne 0 ] || [ "$N_FAIL" -gt 0 ]; then
  scripts/notify.sh crit "daily_master 실패" "rc master=$RC_M 시장실패=$N_FAIL | $SUMMARY | 로그 $LOG"
  rm -f "$RUN"
  exit 1
fi
scripts/notify.sh info "daily_master 완료" "$SUMMARY"
rm -f "$RUN"
