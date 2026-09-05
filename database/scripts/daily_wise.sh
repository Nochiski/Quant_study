#!/bin/bash
# 매일 KST 06:00 — WISE 컨센서스 일일 수집 체인 (2026-09-02 18:00→06:00 변경).
#   ① 키움 마스터 스냅샷(2콜) — 유니버스 최신화 + 관리·정지 상태 이력
#   ② WISE 수집 — 매일 full (전 종목 커버리지 재프로브)
#   (③ v3 미러 sync_v3 는 2026-09-02 중단 — 직접 수집이 같은 값을 커버. 과거분 ~09-02 는 동결 보관)
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger
LOG="logs/daily_wise_$(TZ=Asia/Seoul date +%m%d).log"
{
echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] WISE 데일리 ════"
.venv/bin/python src/master_daily.py 2>&1 | grep -v Deprecation
# 항상 full — 무커버 재프로브가 +3,700요청(+1분)뿐이라, 신규 커버리지 개시를
# 주 1회가 아니라 "다음 날"에 잡는 쪽이 이득이다 (2026-09-01 결정)
MODE=full
echo "──── backfill_wise --mode $MODE ────"
.venv/bin/python src/backfill_wise.py --mode "$MODE" 2>&1 | grep -vE "Deprecation|datetime\.datetime"
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') ════"
} >> "$LOG" 2>&1
