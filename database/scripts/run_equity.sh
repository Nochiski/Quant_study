#!/usr/bin/env bash
# equity 테이블 1개 빌드 — 서버 전용. 사용: scripts/run_equity.sh <table> [equity CLI 추가 인자]
# stage 규약(run_stage.sh)과 같다: QL_HOME·PYTHONPATH 고정, /usr/bin/time -v 로 RSS·초 기록, flock 직렬.
set -euo pipefail
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
TABLE="$1"; shift
cd "$QL_HOME"
mkdir -p logs/equity
LOG="logs/equity/${TABLE}_$(date -u +%Y%m%dT%H%M%SZ).log"
exec 9>/tmp/quant_ledger_equity.lock
flock -n 9 || { echo "another equity build is running — lock /tmp/quant_ledger_equity.lock"; exit 3; }
/usr/bin/time -v .venv/bin/python -m equity build "$TABLE" "$@" 2>&1 | tee "$LOG"
echo "log: $LOG"
