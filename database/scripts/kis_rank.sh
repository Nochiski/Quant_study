#!/usr/bin/env bash
# KIS 관심종목 등록 상위 · HTS 조회 상위 스냅숏 크론 래퍼(N-66) — 슬롯 판정 · 수집 · 저장은 `python -m daily.kis_rank` 한 곳.
#   예정 크론(서버 TZ=UTC — crontab = KST − 9. 등록은 오케스트레이터가 한다):
#     30 23 * * 0-4     cd ~/quant-ledger && /bin/bash scripts/kis_rank.sh >> logs/cron_kis_rank.log 2>&1   # 08:30 KST(월~금)
#     0,30 0-11 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/kis_rank.sh >> logs/cron_kis_rank.log 2>&1   # 09:00~20:30 KST
#   하루 요약(ok 슬롯 수)은 `watchdog.sh kis_rank`(20:45 KST) 몫이다.
#   자기 락만 비대기로 잡는다(쥐고 있으면 rc 3) — 원장 락(scripts/raw_lock.sh)은 기다리지 않는다. 기다리면 슬롯 시각이
#   밀려 그날 값을 잃는다(장 마감 수집기 T-4 와 같은 이유).
#   rc → 알림(logs/notify.log 기록만 — 텔레그램 금지): 5 warn(토큰 · 유량 · 호출 실패) · 2 crit(칸 · 행 수 이상) ·
#   그 밖의 rc 도 crit(1 = 파이썬 예상 밖 예외). 0 · 3 은 남기지 않는다 — 슬롯마다 info 는 하루 25줄 소음이다.
#   QL_KIS_RANK_LOCK 은 테스트 전용(락 파일 경로 덮어쓰기)이다.
set -uo pipefail
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4) — 배포 rsync --delete 밖, 바깥 값·옛 시스템 파일을 쓰지 않는다
LOCK="${QL_KIS_RANK_LOCK:-/tmp/quant_ledger_kis_rank.lock}"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] kis_rank 앞 실행이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
  exit 3
fi
OUT=$(.venv/bin/python -m daily.kis_rank "$@" 2>&1)
RC=$?
printf '%s\n' "$OUT"
LAST=$(printf '%s\n' "$OUT" | tail -1)
case "$RC" in
  0|3) ;;
  5) scripts/notify.sh warn "kis_rank 슬롯 일부 못 받음" "$LAST" ;;
  2) scripts/notify.sh crit "kis_rank 응답 칸 · 행 수 이상" "$LAST | 원문 data/raw/kis_rank.db kis_rank_runs.rows_json" ;;
  *) scripts/notify.sh crit "kis_rank 예상 밖 종료 rc=$RC" "$LAST | 로그 logs/cron_kis_rank.log" ;;
esac
exit "$RC"
