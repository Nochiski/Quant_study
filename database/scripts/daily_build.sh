#!/usr/bin/env bash
# 08:10 KST 빌드 체인 — KRX(T+1 08:00 공표) → 키움 KRX 대조·머지 → 원장 건전성 → 확정 빌드 → 요약.
# 플랜 P1 Task 1.8 / 결정 R1. 확정 빌드(stage·equity basis=morning)는 scripts/build_morning.sh 가 한다
# (플랜 v2 Task B.1). `--no-build` 를 주면 원장 단계에서 멈춘다 — 크론의 --no-build 는 오케스트레이터가 뗀다.
#   사용: daily_build.sh [--date YYYYMMDD] [--no-build] [--dry-run] [--limit N]
#   환경: QL_SKIP_KW=1 이면 키움 merge 를 건너뛰고 건전성 판정의 kiwoom 항목을 skip 한다(앱키 분리 전 임시)
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
LOCK=/tmp/quant_ledger_raw.lock
if [ -z "${QL_RAW_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "daily_build 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
    exit 3
  fi
  export QL_RAW_LOCK_HELD=1
fi
DATE_ARG=""; DRY=""; LIMIT=""; NOBUILD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --limit) LIMIT="--limit $2"; shift 2 ;;
    --no-build) NOBUILD=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_build_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
FAILED=""
step() {
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$name(rc=$rc)"; return 1; fi
  return 0
}
krx_step() {  # KRX D + 최근 10거래일 pending 재수집. pending 이면 10분 간격 최대 6회(08:10→09:10)
  local d="$1" from to pend
  from=$($PY -c 'import datetime as dt,sys; from daily import calendar as c
d=sys.argv[1]; print(c.load().prev_trading_day(dt.date(int(d[:4]),int(d[4:6]),int(d[6:8])), n=10).strftime("%Y-%m-%d"))' "$d")
  to="${d:0:4}-${d:4:2}-${d:6:2}"
  for attempt in 1 2 3 4 5 6; do
    pend=$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT group_concat(DISTINCT bas_dd) FROM ingest_log WHERE status='pending' AND bas_dd>='${from//-/}'" 2>/dev/null || true)
    $PY src/backfill_krx.py --from "$from" --to "$to" ${pend:+--refetch "$pend"} ${LIMIT:+$LIMIT} || return 1
    if [ "$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT COUNT(*) FROM ingest_log WHERE bas_dd='$d' AND status='pending'")" = "0" ]; then return 0; fi
    echo "  KRX D=$d 아직 미공표(pending) — 10분 뒤 재시도 ($attempt/6)"
    [ -n "$DRY" ] && return 3
    sleep 600
  done
  return 3
}
{
echo "════ [$(kst)] daily_build 시작 dry=${DRY:-no} no_build=${NOBUILD:-0} ════"
D="${DATE_ARG:-$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
echo "  대상 거래일 D=$D"
HSKIP=""
if [ -n "${QL_SKIP_KW:-}" ]; then
  echo "  QL_SKIP_KW=1 — 키움 merge 건너뜀, 건전성 판정에서 kiwoom 항목은 skip"
  HSKIP="--skip kiwoom"
fi
# 외국인 보유(ka10008)는 T-1 행이 07시 전후에 정정된다(프로브 실측 09-10: 삼성전자·SK하이닉스) — 06:00 이 아니라
# 여기서 받는다. 다른 세 TR 의 incoming 은 06:00 체인이 세워 둔 것을 그대로 쓴다(--tr 은 자기 TR 만 비운다).
kw_fetch_fh() { if [ -n "${QL_SKIP_KW:-}" ]; then return 0; fi; $PY -m daily.kw_daily --date "$D" --fetch --tr ka10008 --not-before "${QL_KW_FH_NOT_BEFORE:-07:10}" $DRY $LIMIT; }
kw_merge() { if [ -n "${QL_SKIP_KW:-}" ]; then return 0; fi; $PY -m daily.kw_daily --date "$D" --merge $DRY $LIMIT; }
if [ -n "$DRY" ]; then
  echo "  dry-run: KRX 수집 단계는 건너뛴다(backfill_krx.py 에 dry-run 이 없다 — 원장 무변경 보장)"
  step "kiwoom fetch ka10008" kw_fetch_fh \
  && step "kiwoom merge" kw_merge \
  && step "ledger_health" $PY -m daily.ledger_health --date "$D" --out "$(mktemp -d)" $HSKIP
else
  step "krx" krx_step "$D" \
  && step "kiwoom fetch ka10008" kw_fetch_fh \
  && step "kiwoom merge" kw_merge \
  && step "ledger_health" $PY -m daily.ledger_health --date "$D" $HSKIP
fi
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$NOBUILD" ] && [ -z "$DRY" ]; then
  # 원장 게이트가 통과한 날만 확정판을 짓는다. 빌드 락은 build_chain 이 새로 잡고(raw 락은 물려준다),
  # stage·equity·인계 JSON·스냅샷 GC·알림은 전부 build_morning 안에 있다.
  export QL_BUILD_LOCK_HELD=""
  step "build_morning" bash scripts/build_morning.sh --date "$D" || true
fi
# 통합 일일 리포트는 읽기 전용이라 게이트 실패일·--no-build 에도 돈다(가장 필요한 날이 실패일이다. 검수 R4-03).
[ -z "$DRY" ] && [ -x scripts/daily_report.py ] && { $PY scripts/daily_report.py --date "$D" || true; }
echo "════ 종료 rc=$RC $(kst) ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUMMARY=$(grep -E "^원장 건전성|──── .* 종료|아직 미공표" "$RUN" | tail -8 | tr '\n' ' ' | cut -c1-900)
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_build 실패: $FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_build 완료" "$SUMMARY"
rm -f "$RUN"
