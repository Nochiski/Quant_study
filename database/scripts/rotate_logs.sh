#!/usr/bin/env bash
# 로그 로테이션 — `logs/` 의 `*.log` 는 14일 초과분 gzip, `*.log.gz` 는 90일 초과분 삭제. 플랜 v1 §9 Task 6.2.
#   일요일 `gc.sh` 뒤에 돈다(gc.sh 끝에서 같은 모드로 호출한다). 기본은 dry-run — `--apply` 일 때만 실제로 건드린다.
#   · `logs/health/**` 는 건드리지 않는다. 워치독(`watchdog.sh`)과 일일 리포트(`daily_report.py`)가
#     `logs/health/<D>.json`·`stage_<D>_<basis>.json` 을 읽고, 건전성 판정은 전날 리포트를 기준선으로 쓴다(추적 규약).
#   · `logs/evening/`·`logs/morning/` 같은 하위 디렉터리도 대상이다(find 재귀).
#   · 로그 gzip 규칙은 여기 한 곳뿐이다(`gc.sh` 의 자체 7일 gzip 줄은 2026-09-11 에 제거).
#   사용: rotate_logs.sh [--dry-run|--apply]
set -euo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
GZIP_DAYS=14
PURGE_DAYS=90
APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: rotate_logs.sh [--dry-run|--apply]" >&2; exit 2 ;;
esac
run() { if [ "$APPLY" -eq 1 ]; then "$@"; else echo "DRY: $*"; fi; }

echo "== rotate_logs mode: $([ "$APPLY" -eq 1 ] && echo apply || echo dry-run)  $(date -u +%FT%TZ)"
if [ ! -d logs ]; then
  echo "== logs 디렉터리 없음 — 할 일 없음"
  exit 0
fi
n_gzip=$(find logs -type f -name '*.log' -mtime +"$GZIP_DAYS" -not -path 'logs/health/*' | wc -l | tr -d ' ')
echo "== gzip: ${GZIP_DAYS}일 초과 .log $n_gzip 건"
find logs -type f -name '*.log' -mtime +"$GZIP_DAYS" -not -path 'logs/health/*' -print0 \
  | while IFS= read -r -d '' f; do run gzip -qf "$f"; done   # -f: 동명 .gz 가 있어도 죽지 않는다(set -e)
n_purge=$(find logs -type f -name '*.log.gz' -mtime +"$PURGE_DAYS" -not -path 'logs/health/*' | wc -l | tr -d ' ')
echo "== 삭제: ${PURGE_DAYS}일 초과 .log.gz $n_purge 건"
find logs -type f -name '*.log.gz' -mtime +"$PURGE_DAYS" -not -path 'logs/health/*' -print0 \
  | while IFS= read -r -d '' f; do run rm -f "$f"; done
n_health=$(find logs/health -type f 2>/dev/null | wc -l | tr -d ' ')
echo "== logs/health 보존 $n_health 건 (삭제 대상 아님)"
echo "== done  압축 $n_gzip 건 · 삭제 $n_purge 건"
