#!/usr/bin/env bash
# 06:00 KST 수집 체인 — KRX 를 뺀 전 소스. 플랜 P1 Task 1.8 / 결정 R1.
#   순서: v3 휴장 사본 동기화(병행 대조용) → 휴장 달력 직접 갱신(KIS chk-holiday 1콜, K1-9) →
#         [스위치 켜짐] v3·uni 휴장 파일 내보내기(T-48 — 켜져 있으면 앞의 v3 사본 동기화는 건너뜀) → D(직전 거래일) 판정 →
#         키움 마스터(daily_wise.sh, 매일) → [월~토] WISE 스냅샷 시작(뒤 단계와 병렬, KIS 뒤 — 건너뜀 길은 끝 전 — 에서 기다림, N-53) →
#         [월요일 KST · 마지막 성공 7일 초과] DART 번호표 갱신(dart_universe.py → dart_corp_map·corps.txt, A-01) →
#         [D 미수집이면] 키움 대차 1 TR fetch → KIS credit → 저녁 키움 보강 판정(T-13) → DART 스윕·상세·문서 →
#         신규 corp 회사정보 공백 메우기 → 수집 요약 알림
#         [스위치 켜짐 · 오늘이 휴장 연속의 첫 휴일] 주말 보강 WISE 스냅샷 1회(N-45) — DART 와 나란한 갈래(별도 로그),
#         요약 전에 끝나기를 기다린다. 실패는 warn, 체인 rc 불변
#   소스별 단계는 서로 막지 않는다 — 한 단계가 rc≠0 이어도 다음 소스는 받고, 실패한 단계를 모두 모아 crit
#   (플랜 2026-09-30 T-K3: 신용잔고 판정 실패가 DART 를 막던 결함). `dart company gap` 만 `dart` 성공에 묶는다.
#   KIS 는 07:00(v3 토큰 재발급) 전에 끝나야 해서 순서는 키움 → KIS → DART 그대로다.
#   사용: daily_ledger.sh [--date YYYYMMDD] [--dry-run] [--limit N]
#   환경: QL_KW_NOT_BEFORE=HH:MM (키움 fetch 하한 시각, P0 프로브 판독값. 기본 06:00)
#         QL_V3_HOLIDAY_FILE (휴장 파일 내보내기 대상 덮어쓰기 — 테스트용. 기본은 아래 V3_HOLIDAY_FILE)
#         QL_SKIP_KW=1 이면 키움 시계열 단계를 건너뛴다(앱키 분리 전 임시)
#         QL_TODAY=YYYYMMDD (테스트 전용 — 주말 보강 WISE 판정의 오늘 KST 날짜 덮어쓰기. 운영에선 비워 둔다)
#         키움은 대차(ka20068) 하나만 여기서 늘 받는다 — 투자자·공매도(ka10060·ka10014)는 18:05
#         daily_evening.sh 가 당일 저녁(21:05)에 원장 직행으로 받고(결정 V2-1), 여기서는 그 D 커버리지를
#         재서 미달일 때만 다시 받는다(T-13 · H1-5). 외국인 보유(ka10008)는 T-1 행이 07시 전후에 정정되므로
#         (프로브 실측 09-10) daily_build.sh(08:10) 가 받는다.
#   원장 락: 다른 원장 작업이 쥐고 있으면 끝날 때까지 기다렸다 이어서 돈다(P9, 배포 묶음 5-3) — 규칙은
#         scripts/raw_lock.sh 한 곳(대기자 1 · QL_RAW_LOCK_HELD · QL_RAW_LOCK_FILE 테스트 전용).
#   WISE 스냅샷(N-53 — 18:05 저녁 체인에서 옮김, V2-3 되돌림): 아침에 받아 두면 08:10 확정 체인의 stage 판에 실리고,
#         15:41 장 마감 판(D' 확정판 고정)이 그날 수집분을 읽는다(전제 — 아침 본문이 그날 갱신분인지는 배포 전 실측 대상,
#         plans/2026-10-16-wise-0600.md '열린 것'). 월요일·연휴 다음 날은 장 마감 판이 고정한 확정판이 토요일·연휴 첫날
#         아침에 지어져 그날 수집분이 아니라 그 아침 수집분을 읽는다 — 그날 수집분은 그날 연구 확정판·원장 건전성 몫이다.
#         수집기(`src/backfill_wise.py`, 약 7분)는 다른 원장(wisereport.db)만 쓰므로 키움 마스터 뒤 배경으로 돌려 KIS 의 07:00
#         전 종료 여유를 줄이지 않는다. 일요일은 받지 않는다(토요일 06:00 수집과 기준일이 같다 — 금요일). D 건너뜀과
#         상관없이 돈다. 수집기 rc≠0 은 다른 단계처럼 FAILED(crit — D 를 받는 길이면 ledger_chain 런 로그도 failed) 이고, rc 0
#         인데 실패 콜이 있으면 KIS 뒤 기다린 자리에서 warn 1건(N-27 ③ — 08:10 확정 체인 전에 같은 날 재실행하면 그날 장 마감
#         판에 실린다).
#         dry-run 은 원장 쓰기라 받지 않고 계획 한 줄만 찍는다(backfill_wise.py 에 --dry-run 이 없다).
set -uo pipefail
ROOT="${QL_LEDGER_ROOT:-$HOME/quant-ledger}"   # 테스트가 임시 루트를 쓰게 할 때만 바꾼다
cd "$ROOT"
export QL_HOME="$ROOT" PYTHONPATH="$ROOT/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4) — 배포 rsync --delete 밖, 바깥 값·옛 시스템 파일을 쓰지 않는다
PY=.venv/bin/python
# 인자는 락보다 먼저 읽는다 — 락 대기 알림이 dry-run 인지 알아야 한다(daily_build.sh 와 같은 순서)
DATE_ARG=""; DRY=""; LIMIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --limit) LIMIT="--limit $2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
. scripts/raw_lock.sh
raw_lock_acquire daily_ledger "$DRY" || exit $?
# shellcheck source=scripts/postclose_conf.sh
. scripts/postclose_conf.sh   # calendar_export_v3_on — 휴장 파일 내보내기 스위치 판정 한 곳(daily_evening.sh 와 공용)
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_ledger_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
FAILED=""
SKIPPED=""   # 건너뜀 사유 — 비어 있지 않으면 완료 알림 제목을 바꾼다(V2-7: 건너뜀도 보고)
WISE_DAY=$(TZ=Asia/Seoul date +%Y%m%d)   # 스냅샷 날짜(KST 오늘 — 수집기 fetched_date 와 같은 축)
WISE_LOG="logs/wise_${WISE_DAY}.log"
PID_WISE=""; RC_WISE=""; WISE_FROM=""; WISE_N_BAD=""; WISE_BAD_SUMMARY=
step() {  # step <이름> <명령...> — rc≠0 이면 FAILED 에 이름을 덧붙이고 1 반환
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="${FAILED:+$FAILED, }$name(rc=$rc)"; return 1; fi
  return 0
}
# 주말 보강판 WISE 수집(N-45 · DECISIONS §7 #8) — 스위치(config/supplement.env SUPPLEMENT_ENABLED=1, 판정
#   scripts/postclose_conf.sh supplement_on)가 켜져 있고 오늘(KST)이 휴장 연속의 첫 휴일(직전 달력일 = 거래일 D —
#   daily.calendar supplement_target)일 때만 WISE 스냅샷 full 을 한 번 받는다. WISE 는 D 분 컨센서스를 D 밤사이
#   (D 18:11 ~ D+1 06:00)에 올린다. 06:00 정규 WISE 갈래(N-53, 월~토)가 휴일 아침에도 먼저 받으므로 이 수집은 보통 대상 0
#   (수집기는 그날 받은 종목을 건너뛴다)이고, 보강판이 보는 런 로그 표식(supplement_wise · mode supplement)을 남긴다.
#   정규 갈래를 기다린(wise_wait) 뒤에 띄운다 — 같은 원장에 두 수집기가 겹치지 않게.
#   - DART 와 나란한 갈래(같은 파일 정규 WISE 갈래 branch_wise 와 같은 모양 — 다른 원장 wisereport.db 만 쓴다): DART 단계 직전에
#     띄우고(D 를 이미 받은 날은 그 자리에서) 체인 끝에서 끝나기를 기다린다 — 완료 감지, 시간 한도 없음(P9). 평소 휴일엔
#     DART 시간 안에 끝나 08:10 확정 빌드(원장 락 대기)를 늦추지 않는다. 원장 락은 체인이 쥔 채다.
#   - 수집기 출력은 갈래 로그 logs/supplement_wise_<오늘>.log 로만 간다(진행 줄이 체인 요약 tail 창을 밀어내지 않게) —
#     체인 로그·요약에는 시작·종료 줄과 결과 한 줄만 남는다.
#   - 런 로그 mode 표식 supplement(`--run-tag`) — 원장 건전성 wise.run 이 이 런을 D 의 정규 수집으로 세지 않는다.
#   - 결과는 런 로그 source=supplement_wise · date=오늘(ok = 수집기 rc 0 + 이번 런의 ws_run_log 1행, 일부 콜 실패는 ok +
#     warn). 08:10 daily_build.sh 끝이 그것을 보고 보강판(weekend_supplement.sh)을 부른다. 오늘 이미 ok 면 다시 받지 않는다.
#   - 실패·완주 기록 없음·대상일 판정 불가는 warn 한 줄(logs/notify.log)이고 체인 rc·FAILED·ledger_chain 런 로그에는 넣지
#     않는다. dry-run 은 계획 한 줄만. QL_TODAY(YYYYMMDD)는 테스트 전용 — 오늘(KST)을 덮어쓴다. 운영에선 비워 둔다.
SUPP_PID=""; SUPP_D=""; SUPP_LOG=""; SUPP_TODAY=""
supp_wise_branch() {  # supp_wise_branch <D> <오늘> — 백그라운드 갈래 본체(출력은 갈래 로그). 마지막 두 칸 줄이 결과다
  local d="$1" today="$2" rid from rc got status warn_title=""
  rid=$($PY -c 'import sys; from daily import runlog
print(runlog.start("data/raw/daily_run.db", date=sys.argv[1], source="supplement_wise"))' "$today") || rid=""
  [ -n "$rid" ] || echo "  ! 런 로그 시작 기록 실패 — source=supplement_wise(08:10 체인이 '기록 없음'으로 본다)"
  from=$(date -u +%Y-%m-%dT%H:%M:%S)   # 이번 런의 하한 — ws_run_log.run_at 과 같은 UTC 형식
  echo "──── 주말 보강 WISE 수집 시작 (D=$d · 오늘 $today) $(kst) ────"
  $PY src/backfill_wise.py --mode full --run-tag supplement 2>&1 | grep -vE "Deprecation|datetime\.datetime"
  rc=${PIPESTATUS[0]}
  echo "DONE_AT=$(TZ=Asia/Seoul date '+%Y-%m-%dT%H:%M:%S+09:00')"
  echo "──── 주말 보강 WISE 수집 종료 rc=$rc $(kst) ────"
  # 완주 증거 = 이번 런의 WISE 런 로그(ws_run_log, run_at ≥ 시작 · mode supplement) — --limit 없는 런은 대상 0 이어도
  # 1행을 남긴다(backfill_wise.py). 실패 콜 수 = Σ(n_req − n_ok) — 저녁 체인 WISE 부분 실패 판정과 같은 양
  got=$($PY -c 'import sqlite3, sys
try:
    con = sqlite3.connect("file:data/raw/wisereport.db?mode=ro", uri=True)
    rows = con.execute("SELECT n_req, n_ok FROM ws_run_log WHERE run_at >= ? AND mode = ?",
                       (sys.argv[1], sys.argv[2])).fetchall()
except Exception as e:  # noqa: BLE001  # reason: 읽기 실패는 완주를 확인하지 못한 것 — 사유를 남긴다
    print(f"WISE 런 로그(ws_run_log)를 못 읽음: {type(e).__name__}: {e}")
    sys.exit(1)
if not rows:
    print(f"WISE 런 로그(ws_run_log) 없음 run_at>={sys.argv[1]} mode={sys.argv[2]} — 수집기가 완주하지 않았다")
    sys.exit(1)
print(sum(int(a or 0) - int(b or 0) for a, b in rows))' "$from" supplement) || got="? ($got)"
  status=ok
  if [ "$rc" -ne 0 ]; then
    status=failed; warn_title="주말 보강 WISE 수집 실패(rc=$rc)"
  elif [[ ! "$got" =~ ^[0-9]+$ ]]; then
    status=failed; warn_title="주말 보강 WISE 완주 기록 없음"
  elif [ "$got" -gt 0 ]; then
    warn_title="주말 보강 WISE 일부 실패 ${got}콜"
  fi
  if [ -n "$rid" ]; then
    $PY -c '# 주말 보강 WISE 런(source=supplement_wise) 종료 기록
import sys; from daily import runlog
runlog.finish("data/raw/daily_run.db", int(sys.argv[1]), status=sys.argv[2], detail=sys.argv[3])' \
      "$rid" "$status" "D=$d wise_rc=$rc fail_calls=$got" \
      || echo "  ! 런 로그 종료 기록 실패 — source=supplement_wise run_id=$rid"
  fi
  if [ -n "$warn_title" ]; then
    scripts/notify.sh warn "$warn_title" \
      "D=$d · 오늘 $today | 실패 콜 $got · 런 로그 supplement_wise $status — $([ "$status" = ok ] && echo '보강판은 그대로 짓는다(빠진 종목은 D 판 추정치 · 유예)' || echo '08:10 체인이 보강판을 짓지 않는다. 정규 체인 rc·판은 그대로') | 로그 $SUPP_LOG"
  fi
  echo "  주말 보강 WISE D=$d 오늘 $today rc=$rc 실패 콜 $got → $status"
  return 0
}
supp_wise_start() {  # 대상일이면 갈래를 띄운다(SUPP_PID). 아니면 사유 한 줄만
  supplement_on || return 0
  SUPP_TODAY="${QL_TODAY:-$(TZ=Asia/Seoul date +%Y%m%d)}"
  # 표준 출력만 받는다(경고 줄은 로그로) — 사유는 판정 코드가 표준 출력에 찍는다. 8자리 날짜·빈 값이 아니면 판정 불가
  SUPP_D=$(supplement_target_day "$SUPP_TODAY"); local t=$? why
  if [ "$t" -ne 0 ] || [[ ! "$SUPP_D" =~ ^([0-9]{8})?$ ]]; then
    echo "  ! 주말 보강 WISE 대상일 판정 불가 — $SUPP_D"
    [ -z "$DRY" ] && scripts/notify.sh warn "주말 보강 WISE 대상일 판정 불가" \
      "오늘 $SUPP_TODAY | $SUPP_D — 보강 WISE 를 받지 않았다(정규 체인 rc·판은 그대로) | 로그 $LOG"
  elif [ -z "$SUPP_D" ]; then
    echo "주말 보강 WISE 없음 — 오늘 $SUPP_TODAY 은 휴장 연속의 첫 휴일이 아니다"
  elif [ -n "$DRY" ]; then
    echo "  주말 보강 WISE 계획(dry-run — 받지 않음): D=$SUPP_D 오늘 $SUPP_TODAY · $PY src/backfill_wise.py --mode full --run-tag supplement(DART 와 나란히)"
  elif why=$(supplement_wise_ok "$SUPP_TODAY"); then
    echo "  주말 보강 WISE 이미 받음(오늘 $SUPP_TODAY) — $why"
  else
    SUPP_LOG="logs/supplement_wise_${SUPP_TODAY}.log"
    echo "──── 주말 보강 WISE 시작 (D=$SUPP_D · 오늘 $SUPP_TODAY · DART 와 나란히 — 로그 $SUPP_LOG) $(kst) ────"
    supp_wise_branch "$SUPP_D" "$SUPP_TODAY" >> "$SUPP_LOG" 2>&1 &
    SUPP_PID=$!
  fi
  return 0
}
supp_wise_wait() {  # 띄운 갈래가 끝나기를 기다린다(시간 한도 없음 — P9). 결과 한 줄을 체인 로그로 옮긴다
  [ -n "$SUPP_PID" ] || return 0
  local rc line
  wait "$SUPP_PID"; rc=$?
  echo "──── 주말 보강 WISE 종료 rc=$rc $(kst) ────"
  line=$(grep -E "^  주말 보강 WISE D=" "$SUPP_LOG" | tail -1)
  if [ -n "$line" ]; then
    echo "$line"
  else
    # 갈래가 결과를 못 남기고 죽었다(kill·OOM) — 런 로그는 running 이라 08:10 이 보강판을 짓지 않는다. 사람에게 닿게
    echo "  주말 보강 WISE D=$SUPP_D 오늘 $SUPP_TODAY 결과 줄 없음(rc=$rc) — 갈래 로그 $SUPP_LOG"
    scripts/notify.sh warn "주말 보강 WISE 결과 미기록" \
      "D=$SUPP_D · 오늘 $SUPP_TODAY | 갈래가 rc $rc 로 결과 없이 끝났다 — 보강판은 짓지 않는다 | 로그 $SUPP_LOG"
  fi
  SUPP_PID=""
}
branch_wise() {   # 배경 갈래 — 자기 로그($WISE_LOG)에만 쓴다
  echo "──── WISE 스냅샷 시작 $(kst) ────"
  $PY src/backfill_wise.py --mode full 2>&1 | grep -vE "Deprecation|datetime\.datetime"
  local rc=${PIPESTATUS[0]}
  echo "──── WISE 스냅샷 종료 rc=$rc $(kst) ────"
  return "$rc"
}
wise_wait() {   # 배경 WISE 갈래를 기다려 rc·실패 콜 수를 모은다. 갈래가 없으면(일요일·dry-run) 아무것도 안 한다
  [ -n "$PID_WISE" ] || return 0
  wait "$PID_WISE"; RC_WISE=$?
  PID_WISE=""
  # 체인 로그·알림 요약에는 시작·끝·결과 줄만 싣는다 — 200종목마다 찍히는 진행 줄이 요약(tail)을 차지해 DART·KIS
  # 결과를 밀어내지 않게. 전체는 갈래 로그($WISE_LOG)에 남는다
  grep -E "^──── WISE|④ 종료|⚠⚠|· 대상" "$WISE_LOG" || true
  # 실패 콜 수(N-27 ③ · N-30 ③) — 수집기는 일부 콜이 실패해도 rc 0 이다. 근거는 이번 런의 런 로그(ws_run_log,
  # run_at ≥ 갈래 시작)의 n_req − n_ok(본문 검증 실패 + 전송 실패) — 08:10 wise.run 판정과 같은 양이다(P1). 같은 날
  # 앞 런은 세지 않는다(회복 여부는 08:10 건전성 몫). 런 로그가 0행이거나 못 읽으면 모름(빈 값) — --limit 없는 rc 0
  # 런은 대상 0 이어도 런 로그를 반드시 1행 쓰므로 0행은 실제 이상이다.
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
  echo "  WISE 스냅샷 $WISE_DAY 실패 콜 ${WISE_N_BAD:-모름} ${WISE_BAD_SUMMARY} $(kst)"
  if [ "$RC_WISE" -ne 0 ]; then FAILED="${FAILED:+$FAILED, }wise(rc=$RC_WISE)"; fi
  # 부분 실패 warn(N-27 ③ · N-30 ③) — 체인 끝(DART 마감일엔 08:10 뒤)이 아니라 기다린 자리에서 바로 낸다. 08:10
  # 확정 체인 전에 같은 날 다시 받으면 그날 장 마감 판에도 실린다(README 'WISE 같은 날 재실행'). 수집기 rc 0 인데
  # 실패 수를 모르면 그것도 warn 1건(P1). rc≠0 은 체인 끝 crit(FAILED)이 맡는다
  if [ -z "$DRY" ] && [ "${WISE_N_BAD:-0}" -gt 0 ]; then
    scripts/notify.sh warn "WISE 수집 일부 실패 ${WISE_N_BAD}콜" \
      "스냅샷=$WISE_DAY | 종류 $WISE_BAD_SUMMARY | 같은 날 재실행: README 'WISE 같은 날 재실행'(08:10 전) | 로그 $WISE_LOG"
  elif [ -z "$DRY" ] && [ -z "$WISE_N_BAD" ] && [ "$RC_WISE" -eq 0 ]; then
    scripts/notify.sh warn "WISE 실패 콜 수 확인 불가" "스냅샷=$WISE_DAY | 수집기 rc 0, 런 로그(run_at>=$WISE_FROM) 못 읽음 | 로그 $LOG"
  fi
}
{
echo "════ [$(kst)] daily_ledger 시작 dry=${DRY:-no}${LOCK_WAITED:+ 원장 락 대기 $LOCK_WAITED} ════"
# 휴장 파일 내보내기 스위치(T-48) — 켜져 있으면 v3 휴장 파일은 아래 내보내기 산출이라 v3 사본 동기화(병행 대조)가 자기
#   사본 대조가 된다. 건너뛴다. 한 번만 판정해 두 자리(동기화·내보내기)가 같은 값을 쓴다
EXPORT_V3=""; calendar_export_v3_on && EXPORT_V3=1
if [ -n "$EXPORT_V3" ]; then
  echo "  v3 휴장 사본 동기화 건너뜀 — CALENDAR_EXPORT_V3=1(v3 휴장 파일이 06:00 내보내기 산출이라 자기 사본 대조, T-48)"
else
  scripts/sync_calendar.sh || echo "  ! v3 휴장 사본 동기화 실패 — 판정 달력은 그대로(병행 대조만 빠진다)"
fi
# 휴장 달력 직접 갱신(결정 Q-2 = N-31 ②, RM K1-9 ①③④⑥ — 플랜 2026-10-10-holiday-calendar-direct).
#   KIS chk-holiday 하루 1콜로 판정 연도 파일을 덧씌우고 trading_calendar·이듬해 기한·v3 사본을 대조한다.
#   rc 0 정상 · 1 warn · 2 crit. 마지막 줄이 요약이 아니면(파이썬이 import 단계에서 죽은 rc 1 등) crit 로 본다.
#   체인 rc·FAILED·ledger_chain 런 로그에는 넣지 않는다 — 달력 갱신 실패로 그날 D 가 '실패'로 남으면 다음
#   06:00 이 전 소스를 다시 받는다. 판정은 직전 달력으로 계속하고, 달력 자체를 못 읽으면 아래 D 산출이 멈춘다(⑦).
#   dry-run 은 --check(읽기 전용 — 1콜, 원장·달력·런 로그·보고서 무변경).
echo "──── 휴장 달력 갱신 시작 $(kst) ────"
CAL_OUT=$($PY -m daily.calendar_refresh ${DRY:+--check} 2>&1); CAL_RC=$?
printf '%s\n' "$CAL_OUT"
echo "──── 휴장 달력 갱신 종료 rc=$CAL_RC $(kst) ────"
CAL_SUM=$(printf '%s\n' "$CAL_OUT" | tail -1 | cut -c1-700 | iconv -c -f UTF-8 -t UTF-8 2>/dev/null)
if [ -z "$DRY" ] && [ "$CAL_RC" -ne 0 ]; then
  if [ "$CAL_RC" -eq 1 ] && [[ "$CAL_SUM" == "휴장 달력 "* ]]; then
    scripts/notify.sh warn "휴장 달력 경고" "$CAL_SUM | 로그 $LOG"
  else
    scripts/notify.sh crit "휴장 달력 갱신 crit(rc=$CAL_RC)" "${CAL_SUM:-출력 없음} | 로그 $LOG"
  fi
fi
# v3·uni 휴장 파일 내보내기(T-48 · QL-Q2) — 스위치(config/calendar_export.env CALENDAR_EXPORT_V3=1)가 켜져 있을 때만.
#   컷오버로 v3 의 휴장 쓰기(daily_all 의 calendar_refresh · 휴장 크론 2줄)가 꺼지면 그 파일(uni 도 읽는다)을 여기서
#   판정 달력으로 쓴다. 이 체인은 주말·휴장 포함 매일 06:00 KST 라 'KST 00:00 뒤·v3 20:05 전 하루 1회 이상' 전제를
#   채운다(src/daily/calendar_export.py 머리 주석). 위 달력 갱신의 rc 와 무관하게 부른다 — 판정 연도 파일을 못 읽으면
#   내보내기 자신이 rc 2·대상 무변경으로 멈춘다. 실패는 crit 한 줄이고 체인 rc·FAILED·ledger_chain 런 로그에는 넣지
#   않는다(달력 갱신과 같은 자리 — 점수 경로 밖이라 창 판정 제외 목록). dry-run 은 쓰지 않고 계획 한 줄만 찍는다.
if [ -n "$EXPORT_V3" ]; then
  V3_HOLIDAY_FILE="${QL_V3_HOLIDAY_FILE:-$HOME/kael-system-v3/data/.kis_holidays.json}"   # sync_calendar.sh SRC 기본값과 같은 파일
  if [ -n "$DRY" ]; then
    echo "  휴장 파일 내보내기 계획(dry-run — 쓰지 않음): $PY -m daily.calendar_export --target $V3_HOLIDAY_FILE"
  else
    echo "──── 휴장 파일 내보내기 시작 $(kst) ────"
    EXP_OUT=$($PY -m daily.calendar_export --target "$V3_HOLIDAY_FILE" 2>&1); EXP_RC=$?
    printf '%s\n' "$EXP_OUT"
    echo "──── 휴장 파일 내보내기 종료 rc=$EXP_RC $(kst) ────"
    if [ "$EXP_RC" -ne 0 ]; then
      EXP_SUM=$(printf '%s\n' "$EXP_OUT" | tail -1 | cut -c1-700 | iconv -c -f UTF-8 -t UTF-8 2>/dev/null)
      scripts/notify.sh crit "휴장 파일 내보내기 실패(rc=$EXP_RC)" "${EXP_SUM:-출력 없음} | 로그 $LOG"
    fi
  fi
fi
D="${DATE_ARG:-$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
echo "  대상 거래일 D=$D"
if [ -z "$D" ]; then
  echo "  ✗ 대상 거래일 D 산출 실패(캘린더 오류) — 중단"
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_ledger 중단 — 대상 거래일 산출 실패" "daily.calendar.prev_trading_day 가 값을 주지 않았다(연도 파일 부재?) | 로그 $LOG"
  cat "$RUN" >> "$LOG"; rm -f "$RUN"; exit 2
fi
# ① 소멸성 축(키움 마스터)은 매일 — daily_wise.sh 는 raw 락을 물려받는다(이름과 달리 WISE 는 받지 않는다 — 아래 갈래)
if [ -z "$DRY" ]; then step "daily_wise" bash scripts/daily_wise.sh || true; fi
# WISE 스냅샷(N-53) — 키움 마스터 뒤(수집 대상 = 그날 마스터) 배경으로 시작하고 KIS 뒤(건너뜀 길은 끝 전)에 wise_wait 로 기다린다.
#   QL_WEEKDAY(1=월…7=일)는 테스트 전용 요일 주입 — 운영에선 비워 둔다(크론이 UTC 라 요일은 KST 로 본다)
if [ "${QL_WEEKDAY:-$(TZ=Asia/Seoul date +%u)}" = 7 ]; then
  echo "  WISE 스냅샷 건너뜀 — 일요일(토요일 06:00 수집과 기준일이 같다)"
elif [ -n "$DRY" ]; then
  echo "  WISE 스냅샷 계획(dry-run — 받지 않음): $PY src/backfill_wise.py --mode full → $WISE_LOG"
else
  WISE_FROM=$(date -u +%Y-%m-%dT%H:%M:%S)   # 이번 런의 하한 — ws_run_log.run_at 과 같은 UTC 형식
  echo "  WISE 스냅샷 배경 시작 — 로그 $WISE_LOG $(kst)"
  branch_wise > "$WISE_LOG" 2>&1 & PID_WISE=$!
fi
# DART 번호표(dart_corp_map)·corps.txt 갱신(A-01 · DART 1콜) — 신규 상장을 DART 재무·공시에 잇는다.
#   월요일(KST)엔 늘, 그 밖의 날엔 마지막 성공(런 로그 source=dart_universe, date = KST 달력일)이 7일을 넘었거나
#   기록이 없을 때 돈다(N-27 ⑤, 배포 묶음 5-2 — 월요일만이면 실패한 주는 2주 공백). 런 로그를 못 읽으면 돈다(P1).
#   성공해야 ok 를 남기고, 실패는 warn 한 줄 — 체인 rc·FAILED·ledger_chain 런 로그엔 넣지 않는다(체인은 계속,
#   성공 기록이 없으니 7일을 넘는 동안 다음 06:00 이 다시 부른다).
#   키움 마스터 뒤라 그날 신규 상장까지 들고, 건너뜀 검사 앞이라 D(금)를 이미 받은 평소 월요일에도 돈다(그날 신규
#   corp 의 회사 정보 공백 메우기는 D 를 받는 다음 실행 몫). 크론이 UTC(일 21:00)라 요일은 KST 로 본다.
#   dry-run 이 없는 원장 쓰기라 dry-run 에선 건너뛴다. QL_WEEKDAY(1=월…7=일)는 테스트 전용 요일 주입 — 운영에선 비워 둔다.
if [ -z "$DRY" ]; then
  if [ "${QL_WEEKDAY:-$(TZ=Asia/Seoul date +%u)}" = 1 ]; then
    UNIV_WHY="월요일"
  elif UNIV_WHY=$($PY -c 'import datetime as dt, sys; from daily import runlog
ok = [r.date for r in runlog.recent("data/raw/daily_run.db", source="dart_universe", limit=20) if r.status == "ok"]
if not ok:
    print("마지막 성공 기록 없음"); sys.exit(1)
age = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date() - dt.datetime.strptime(ok[0], "%Y%m%d").date()).days
print(f"마지막 성공 {ok[0]}({age}일 전)"); sys.exit(0 if age <= 7 else 1)'); then
    echo "  DART 번호표 갱신 안 함 — $UNIV_WHY, 7일 이내"
    UNIV_WHY=""
  else
    UNIV_WHY="${UNIV_WHY:-런 로그를 못 읽음}"
  fi
  if [ -n "$UNIV_WHY" ]; then
    echo "──── dart universe 시작 ($UNIV_WHY) $(kst) ────"
    $PY src/dart_universe.py; URC=$?
    echo "──── dart universe 종료 rc=$URC $(kst) ────"
    if [ "$URC" -eq 0 ]; then
      $PY -c 'import datetime as dt; from daily import runlog
d = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
runlog.finish("data/raw/daily_run.db", runlog.start("data/raw/daily_run.db", date=d, source="dart_universe"), status="ok")' \
        || echo "  ! dart_universe 성공 기록 실패 — 다음 06:00 이 다시 부른다(DART 1콜)"
    else
      scripts/notify.sh warn "daily_ledger dart universe 실패(rc=$URC)" \
        "DART 번호표(dart_corp_map)·corps.txt 갱신 실패($UNIV_WHY) — 체인은 계속. 마지막 성공이 7일을 넘는 동안 06:00 체인이 날마다 다시 부른다 | 로그 $LOG"
    fi
  fi
fi
# D 가 이미 수집·판정 완료면 여기서 끝(주말·연휴에 같은 D 를 반복하지 않는다)
if [ -z "$DRY" ] && $PY -c 'import sys; from daily import runlog
rows=[r for r in runlog.recent("data/raw/daily_run.db", source="ledger_chain", limit=10) if r.date==sys.argv[1] and r.status=="ok"]
sys.exit(0 if rows else 1)' "$D"; then
  echo "  D=$D 는 이미 수집 완료 — 종료"
  SKIPPED="건너뜀(D=$D 이미 수집 완료)"
  wise_wait
  supp_wise_start   # 주말 보강 WISE(N-45) — 정규 WISE 갈래를 기다린 뒤 띄우고 바로 아래 끝에서 기다린다
  echo "════ 종료 $(kst) ════"
else
  RID=""
  [ -z "$DRY" ] && RID=$($PY -c 'import sys; from daily import runlog; print(runlog.start("data/raw/daily_run.db", date=sys.argv[1], source="ledger_chain"))' "$D")
  # ② 키움 대차 1 TR fetch(대기 테이블) → ③ KIS credit → ④ DART — 소스끼리는 서로 막지 않는다(T-K3)
  if [ -n "${QL_SKIP_KW:-}" ]; then
    echo "  QL_SKIP_KW=1 — 키움 시계열 fetch 건너뜀(앱키 분리 전, DECISIONS_PENDING 결정 5 R5 후속)"
  else
    step "kiwoom fetch" $PY -m daily.kw_daily --date "$D" --fetch --tr ka20068 --not-before "${QL_KW_NOT_BEFORE:-06:00}" $DRY $LIMIT || true
  fi
  step "kis credit" $PY -m daily.kis_daily --date "$D" $DRY $LIMIT || true
  # WISE(약 7분)는 키움 대차·KIS(약 41분)보다 먼저 끝나 보통 기다림이 없다 — 여기서 받아 경보를 바로 낸다(DART 마감일에도
  # 08:10 전에 사람에게 닿게). 실패는 이 체인의 실패다 — 런 로그와 알림이 같은 판정을 쓴다(DEFECT-A10)
  wise_wait
  # 저녁 키움 보강(T-13 · H1-5) — 전날 21:05 저녁 직행(ka10060·ka10014)의 D 커버리지를 원장에서 재고(읽기만),
  #   미달인 TR 만 저녁과 같은 `--fetch --commit` 으로 한 번 다시 받은 뒤 다시 잰다. 그래도 미달이면 rc 2 →
  #   다른 소스 단계처럼 FAILED → crit · rc 2 · ledger_chain failed. 그 D 를 다음 06:00 이 다시 판정하는 것은
  #   주말·연휴뿐이다(평일엔 다음 06:00 의 D 가 다음 거래일로 넘어간다).
  #   판정 줄(`[kw_daily] cover 판정`)은 아래 요약 맨 앞에 싣는다. 하한·술어는 kw_daily 한 곳(COMMIT_MIN_RATIO).
  #   KIS 뒤: KIS 는 07:00(v3 토큰 재발급) 전에 끝나야 한다(실측 06:13→06:41, 여유 19분) — 다시 받기(키움 ≈14분)를
  #   앞에 두면 여유가 5분으로 준다. DART 앞: 마감일 DART 는 2.3~3.8시간 더 걸려 그 뒤면 보강이 한참 밀린다.
  #   공유 키움 앱키 슬롯: 미달인 날만 쓰는 조건부 슬롯(06:00 체인, KIS 뒤 약 10~15분)이다. 07:00 을 넘기면 v3 토큰
  #   재발급과 겹치는데, 그때 나는 8005 는 api.kiwoom 이 강제 재발급 1회 재시도로 받아 양쪽 다 스스로 복구한다.
  if [ -z "${QL_SKIP_KW:-}" ]; then
    step "kiwoom evening cover" $PY -m daily.kw_daily --cover --date "$D" --tr ka10060,ka10014 $DRY $LIMIT || true
  fi
  supp_wise_start   # 주말 보강 WISE(N-45) — DART 와 나란한 갈래(다른 원장), 체인 끝에서 기다린다
  step "dart" $PY -m daily.dart_daily --date "$D" $DRY $LIMIT \
    && step "dart company gap" bash scripts/dart_company_gap.sh ${DRY:+--dry-run}
  RC=0; [ -n "$FAILED" ] && RC=1
  # `RC` 는 수집 체인의 rc 라 `|| true` 로 흘려보낸 daily_wise 실패를 모른다 — 런로그가 ok 인데 알림은
  # crit 이던 관측 불일치(DEFECT-A10). 런로그도 FAILED 를 함께 본다.
  if [ -n "$RID" ]; then
    $PY -c 'import sys; from daily import runlog; runlog.finish("data/raw/daily_run.db", int(sys.argv[1]), status=sys.argv[2], detail=sys.argv[3])' "$RID" "$([ $RC -eq 0 ] && [ -z "$FAILED" ] && echo ok || echo failed)" "${FAILED:-}"
  fi
  echo "════ 종료 rc=$RC $(kst) ════"
fi
# 주말 보강 WISE 갈래(위 supp_wise_* 주석)가 떠 있으면 끝나기를 기다린다 — 요약·알림은 그 뒤다
supp_wise_wait
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
# 저녁 키움 보강 판정 줄(T-13)은 맨 앞 — 뒤 단계(DART) 출력이 tail 창에서 밀어내지 않게 따로 집는다
# WISE 줄은 tail 창 밖에서 결과 한 줄만 붙인다 — 수집 단계(KIS·DART) 결과를 밀어내지 않게(N-53)
# GNU cut -c 는 멀티바이트를 모르고 바이트로 자른다 — 끝에서 잘린 글자 조각은 iconv -c 가 버린다(#630)
SUMMARY=$({ grep -E "^\[kw_daily\] cover 판정" "$RUN" | tail -1
            grep -E "^  |──── .* 종료" "$RUN" | grep -vE "WISE 스냅샷|④ 종료|^  · 대상" | tail -8
            grep -E "^  WISE 스냅샷 (건너뜀|[0-9]{8} 실패 콜)" "$RUN" | tail -1; } \
  | tr '\n' ' ' | cut -c1-900 | iconv -c -f UTF-8 -t UTF-8 2>/dev/null)
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_ledger 실패: $FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_ledger ${SKIPPED:-완료}" "$SUMMARY"
rm -f "$RUN"
