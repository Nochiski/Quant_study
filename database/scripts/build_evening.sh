#!/usr/bin/env bash
# 18:15 KST 잠정 빌드 트리거 — 플랜 v2 §4 Task B.1. 저녁 원장이 끝나면 stage·equity 잠정판을 짓는다.
#   사용: scripts/build_evening.sh [--date YYYYMMDD] [--dry-run]
#   크론(서버 TZ=UTC. 등록은 오케스트레이터): 15 9 * * 1-5 cd /home/kael/quant-ledger && scripts/build_evening.sh
#   순서: 오늘(KST) 거래일 판정 → 저녁 원장 완료 대기 → scripts/build_chain.sh evening
#   대기 규칙: data/deliver/ledger_evening.json 이 date==오늘 && kiwoom_rc==0 && wise_rc==0 이 될
#     때까지 60초 간격으로 본다. 한도는 18:40 KST(QL_EVENING_BUILD_DEADLINE 로 조정). DART 는
#     조건이 아니다 — 저녁 스코어링 경로 밖이라 실패해도 빌드는 간다. 다만 저 인계 파일 자체가
#     세 갈래가 다 끝난 뒤에 쓰이므로, 파일이 보이는 시점엔 dart.db 쓰기도 끝나 있다(스냅샷 안전).
#   휴장·대기 실패도 반드시 알린다 (결정 V2-7). 알림 없는 경로는 --dry-run 뿐이다.
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 빌드하지 않는다" >&2; exit 4; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
DATE_ARG=""; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"     # 저녁 슬롯은 당일 데이터를 받는다
mkdir -p logs/evening
LOG="logs/evening/build_${D}.log"
say() { echo "$1" | tee -a "$LOG"; }

is_trading_day() {
  $PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
sys.exit(0 if c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))) else 1)' "$1"
}
ledger_ready() {
  $PY - "$1" <<'PY'
import json
import sys
from pathlib import Path

path = Path("data/deliver/ledger_evening.json")
if not path.exists():
    raise SystemExit(1)
try:
    rep = json.loads(path.read_text(encoding="utf-8"))
except ValueError:
    raise SystemExit(1)      # 쓰는 중 — 다음 주기에 다시 본다
ready = (str(rep.get("date") or "") == sys.argv[1]
         and rep.get("kiwoom_rc") == 0 and rep.get("wise_rc") == 0)
print(f"  원장 보고 date={rep.get('date')} kiwoom_rc={rep.get('kiwoom_rc')} "
      f"wise_rc={rep.get('wise_rc')} dart_rc={rep.get('dart_rc')}(조건 아님)")
raise SystemExit(0 if ready else 1)
PY
}

say "════ [$(kst)] build_evening D=$D dry=${DRY:-no} ════"
if ! is_trading_day "$D"; then
  say "  D=$D 는 거래일이 아니다 — 잠정 빌드 건너뜀"
  [ -z "$DRY" ] && scripts/notify.sh info "잠정 빌드 휴장 — 건너뜀" "D=$D | 로그 $LOG"
  exit 0
fi
if [ -n "$DRY" ]; then
  say "  dry-run: 저녁 원장 대기·락·스냅샷 없이 계획만 출력한다"
  exec bash scripts/build_chain.sh evening --date "$D" --dry-run
fi

DEADLINE_HHMM="${QL_EVENING_BUILD_DEADLINE:-18:40}"
DEADLINE_TS=$(TZ=Asia/Seoul date -d "$(TZ=Asia/Seoul date +%Y-%m-%d) $DEADLINE_HHMM" +%s)
say "  저녁 원장 완료 대기 — 한도 $DEADLINE_HHMM KST, 60초 간격"
WAITED=0
while ! ledger_ready "$D" >> "$LOG" 2>&1; do
  if [ "$(date +%s)" -ge "$DEADLINE_TS" ]; then
    say "  ✗ $DEADLINE_HHMM KST 까지 저녁 원장이 완료되지 않았다 (대기 ${WAITED}초)"
    scripts/notify.sh crit "잠정 빌드 시작 불가 — 저녁 원장 미완료" \
      "D=$D | data/deliver/ledger_evening.json 이 date=$D kiwoom_rc=0 wise_rc=0 이 되지 않았다 (한도 $DEADLINE_HHMM KST, 대기 ${WAITED}초) | 로그 $LOG"
    exit 2
  fi
  sleep 60
  WAITED=$((WAITED + 60))
done
say "  저녁 원장 완료 확인 (대기 ${WAITED}초) — 잠정 빌드 시작 $(kst)"
exec bash scripts/build_chain.sh evening --date "$D"
