#!/usr/bin/env bash
# quant-ledger 정리 — 기본은 dry-run(무엇을 지울지 출력만), `--apply` 로 실행. 플랜 P0 Task 0.5.
#   · data/stage/_tmp/doc/            프리패스 캐시 전부 삭제(재파싱 3~4.5h 로 재현 가능)
#   · data/stage/_failed/, data/equity/_failed/   30일 초과 판정 파일 삭제
#   · logs/**                          로테이션은 scripts/rotate_logs.sh 에 위임(14일 gzip · 90일 삭제 · health 보존)
#   스냅샷(data/snapshots) GC 는 여기 없다 — stage 빌드에 내장한다(플랜 Task 4.2). 한 곳에서만.
set -euo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
APPLY=0
# 주간 정리도 보고한다(결정 V2-7). set -e 로 죽는 어느 줄이든 warn 이 나간다.
trap 'rc=$?; [ "$APPLY" -eq 1 ] && scripts/notify.sh warn "gc 실패" "gc.sh line $LINENO rc=$rc — 로그를 확인한다" || true' ERR
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: gc.sh [--dry-run|--apply]" >&2; exit 2 ;;
esac
run() { if [ "$APPLY" -eq 1 ]; then "$@"; else echo "DRY: $*"; fi; }

echo "== mode: $([ "$APPLY" -eq 1 ] && echo apply || echo dry-run)  $(date -u +%FT%TZ)"
if [ -d data/stage/_tmp/doc ]; then
  echo "== _tmp/doc: $(du -sh data/stage/_tmp/doc | cut -f1)"
  run rm -rf data/stage/_tmp/doc
fi
for d in data/stage/_failed data/equity/_failed; do
  [ -d "$d" ] || continue
  n=$(find "$d" -type f -mtime +30 | wc -l)
  echo "== $d: 30일 초과 $n 건"
  find "$d" -type f -mtime +30 -print0 | while IFS= read -r -d '' f; do run rm -f "$f"; done
done
bash scripts/rotate_logs.sh "$([ "$APPLY" -eq 1 ] && echo --apply || echo --dry-run)"   # 14일 초과 gzip · 90일 초과 gz 삭제 · health 보존
echo "== done"
[ "$APPLY" -eq 1 ] && scripts/notify.sh info "gc 완료 $(TZ=Asia/Seoul date +%m-%d\ %H:%M)" "캐시·_failed 정리 + rotate_logs 완료 — 디스크 여유 $(df -Pk . | awk 'NR==2 {printf "%d", $4/1024/1024}') GB" || true
