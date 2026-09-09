#!/bin/bash
# stage 빌드 러너 — 새 스냅샷으로 1회 빌드한 뒤, 같은 스냅샷으로 재빌드해 content_hash 재현성을 확인한다.
#   사용: scripts/run_stage.sh <table> [--threads N] [--memory-limit 6GB]
#   로그: logs/stage_<table>.log (경과·최대 RSS 포함). 실행 창: 06:30~익일 05:30 KST (daily_wise 무동작).
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
# 2026-09-09 (플랜 P0 Task 0.2): stage·equity 공용 빌드 락. 최외곽만 잡고 자식은 QL_BUILD_LOCK_HELD=1 이면 생략.
LOCK=/tmp/quant_ledger_build.lock
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  flock -n 9 || { echo "another build is running — lock $LOCK"; exit 3; }
  export QL_BUILD_LOCK_HELD=1
fi
TABLE="$1"; shift
LOG="logs/stage_${TABLE}.log"
{
echo "════ stage build ${TABLE} 시작 $(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST') ════"
/usr/bin/time -v .venv/bin/python -m stage --table "$TABLE" "$@" 2>&1 | tee /tmp/stage_${TABLE}_run1.out \
  | grep -vE "^\s+(Page size|Swaps|File system|Socket|Signals|Average|Exit status|Minor|Major|Voluntary|Involuntary)"
SNAP=$(grep -oE 'snapshot=snap_[0-9TZ]+' /tmp/stage_${TABLE}_run1.out | head -1 | cut -d= -f2)
if [ -n "$SNAP" ]; then
  echo "──── 재현성: 같은 스냅샷 ${SNAP} 으로 재빌드 ────"
  case " $* " in *" --snapshot-id "*) EXTRA=();; *) EXTRA=(--snapshot-id "$SNAP");; esac   # 중복 지정 방지
  .venv/bin/python -m stage --table "$TABLE" "${EXTRA[@]}" "$@" 2>&1 | tee /tmp/stage_${TABLE}_run2.out | grep -E "^(ok|gate_failed)"
  H1=$(grep -oE 'hash=[0-9]+:[0-9a-f]+' /tmp/stage_${TABLE}_run1.out | head -1)
  H2=$(grep -oE 'hash=[0-9]+:[0-9a-f]+' /tmp/stage_${TABLE}_run2.out | head -1)
  [ "$H1" = "$H2" ] && echo "재현성 OK: $H1" || echo "재현성 FAIL: run1 $H1 / run2 $H2"
fi
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') ════"
} >> "$LOG" 2>&1
