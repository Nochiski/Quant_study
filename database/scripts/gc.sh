#!/usr/bin/env bash
# quant-ledger 정리 — 기본은 dry-run(무엇을 지울지 출력만), `--apply` 로 실행. 플랜 P0 Task 0.5.
#   · data/stage/_tmp/doc/            프리패스 캐시 — 현재 stage 판이 선 스냅샷 + 가장 최근 1개만 남기고 삭제
#                                      (옛 규칙은 전삭제였다. 문서층 일일 증분이 붙은 뒤로는 그게 곧
#                                       3~4.5h 재파싱이고, 다음 증분이 붙잡을 base 캐시까지 없앤다 — DEFECT-D12)
#   · data/stage/_failed/, data/equity/_failed/   30일 초과 판정 파일 삭제
#   · logs/**                          로테이션은 scripts/rotate_logs.sh 에 위임(14일 gzip · 90일 삭제 · health 보존)
#   스냅샷(data/snapshots) GC 는 여기 없다 — stage 빌드에 내장한다(플랜 Task 4.2). 한 곳에서만.
#   빌드 락을 잡는다 — 같은 자원을 만지는 run_stage_all.sh·build_chain.sh·run_equity.sh 와 같은 락이다.
set -euo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
export PYTHONPATH="$PWD/src"
PY=.venv/bin/python
APPLY=0
# 주간 정리도 보고한다(결정 V2-7). set -e 로 죽는 어느 줄이든 warn 이 나간다.
trap 'rc=$?; [ "$APPLY" -eq 1 ] && scripts/notify.sh warn "gc 실패" "gc.sh line $LINENO rc=$rc — 로그를 확인한다" || true' ERR
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: gc.sh [--dry-run|--apply]" >&2; exit 2 ;;
esac
run() { if [ "$APPLY" -eq 1 ]; then "$@"; else echo "DRY: $*"; fi; }

# 일요일 04:30 크론과 사람이 돌리는 수동 빌드·프리패스가 겹치면 진행 중인 캐시를 통째로 지운다(D12).
# 락을 못 잡으면 정리를 건너뛴다 — 다음 주 일요일에 다시 돈다. 실패가 아니므로 rc 0.
LOCK=/tmp/quant_ledger_build.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "== 빌드 락 점유 중($LOCK) — 이번 gc 는 건너뛴다"
  [ "$APPLY" -eq 1 ] && scripts/notify.sh warn "gc 건너뜀" "다른 빌드가 $LOCK 을 쥐고 있다 — 캐시·_failed 정리 없이 종료" || true
  exit 0
fi

echo "== mode: $([ "$APPLY" -eq 1 ] && echo apply || echo dry-run)  $(date -u +%FT%TZ)"
if [ -d data/stage/_tmp/doc ]; then
  echo "== _tmp/doc: $(du -sh data/stage/_tmp/doc | cut -f1)"
  # 보존 = 현재 stage 판이 선 스냅샷의 캐시(그 판을 같은 입력으로 재빌드할 수 있어야 한다)
  #      + 가장 최근 mtime 캐시 1개(다음 증분 프리패스의 base).
  if DOC_KEEP=$($PY - <<'PYEOF'
from pathlib import Path

from stage import snapshot

root = Path("data/stage/_tmp/doc")
keep = set(snapshot.current_snapshot_ids(Path("data/stage")))
caches = [d for d in root.iterdir() if d.is_dir()]
if caches:
    keep.add(max(caches, key=lambda d: d.stat().st_mtime).name)
print(" ".join(sorted(keep)))
PYEOF
  ); then
    echo "   keep: ${DOC_KEEP:-없음}"
    for d in data/stage/_tmp/doc/*/; do
      [ -d "$d" ] || continue
      name=$(basename "$d")
      case " $DOC_KEEP " in
        *" $name "*) echo "   유지 $name" ;;
        *) echo "   삭제 $name"; run rm -rf "$d" ;;
      esac
    done
  else
    echo "   보존 목록 계산 실패 — _tmp/doc 은 건드리지 않는다(전삭제는 3~4.5h 재파싱이다)"
  fi
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
