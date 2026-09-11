#!/usr/bin/env bash
# equity 테이블 1개 빌드 — 서버 전용. 사용: scripts/run_equity.sh <table> [equity CLI 추가 인자]
# 추가 인자는 `python -m equity build` 로 그대로 넘어간다 — `--basis evening|morning` 포함.
# stage 규약(run_stage.sh)과 같다: QL_HOME·PYTHONPATH 고정, /usr/bin/time -v 로 RSS·초 기록, flock 직렬.
# 2026-09-09 (플랜 P0 Task 0.2): 락은 stage·equity 공용 /tmp/quant_ledger_build.lock 하나로 통일했다
#   (stage 7.16 GB + equity 6.5 GB > available 13 GB — 빌드는 한 번에 하나만). 최외곽 스크립트만 락을
#   잡고, 부모가 QL_BUILD_LOCK_HELD=1 을 넘기면 자식은 획득을 생략한다.
set -euo pipefail
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
TABLE="$1"; shift
cd "$QL_HOME"
mkdir -p logs/equity
LOG="logs/equity/${TABLE}_$(date -u +%Y%m%dT%H%M%SZ).log"
LOCK=/tmp/quant_ledger_build.lock
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  flock -n 9 || { echo "another build is running — lock $LOCK"; exit 3; }
  export QL_BUILD_LOCK_HELD=1
fi
/usr/bin/time -v .venv/bin/python -m equity build "$TABLE" "$@" 2>&1 | tee "$LOG"
echo "log: $LOG"
