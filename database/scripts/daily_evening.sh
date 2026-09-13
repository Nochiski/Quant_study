#!/usr/bin/env bash
# 18:05 KST 저녁 원장 슬롯 — 그날 확정된 축을 그날 저녁에 원장에 넣는다. 플랜 v2 §3 Task A.2.
#   순서: 원장 락 → 캘린더 동기화 → D(오늘 KST) 거래일 판정 → [D 미완료면] 병렬 3갈래 →
#         rc 취합 → runlog(evening_chain) → data/deliver/ledger_evening.json → 알림
#   ① 키움 ka10060·ka10014 fetch + --commit — KRX 대조 없이 원장 직행(결정 V2-1). 대조 상대인 KRX 는
#      T+1 08:00 공표라 그날 저녁엔 없다. 오염 게이트(b)만 통과 조건이다(ka10008 이 없으니 basis=skipped).
#   ② DART 당일 스윕·상세 — 접수 마감 18:00 직후.
#   ③ WISE 스냅샷 full — 06:00 에서 18:05 로 이동(결정 V2-3). 키움 마스터는 06:00 daily_wise.sh 에 남는다.
#   ①·③ 의 종료 시각을 로그와 deliver JSON 에 남긴다 — 18:15 잠정 빌드의 트리거 근거다.
#   휴장·이미 완료도 info 로 보고한다(결정 V2-7: 무소식과 고장을 구분한다). dry-run 만 알림 없음.
#   사용: daily_evening.sh [--date YYYYMMDD] [--dry-run] [--limit N]
#   환경: QL_EVENING_NOT_BEFORE=HH:MM (키움 fetch 하한 시각. 기본 18:00)
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
LOCK=/tmp/quant_ledger_raw.lock
if [ -z "${QL_RAW_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "daily_evening 락 실패" "다른 원장 작업이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
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
deliver_json() {
  # 18:15 잠정 빌드·워치독이 읽는 인계 파일. dart_rc 가 빈 문자열이면 아직 도는 중(null) — 빌드 조건이 아니다.
  # 임시 파일에 쓰고 원자 교체한다(읽는 쪽이 부분 JSON 을 보지 않게).
  $PY -c 'import datetime as dt, json, os, sys
d, rc_kw, rc_dart, rc_wise, kw_at, wise_at = sys.argv[1:7]
os.makedirs("data/deliver", exist_ok=True)
payload = {"date": d,
           "finished_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "kiwoom_rc": int(rc_kw), "dart_rc": (int(rc_dart) if rc_dart != "" else None),
           "wise_rc": int(rc_wise), "dart_done": rc_dart != "",
           "kiwoom_done_at": kw_at or None, "wise_done_at": wise_at or None}
tmp = "data/deliver/ledger_evening.json.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
os.replace(tmp, "data/deliver/ledger_evening.json")
print("  deliver/ledger_evening.json 기록 " + json.dumps(payload, ensure_ascii=False))' "$@"
}
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_evening_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
D=""; FAILED=""; SKIPPED=""
RC_KW=0; RC_DART=0; RC_WISE=0
KW_LOG=""; DART_LOG=""; WISE_LOG=""; KW_DONE=""; WISE_DONE=""
# 세 갈래는 서로 다른 원장(kiwoom.db · dart.db · wise.db)만 건드리므로 병렬이 안전하다.
# 각자 자기 로그에 쓰고 rc 는 `wait <pid>` 로 따로 받는다 — 하나가 죽어도 나머지는 끝까지 간다.
branch_kiwoom() {
  # 키움 거래량은 18:05 에 시간외 반영 전 값이라(09-11 실측 315/2,650 이 KRX 확정치보다 작음) 19:05 부터가
  # 최종이다(프로브: 19:05 갱신 뒤 다음 날 07:05 까지 불변). 사용자 결정(09-13, 결정 10-(d)): 키움 갈래만
  # 19:05 까지 기다렸다 받는다. DART·WISE 는 18:05 그대로. dry-run 은 기다리지 않는다.
  local wait_until="${QL_KW_EVENING_HHMM:-1905}"
  if [ -z "$DRY" ]; then
    while [ "$(TZ=Asia/Seoul date +%H%M)" -lt "$wait_until" ]; do sleep 60; done
  fi
  echo "──── 키움 fetch+commit 시작 $(kst) (하한 ${wait_until:0:2}:${wait_until:2:2} KST) ────"
  $PY -m daily.kw_daily --date "$D" --fetch --tr ka10060,ka10014 --commit \
     --not-before "${QL_EVENING_NOT_BEFORE:-19:00}" $DRY $LIMIT
  local rc=$?
  echo "DONE_AT=$(TZ=Asia/Seoul date '+%Y-%m-%dT%H:%M:%S+09:00')"
  echo "──── 키움 종료 rc=$rc $(kst) ────"
  return "$rc"
}
branch_dart() {
  echo "──── DART 시작 $(kst) ────"
  $PY -m daily.dart_daily --date "$D" $DRY $LIMIT
  local rc=$?
  echo "──── DART 종료 rc=$rc $(kst) ────"
  return "$rc"
}
branch_wise() {
  echo "──── WISE 스냅샷 시작 $(kst) ────"
  # dry-run 은 3종목만 — backfill_wise.py 에 --dry-run 이 없다(원장 무변경 보장 불가)
  if [ -n "$DRY" ]; then
    $PY src/backfill_wise.py --mode full --limit 3 2>&1 | grep -vE "Deprecation|datetime\.datetime"
  else
    $PY src/backfill_wise.py --mode full 2>&1 | grep -vE "Deprecation|datetime\.datetime"
  fi
  local rc=${PIPESTATUS[0]}
  echo "DONE_AT=$(TZ=Asia/Seoul date '+%Y-%m-%dT%H:%M:%S+09:00')"
  echo "──── WISE 종료 rc=$rc $(kst) ────"
  return "$rc"
}
{
echo "════ [$(kst)] daily_evening 시작 dry=${DRY:-no} ════"
scripts/sync_calendar.sh || echo "  ! 캘린더 동기화 실패 — 이전 복사본으로 진행"
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"
echo "  대상 거래일 D=$D (기본은 오늘 KST — 저녁 슬롯은 당일 데이터를 받는다)"
if ! $PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
sys.exit(0 if c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))) else 1)' "$D"; then
  SKIPPED="휴장"
  echo "  D=$D 는 거래일이 아니다 — 건너뜀"
elif [ -z "$DRY" ] && $PY -c 'import sys; from daily import runlog
rows=[r for r in runlog.recent("data/raw/daily_run.db", source="evening_chain", limit=10) if r.date==sys.argv[1] and r.status=="ok"]
sys.exit(0 if rows else 1)' "$D"; then
  SKIPPED="이미 완료"
  echo "  D=$D 는 저녁 슬롯을 이미 마쳤다 — 종료"
else
  KW_LOG="logs/evening_kiwoom_${D}.log"
  DART_LOG="logs/evening_dart_${D}.log"
  WISE_LOG="logs/evening_wise_${D}.log"
  RID=""
  [ -z "$DRY" ] && RID=$($PY -c 'import sys; from daily import runlog; print(runlog.start("data/raw/daily_run.db", date=sys.argv[1], source="evening_chain"))' "$D")
  branch_kiwoom > "$KW_LOG"   2>&1 & PID_KW=$!
  branch_dart   > "$DART_LOG" 2>&1 & PID_DART=$!
  branch_wise   > "$WISE_LOG" 2>&1 & PID_WISE=$!
  # 잠정 빌드(18:15 build_evening.sh)의 조건은 키움·WISE 둘뿐이다(플랜 §2). DART 는 30분짜리라 그 종료를
  # 기다리면 빌드가 18:35 뒤로 밀리고 19:00 워치독이 매일 오탐한다(검수 R4-01). 그래서 인계 파일을 두 번 쓴다 —
  # 키움·WISE 가 끝나면 dart_rc=null 로 먼저(빌드 트리거), DART 가 끝나면 최종값으로 다시.
  wait "$PID_KW";   RC_KW=$?
  wait "$PID_WISE"; RC_WISE=$?
  KW_DONE=$(grep -m1 '^DONE_AT=' "$KW_LOG" | cut -d= -f2-)
  WISE_DONE=$(grep -m1 '^DONE_AT=' "$WISE_LOG" | cut -d= -f2-)
  echo "  종료 시각 — 키움 ${KW_DONE:-미기록} · WISE ${WISE_DONE:-미기록} (18:15 잠정 빌드 트리거 근거) $(kst)"
  [ -z "$DRY" ] && deliver_json "$D" "$RC_KW" "" "$RC_WISE" "$KW_DONE" "$WISE_DONE"
  wait "$PID_DART"; RC_DART=$?
  echo "  DART 종료 rc=$RC_DART $(kst)"
  for f in "$KW_LOG" "$DART_LOG" "$WISE_LOG"; do
    echo "──── 갈래 로그 $f ────"
    cat "$f"
  done
  [ "$RC_KW" -ne 0 ]   && FAILED="$FAILED 키움(rc=$RC_KW)"
  [ "$RC_DART" -ne 0 ] && FAILED="$FAILED DART(rc=$RC_DART)"
  [ "$RC_WISE" -ne 0 ] && FAILED="$FAILED WISE(rc=$RC_WISE)"
  if [ -n "$RID" ]; then
    $PY -c 'import sys; from daily import runlog; runlog.finish("data/raw/daily_run.db", int(sys.argv[1]), status=sys.argv[2], detail=sys.argv[3])' \
      "$RID" "$([ -z "$FAILED" ] && echo ok || echo failed)" \
      "kiwoom_rc=$RC_KW dart_rc=$RC_DART wise_rc=$RC_WISE kiwoom_done_at=${KW_DONE:-none} wise_done_at=${WISE_DONE:-none}${FAILED:+ failed=$FAILED}"
  fi
  [ -z "$DRY" ] && deliver_json "$D" "$RC_KW" "$RC_DART" "$RC_WISE" "$KW_DONE" "$WISE_DONE"
fi
echo "════ 종료 키움=$RC_KW DART=$RC_DART WISE=$RC_WISE $(kst) ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUM_KW=$(grep -E "^\[kw_daily\] (fetch status|commit )" "$RUN" | tail -2 | tr '\n' ' ')
SUM_DART=$(grep -E "^── DART 일일 증분|^  공시 " "$RUN" | tail -2 | tr '\n' ' ')
SUM_WISE=$(grep -E "④ 종료|⚠⚠|무커버" "$RUN" | tail -2 | tr '\n' ' ')
SUMMARY=$(printf 'D=%s | 키움 %s| DART %s| WISE %s| 종료 키움 %s · WISE %s' \
  "$D" "${SUM_KW:-없음 }" "${SUM_DART:-없음 }" "${SUM_WISE:-없음 }" "${KW_DONE:-?}" "${WISE_DONE:-?}" | cut -c1-900)
if [ -n "$SKIPPED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh info "daily_evening $SKIPPED — 건너뜀" "D=$D | 로그 $LOG"
  rm -f "$RUN"; exit 0
fi
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_evening 실패:$FAILED" "$SUMMARY | 로그 $LOG $KW_LOG $DART_LOG $WISE_LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_evening 완료" "$SUMMARY"
rm -f "$RUN"
