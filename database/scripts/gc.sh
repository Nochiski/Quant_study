#!/usr/bin/env bash
# quant-ledger 정리 — 기본은 dry-run(무엇을 지울지 출력만), `--apply` 로 실행. 플랜 P0 Task 0.5.
#   · data/stage/_tmp/doc/            프리패스 캐시 전부 삭제(재파싱 3~4.5h 로 재현 가능)
#   · data/stage/_failed/, data/equity/_failed/   30일 초과 판정 파일 삭제
#   · logs/**/*.log                    7일 초과 gzip
#   스냅샷(data/snapshots) GC 는 여기 없다 — stage 빌드에 내장한다(플랜 Task 4.2). 한 곳에서만.
set -euo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
APPLY=0
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
n=$(find logs -type f -name '*.log' -mtime +7 | wc -l)
echo "== logs: 7일 초과 .log $n 건 → gzip"
find logs -type f -name '*.log' -mtime +7 -print0 | while IFS= read -r -d '' f; do run gzip -q "$f"; done
echo "== done"
