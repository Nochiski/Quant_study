#!/bin/bash
# 매일 KST 00:01 데일리 재기동 — DART 잔여 백필(stage 2 꼬리 → 3 → 4 → 스윕)을 완주할 때까지.
#   · 전날 프로세스를 안전 종료 후 새로 시작한다: budget_used 는 콜 로그 파생이라 자정에
#     자동 리셋되지만, 프로세스 안의 blocked(접은 키 집합)는 재기동으로만 비워진다 (검토 C-D1)
#   · 각 단계는 예산 소진·완료 시 스스로 종료. 전부 끝난 날은 전 단계가 즉시 무동작 통과
#   · 유닛 원자성 검증됨(2026-08-27 검토 C-5) — 중간 종료해도 부분 적재 없음, 재수집 멱등
#   · 전 작업 완료 확인 후 이 크론 줄을 제거할 것
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger"
LOG="logs/daily_dart_$(TZ=Asia/Seoul date +%m%d).log"

{
echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] 데일리 재기동 ════"

# ① 이전 인스턴스·DART 수집 프로세스 안전 종료 (KIS 수집은 패턴 밖 — 건드리지 않는다)
SELF=$$
for pid in $(pgrep -f "daily_dart\.sh"); do
  [ "$pid" != "$SELF" ] && [ "$pid" != "$PPID" ] && kill "$pid" 2>/dev/null
done
PIDS=$(pgrep -f "src/backfill_dart\.py --corps|src/sweep_disclosure\.py")
if [ -n "$PIDS" ]; then
  echo "  전일 프로세스 종료: $PIDS"
  kill $PIDS 2>/dev/null; sleep 5; kill -9 $PIDS 2>/dev/null; sleep 1
fi

run() {
  L="$1"; shift
  echo "──── $L 시작 $(TZ=Asia/Seoul date '+%H:%M:%S KST') ────"
  "$@"
  echo "──── $L 종료 rc=$? $(TZ=Asia/Seoul date '+%H:%M:%S KST') ────"
}
Y2=$(python3 -c "print(','.join(str(y) for y in range(2016,2026)))")
run "stage 2 잔여" .venv/bin/python src/backfill_dart.py --corps data/corps.txt --stage 2 \
                   --reprt 11012,11013,11014 --years "$Y2" --quota-window midnight
run "stage 3"      .venv/bin/python src/backfill_dart.py --corps data/corps.txt --stage 3 \
                   --quota-window midnight
run "stage 4"      .venv/bin/python src/backfill_dart.py --corps data/corps.txt --stage 4 \
                   --quota-window midnight
run "공시 스윕"     .venv/bin/python src/sweep_disclosure.py --quota-window midnight

echo "──── 키별 소진 (KST 오늘) ────"
timeout 90 sqlite3 "file:data/raw/dart.db?mode=ro" \
  "SELECT '  '||key_id||'  '||printf('%,d',COUNT(*)) FROM dart_call_log
   WHERE ts >= strftime('%Y-%m-%dT%H:%M:%S','now','+9 hours','start of day','-9 hours')
   GROUP BY key_id;"
} >> "$LOG" 2>&1
