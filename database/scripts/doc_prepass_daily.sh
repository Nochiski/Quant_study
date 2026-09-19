#!/usr/bin/env bash
# 문서층 프리패스 일일 증분 — 기준 캐시를 이어받아 새 스냅샷의 차집합만 파싱한다(플랜 09-19 Task 5.3).
#   scripts/doc_prepass_daily.sh <snapshot-id> [workers]      (빌드 체인의 stage 전량 **앞에서** 부른다)
#   · 기준(base) = data/stage/_tmp/doc/*/summary.json 중 status=ok 인 것 중 mtime 최신(자기 자신 제외)
#   · 기준이 없으면 "no base cache" 로 rc 0 — 전량 프리패스(3~4.5h)는 사람이 창을 골라 1회 돌린다.
#     그동안 stg_doc_* 4표는 run_stage_all 이 종전대로 건너뛴다(캐시 없음 = skip).
#   · 이 스냅샷 캐시가 이미 ok 면 아무것도 하지 않고 rc 0 — 체인 재실행은 멱등이어야 한다.
#   · 로그 logs/doc_prepass/<snap>.log. rc 는 그대로 전파한다(호출자가 기록형으로 다룬다).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 4
SNAP="${1:?usage: doc_prepass_daily.sh <snapshot-id> [workers]}"
WORKERS="${2:-3}"
CACHE=data/stage/_tmp/doc
mkdir -p logs/doc_prepass
LOG="logs/doc_prepass/$SNAP.log"

ok_cache() { [ -f "$1" ] && grep -q '"status": "ok"' "$1"; }

if ok_cache "$CACHE/$SNAP/summary.json"; then
  echo "doc_prepass: $SNAP 캐시가 이미 ok — 건너뜀" | tee -a "$LOG"
  exit 0
fi
BASE=""
while IFS= read -r f; do
  d=$(basename "$(dirname "$f")")
  [ "$d" = "$SNAP" ] && continue
  ok_cache "$f" || continue
  BASE="$d"; break
done < <(ls -1t "$CACHE"/*/summary.json 2>/dev/null)

if [ -z "$BASE" ]; then
  echo "doc_prepass: $CACHE 에 쓸 수 있는 기준 캐시가 없다 — 건너뜀 (전량 1회: PYTHONPATH=src" \
       ".venv/bin/python -m stage.doc_prepass --snapshot-id $SNAP --workers $WORKERS)" | tee -a "$LOG"
  exit 0
fi

echo "=== doc_prepass $SNAP ← base $BASE workers=$WORKERS $(date -u +%FT%TZ)" >> "$LOG"
env PYTHONPATH=src .venv/bin/python -m stage.doc_prepass --snapshot-id "$SNAP" \
    --base-snapshot "$BASE" --workers "$WORKERS" >> "$LOG" 2>&1
RC=$?
echo "=== doc_prepass $SNAP rc=$RC $(date -u +%FT%TZ)" >> "$LOG"
tail -n 3 "$LOG"
exit "$RC"
