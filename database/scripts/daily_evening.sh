#!/usr/bin/env bash
# 18:05 KST 저녁 원장 슬롯 — 그날 확정된 축을 그날 저녁에 원장에 넣는다. 플랜 v2 §3 Task A.2.
#   순서: 원장 락 → 캘린더 동기화(휴장 파일 내보내기 스위치가 켜져 있으면 건너뜀 — T-48) → D(오늘 KST) 거래일 판정 →
#         [D 미완료면] 병렬 3갈래 →
#         rc 취합 → runlog(evening_chain) → data/deliver/ledger_evening.json → 알림
#         → [키움 rc 0 이면] 원장 락을 놓고 장 마감 판 재반영 훅 scripts/postclose_chain.sh refill(컷오버 PR-8 ⑦)
#   ① 키움 ka10060·ka10014 fetch + --commit — KRX 대조 없이 원장 직행(결정 V2-1). 대조 상대인 KRX 는
#      T+1 08:00 공표라 그날 저녁엔 없다. 오염 게이트(b)만 통과 조건이다(ka10008 이 없으니 basis=skipped).
#   ② DART 당일 스윕·상세 — 접수 마감 18:00 직후.
#   ③ WISE 스냅샷 full — 06:00 에서 18:05 로 이동(결정 V2-3). 키움 마스터는 06:00 daily_wise.sh 에 남는다.
#   ①·③ 의 종료 시각을 로그와 deliver JSON 에 남긴다 — 18:15 잠정 빌드의 트리거 근거다.
#   휴장·이미 완료도 info 로 보고한다(결정 V2-7: 무소식과 고장을 구분한다). dry-run 만 알림 없음.
#   사용: daily_evening.sh [--date YYYYMMDD] [--dry-run] [--limit N]
#   환경: QL_EVENING_NOT_BEFORE=HH:MM (키움 fetch 하한 시각. 기본 20:00 = 애프터마켓 마감) · QL_KW_EVENING_HHMM (대기 시각, 기본 2105)
set -uo pipefail
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4) — 배포 rsync --delete 밖, 바깥 값·옛 시스템 파일을 쓰지 않는다
PY=.venv/bin/python
DATE_ARG=""; DRY=""; LIMIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --limit) LIMIT="--limit $2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
# 원장 락 — 다른 원장 작업(늦어진 08:10 확정 체인·손 작업 등)이 쥐고 있으면 건너뛰지 않고 끝날 때까지
#   기다렸다 이어서 돈다(P9, 시간 한도 없음 — 배포 묶음 5-3, B-52). 예전 `flock -n` 은 그날 저녁 슬롯
#   전체(키움 소멸성 수급·WISE·DART 첫 런)를 잃었다. 대기 시작은 실시간 출력과 notify info(dry-run 제외)에
#   남는다. 같은 스크립트의 대기자는 하나 — 이미 기다리는 실행이 있으면 info 후 rc 3, 대기형 flock 이
#   실패하면 warn 후 rc 3(락 없이 원장을 쓰지 않는다). 인자를 먼저 읽는 것은 대기 알림이 dry-run 인지
#   알아야 해서다. 규칙(QL_RAW_LOCK_HELD · QL_RAW_LOCK_FILE 테스트 전용)은 scripts/raw_lock.sh 한 곳.
#   대기 중 KST 날짜가 바뀌면(자정을 넘는 점유) crit 후 rc 3 으로 멈춘다 — D(오늘)를 대기 뒤에 정해서다(B-51).
RAW_INHERITED="${QL_RAW_LOCK_HELD:-}"   # 부모가 쥔 원장 락은 이 셸이 놓지 않는다(재반영 훅 앞 락 반납)
. scripts/raw_lock.sh
raw_lock_acquire daily_evening "$DRY" || exit $?
# shellcheck source=scripts/postclose_conf.sh
. scripts/postclose_conf.sh   # calendar_export_v3_on — 휴장 파일 내보내기 스위치 판정 한 곳(daily_ledger.sh 와 공용)
deliver_json() {
  # 18:15 잠정 빌드·워치독이 읽는 인계 파일. dart_rc 가 빈 문자열이면 아직 도는 중(null) — 빌드 조건이 아니다.
  # 임시 파일에 쓰고 원자 교체한다(읽는 쪽이 부분 JSON 을 보지 않게). wise_n_bad = Σ(n_req − n_ok) — 런 로그 n_bad(검증 실패만)와 다르다.
  $PY -c 'import datetime as dt, json, os, sys
d, rc_kw, rc_dart, rc_wise, kw_at, wise_at, n_bad, bad = sys.argv[1:9]
os.makedirs("data/deliver", exist_ok=True)
payload = {"date": d, "finished_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "kiwoom_rc": int(rc_kw), "dart_rc": (int(rc_dart) if rc_dart != "" else None),
           "wise_rc": int(rc_wise), "dart_done": rc_dart != "",
           "kiwoom_done_at": kw_at or None, "wise_done_at": wise_at or None,
           "wise_n_bad": int(n_bad) if n_bad else None, "wise_bad_summary": json.loads(bad) if bad else None}
tmp = "data/deliver/ledger_evening.json.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
os.replace(tmp, "data/deliver/ledger_evening.json")
print("  deliver/ledger_evening.json 기록 " + json.dumps(payload, ensure_ascii=False))' "$@"
}
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_evening_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
D=""; FAILED=""; SKIPPED=""; REFILL_RC=""
RC_KW=0; RC_DART=0; RC_WISE=0
KW_LOG=""; DART_LOG=""; WISE_LOG=""; KW_DONE=""; WISE_DONE=""; WISE_N_BAD=""; WISE_BAD_SUMMARY=""; WISE_FROM=""
# 세 갈래는 서로 다른 원장(kiwoom.db · dart.db · wise.db)만 건드리므로 병렬이 안전하다.
# 각자 자기 로그에 쓰고 rc 는 `wait <pid>` 로 따로 받는다 — 하나가 죽어도 나머지는 끝까지 간다.
branch_kiwoom() {
  # 키움 일별 집계는 KRX 애프터마켓(16:00~20:00, 09-14 시행) 마감 뒤 20:15 안에 정착한다(09-14 촘촘 프로브:
  # 20:15 값 = 22:05 값). kael-v3 daily_all 이 20:05~20:47 에 같은 앱키를 쓰므로 키움 갈래만 21:05 까지
  # 기다렸다 받는다(결정 10-(d) 19:05 → 결정 11). DART·WISE 는 18:05 그대로. dry-run 은 기다리지 않는다.
  local wait_until="${QL_KW_EVENING_HHMM:-2105}"
  if [ -z "$DRY" ]; then
    while [ "$(TZ=Asia/Seoul date +%H%M)" -lt "$wait_until" ]; do sleep 60; done
  fi
  echo "──── 키움 fetch+commit 시작 $(kst) (하한 ${wait_until:0:2}:${wait_until:2:2} KST) ────"
  $PY -m daily.kw_daily --date "$D" --fetch --tr ka10060,ka10014 --commit \
     --not-before "${QL_EVENING_NOT_BEFORE:-20:00}" $DRY $LIMIT
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
echo "════ [$(kst)] daily_evening 시작 dry=${DRY:-no}${LOCK_WAITED:+ 원장 락 대기 $LOCK_WAITED} ════"
# 휴장 파일 내보내기 스위치(T-48)가 켜져 있으면 v3 휴장 파일은 06:00 daily_ledger.sh 의 내보내기 산출이다 — v3 사본
#   동기화(병행 대조)는 자기 사본 대조라 건너뛴다. 내보내기 자체는 06:00 체인 몫이다
if calendar_export_v3_on; then
  echo "  v3 휴장 사본 동기화 건너뜀 — CALENDAR_EXPORT_V3=1(v3 휴장 파일이 06:00 내보내기 산출이라 자기 사본 대조, T-48)"
else
  scripts/sync_calendar.sh || echo "  ! v3 휴장 사본 동기화 실패 — 판정 달력은 그대로(병행 대조만 빠진다)"
fi
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"
echo "  대상 거래일 D=$D (기본은 오늘 KST — 저녁 슬롯은 당일 데이터를 받는다)"
# rc 0 거래일 · 1 휴장 · 2 판정 불가. 종전 `if ! …` 는 KeyError(연도 파일 부재)의 exit 1 을 휴장으로 읽어
# 소멸성 축(키움 수급·공매도)의 그 세션을 info 한 줄로 영구 결손시켰다(리뷰 REC-2) — build_evening.sh 와 같은 삼분기.
$PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
try:
    ok = c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])))
except Exception as e:  # noqa: BLE001  # reason: 어떤 예외든 "판정 불가" 로 올려 crit 을 내야 한다
    print(f"calendar error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
sys.exit(0 if ok else 1)' "$D"; TD=$?
if [ "$TD" -eq 2 ]; then
  echo "  ✗ 캘린더 판정 불가(연도 파일 부재?) — 휴장으로 위장하지 않는다. 중단"
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_evening 중단 — 캘린더 판정 불가" "D=$D | 로그 $LOG"
  cat "$RUN" >> "$LOG"; rm -f "$RUN"; exit 2
elif [ "$TD" -eq 1 ]; then
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
  WISE_FROM=$(date -u +%Y-%m-%dT%H:%M:%S)   # 이번 저녁 WISE 런의 하한 — ws_run_log.run_at 과 같은 UTC 형식
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
  # WISE 부분 실패(N-27 ③·N-30 ③) — 수집기는 일부 콜이 실패해도 rc 0 이라 FAILED 에 들지 않고, ⚠⚠ 줄은
  # 아래 info 요약에 섞일 뿐이다. 근거는 갈래 로그의 ⚠⚠ 줄이 아니라 이번 저녁 런의 런 로그(ws_run_log,
  # run_at ≥ 갈래 시작)다 — ⚠⚠ 줄은 출력 문구라 바뀌면 조용히 0 이 되고 본문 검증 실패(n_bad)만 담는다.
  # 런 로그의 n_req − n_ok 는 전송 실패(http·exc·notjson)까지 담아 08:10 wise.run 의 런 로그 판정
  # (n_bad 0 ∧ n_ok = n_req)과 같은 양이다(P1). 같은 날 앞 런은 세지 않는다 — 회복 여부는 08:10 건전성 몫.
  # 런 로그를 못 읽거나 0행이면(수집기 비정상 종료·--limit·읽기/파싱 실패) 모름 = 빈 값 → 인계 파일 null.
  # rc≠0 이면 이미 crit 이고, rc 0 이면 '확인 불가' warn 이다 — --limit 없는 rc 0 런은 대상 0 이어도 런 로그를
  # 반드시 1행 쓰므로(backfill_wise.py:321-330·:428-431) 0행은 실제 이상이다(P1).
  WISE_FAIL=$($PY -c 'import json, sqlite3, sys
since = sys.argv[1]
try:
    con = sqlite3.connect("file:data/raw/wisereport.db?mode=ro", uri=True)
    rows = con.execute("SELECT n_req, n_ok, n_bad, bad_summary FROM ws_run_log WHERE run_at >= ?",
                       (since,)).fetchall()
    n_fail, kinds = 0, {}
    for n_req, n_ok, n_bad, summary in rows:
        for k, v in json.loads(summary or "{}").items():
            kinds[k] = kinds.get(k, 0) + int(v)
        other = int(n_req or 0) - int(n_ok or 0) - int(n_bad or 0)
        if other:
            kinds["전송 실패(http·exc·notjson)"] = kinds.get("전송 실패(http·exc·notjson)", 0) + other
        n_fail += int(n_req or 0) - int(n_ok or 0)
except Exception as e:  # noqa: BLE001  # reason: 읽기·파싱 어떤 실패든 "모름"(빈 값) — 확인 불가 warn 이 받는다
    sys.exit(f"  ! WISE 런 로그를 읽지 못했다 — run_at>={since} ({type(e).__name__}: {e})")
if not rows:
    sys.exit(f"  ! WISE 런 로그 없음 — run_at>={since} (수집기 비정상 종료·--limit)")
print(n_fail, json.dumps(kinds, ensure_ascii=False))' "$WISE_FROM")
  WISE_N_BAD="${WISE_FAIL%% *}"; WISE_BAD_SUMMARY="${WISE_FAIL#* }"
  echo "  WISE 이번 저녁 런 실패 콜 ${WISE_N_BAD:-모름} ${WISE_BAD_SUMMARY} $(kst)"
  [ -z "$DRY" ] && deliver_json "$D" "$RC_KW" "" "$RC_WISE" "$KW_DONE" "$WISE_DONE" "$WISE_N_BAD" "$WISE_BAD_SUMMARY"
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
  [ -z "$DRY" ] && deliver_json "$D" "$RC_KW" "$RC_DART" "$RC_WISE" "$KW_DONE" "$WISE_DONE" "$WISE_N_BAD" "$WISE_BAD_SUMMARY"
  # 장 마감 판 재반영(컷오버 PR-8 ⑦ · QL-D 후속 · T-38) — 21:05 키움 원장 커밋이 끝났으면(rc 0) 그 완료를 받아 v3 에 한 번 더
  # 반영한다(compat 만, daily_post 없음 — 9표·점수 없는 7표 판정은 postclose_chain.sh). 고정 시각 크론이 아니라 이 체인의
  # 끝(런 로그·최종 인계 파일 뒤)에 잇는다(P9). 재반영은 원장을 읽기만 하므로 원장 락을 먼저 놓는다 — 세 갈래는 이미
  # 끝났다(wait). 부모가 물려준 락이면 놓지 않는다. rc 는 이 체인의 rc·FAILED 에 넣지 않고, 0·1 이 아니면 아래에서 warn.
  if [ -z "$DRY" ] && [ "$RC_KW" -eq 0 ]; then
    if [ -z "$RAW_INHERITED" ]; then
      exec 9>&-
      unset QL_RAW_LOCK_HELD
      echo "  원장 락 반납 — 장 마감 재반영은 원장을 읽기만 한다 $(kst)"
    fi
    bash scripts/postclose_chain.sh refill --date "$D"; REFILL_RC=$?
    echo "  장 마감 재반영 rc=$REFILL_RC — logs/postclose/${D}_refill.log $(kst)"
  fi
fi
echo "════ 종료 키움=$RC_KW DART=$RC_DART WISE=$RC_WISE $(kst) ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUM_KW=$(grep -E "^\[kw_daily\] (fetch status|commit )" "$RUN" | tail -2 | tr '\n' ' ')
SUM_DART=$(grep -E "^── DART 일일 증분|^  공시 " "$RUN" | tail -2 | tr '\n' ' ')
SUM_WISE=$(grep -E "④ 종료|⚠⚠|무커버" "$RUN" | tail -2 | tr '\n' ' ')
SUMMARY=$(printf 'D=%s | 키움 %s| DART %s| WISE %s| 종료 키움 %s · WISE %s%s' \
  "$D" "${SUM_KW:-없음 }" "${SUM_DART:-없음 }" "${SUM_WISE:-없음 }" "${KW_DONE:-?}" "${WISE_DONE:-?}" \
  "${REFILL_RC:+ | 장 마감 재반영 rc=$REFILL_RC}" | cut -c1-900)
if [ -n "$SKIPPED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh info "daily_evening $SKIPPED — 건너뜀" "D=$D | 로그 $LOG"
  rm -f "$RUN"; exit 0
fi
# WISE 부분 실패 warn(N-27 ③·N-30 ③) — 체인 rc·FAILED·info 와 별개로 1건. 08:10 건전성에서야 드러나면 같은 날
# 재실행(자정 전)을 놓친다. 다른 갈래 실패(crit)와 겹쳐도 낸다 — WISE 재실행은 따로 해야 한다.
# 수집기 rc 0 인데 실패 수를 모르면(런 로그 0행·못 읽음) 그것도 warn 1건(P1). rc≠0 은 아래 crit 이 맡는다.
if [ "${WISE_N_BAD:-0}" -gt 0 ] && [ -z "$DRY" ]; then
  scripts/notify.sh warn "WISE 수집 일부 실패 ${WISE_N_BAD}콜" \
    "D=$D | 종류 $WISE_BAD_SUMMARY | 같은 날 재실행: README 'WISE 같은 날 재실행'(자정 전) | 로그 $WISE_LOG"
elif [ -z "$WISE_N_BAD" ] && [ "$RC_WISE" -eq 0 ] && [ -z "$DRY" ]; then
  scripts/notify.sh warn "WISE 실패 콜 수 확인 불가" "D=$D | 수집기 rc 0, 런 로그(run_at>=$WISE_FROM) 못 읽음 | 로그 $LOG"
fi
# 장 마감 재반영 훅이 0(완료·건너뜀·꺼짐)·1 이 아니면 warn 1건 — 재반영 체인이 자기 crit 을 못 남기고 죽은 경우
# (홈 이동·인자·설정 오류 등)도 사람에게 닿게(조용한 실패 금지). 저녁 체인 rc·FAILED 는 그대로다.
if [ -n "$REFILL_RC" ] && [ "$REFILL_RC" -ne 0 ] && [ "$REFILL_RC" -ne 1 ]; then
  scripts/notify.sh warn "daily_evening 장 마감 재반영 rc=$REFILL_RC" \
    "D=$D | scripts/postclose_chain.sh refill 이 rc $REFILL_RC 로 끝났다 — 로그 logs/postclose/${D}_refill.log · $LOG"
fi
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_evening 실패:$FAILED" "$SUMMARY | 로그 $LOG $KW_LOG $DART_LOG $WISE_LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_evening 완료" "$SUMMARY"
rm -f "$RUN"
