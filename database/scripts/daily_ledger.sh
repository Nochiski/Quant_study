#!/usr/bin/env bash
# 06:00 KST 수집 체인 — KRX 를 뺀 전 소스. 플랜 P1 Task 1.8 / 결정 R1.
#   순서: 캘린더 동기화 → D(직전 거래일) 판정 → 키움 마스터·WISE(daily_wise.sh, 매일) →
#         [D 미수집이면] 키움 4 TR fetch → KIS credit → DART 스윕·상세·문서 → 수집 요약 알림
#   어느 단계든 rc≠0 이면 그 단계에서 멈추고 crit. 07:00(v3 토큰 재발급) 전에 끝나야 한다(예산 36분).
#   사용: daily_ledger.sh [--date YYYYMMDD] [--dry-run] [--limit N]
#   환경: QL_KW_NOT_BEFORE=HH:MM (키움 fetch 하한 시각, P0 프로브 판독값. 기본 06:00)
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
LOCK=/tmp/quant_ledger_raw.lock
if [ -z "${QL_RAW_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "daily_ledger 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
    exit 3
  fi
  export QL_RAW_LOCK_HELD=1
fi
DATE_ARG=""; DRY=""; LIMIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --limit) LIMIT="--limit $2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_ledger_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
FAILED=""
step() {  # step <이름> <명령...> — rc≠0 이면 FAILED 에 이름을 적고 1 반환
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$name(rc=$rc)"; return 1; fi
  return 0
}
{
echo "════ [$(kst)] daily_ledger 시작 dry=${DRY:-no} ════"
scripts/sync_calendar.sh || echo "  ! 캘린더 동기화 실패 — 이전 복사본으로 진행"
D="${DATE_ARG:-$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
echo "  대상 거래일 D=$D"
# ① 소멸성 축(마스터·WISE)은 매일 — daily_wise.sh 는 raw 락을 물려받는다
if [ -z "$DRY" ]; then step "daily_wise" bash scripts/daily_wise.sh || true; fi
# D 가 이미 수집·판정 완료면 여기서 끝(주말·연휴에 같은 D 를 반복하지 않는다)
if [ -z "$DRY" ] && $PY -c 'import sys; from daily import runlog
rows=[r for r in runlog.recent("data/raw/daily_run.db", source="ledger_chain", limit=10) if r.date==sys.argv[1] and r.status=="ok"]
sys.exit(0 if rows else 1)' "$D"; then
  echo "  D=$D 는 이미 수집 완료 — 종료"
  echo "════ 종료 $(kst) ════"
else
  RID=""
  [ -z "$DRY" ] && RID=$($PY -c 'import sys; from daily import runlog; print(runlog.start("data/raw/daily_run.db", date=sys.argv[1], source="ledger_chain"))' "$D")
  # ② 키움 4 TR fetch(대기 테이블) → ③ KIS credit → ④ DART
  step "kiwoom fetch" $PY -m daily.kw_daily --date "$D" --fetch --not-before "${QL_KW_NOT_BEFORE:-06:00}" $DRY $LIMIT \
  && step "kis credit" $PY -m daily.kis_daily --date "$D" $DRY $LIMIT \
  && step "dart" $PY -m daily.dart_daily --date "$D" $DRY $LIMIT
  RC=$?
  if [ -n "$RID" ]; then
    $PY -c 'import sys; from daily import runlog; runlog.finish("data/raw/daily_run.db", int(sys.argv[1]), status=sys.argv[2], detail=sys.argv[3])' "$RID" "$([ $RC -eq 0 ] && echo ok || echo failed)" "${FAILED:-}"
  fi
  echo "════ 종료 rc=$RC $(kst) ════"
fi
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUMMARY=$(grep -E "^  |──── .* 종료" "$RUN" | tail -8 | tr '\n' ' ' | cut -c1-900)
if [ -n "$FAILED" ]; then
  scripts/notify.sh crit "daily_ledger 실패: $FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_ledger 완료" "$SUMMARY"
rm -f "$RUN"
