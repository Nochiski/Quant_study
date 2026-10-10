#!/usr/bin/env bash
# 06:00 KST 수집 체인 — KRX 를 뺀 전 소스. 플랜 P1 Task 1.8 / 결정 R1.
#   순서: v3 휴장 사본 동기화(병행 대조용) → 휴장 달력 직접 갱신(KIS chk-holiday 1콜, K1-9) →
#         [스위치 켜짐] v3·uni 휴장 파일 내보내기(T-48 — 켜져 있으면 앞의 v3 사본 동기화는 건너뜀) → D(직전 거래일) 판정 →
#         키움 마스터(daily_wise.sh, 매일) →
#         [월요일 KST · 마지막 성공 7일 초과] DART 번호표 갱신(dart_universe.py → dart_corp_map·corps.txt, A-01) →
#         [D 미수집이면] 키움 대차 1 TR fetch → KIS credit → 저녁 키움 보강 판정(T-13) → DART 스윕·상세·문서 →
#         신규 corp 회사정보 공백 메우기 → 수집 요약 알림
#   소스별 단계는 서로 막지 않는다 — 한 단계가 rc≠0 이어도 다음 소스는 받고, 실패한 단계를 모두 모아 crit
#   (플랜 2026-09-30 T-K3: 신용잔고 판정 실패가 DART 를 막던 결함). `dart company gap` 만 `dart` 성공에 묶는다.
#   KIS 는 07:00(v3 토큰 재발급) 전에 끝나야 해서 순서는 키움 → KIS → DART 그대로다.
#   사용: daily_ledger.sh [--date YYYYMMDD] [--dry-run] [--limit N]
#   환경: QL_KW_NOT_BEFORE=HH:MM (키움 fetch 하한 시각, P0 프로브 판독값. 기본 06:00)
#         QL_V3_HOLIDAY_FILE (휴장 파일 내보내기 대상 덮어쓰기 — 테스트용. 기본은 아래 V3_HOLIDAY_FILE)
#         QL_SKIP_KW=1 이면 키움 시계열 단계를 건너뛴다(앱키 분리 전 임시)
#         키움은 대차(ka20068) 하나만 여기서 늘 받는다 — 투자자·공매도(ka10060·ka10014)는 18:05
#         daily_evening.sh 가 당일 저녁(21:05)에 원장 직행으로 받고(결정 V2-1·V2-3), 여기서는 그 D 커버리지를
#         재서 미달일 때만 다시 받는다(T-13 · H1-5). 외국인 보유(ka10008)는 T-1 행이 07시 전후에 정정되므로
#         (프로브 실측 09-10) daily_build.sh(08:10) 가 받는다.
#   원장 락: 다른 원장 작업이 쥐고 있으면 끝날 때까지 기다렸다 이어서 돈다(P9, 배포 묶음 5-3) — 규칙은
#         scripts/raw_lock.sh 한 곳(대기자 1 · QL_RAW_LOCK_HELD · QL_RAW_LOCK_FILE 테스트 전용).
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
step() {  # step <이름> <명령...> — rc≠0 이면 FAILED 에 이름을 덧붙이고 1 반환
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="${FAILED:+$FAILED, }$name(rc=$rc)"; return 1; fi
  return 0
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
CAL_SUM=$(printf '%s\n' "$CAL_OUT" | tail -1 | cut -c1-700)
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
      EXP_SUM=$(printf '%s\n' "$EXP_OUT" | tail -1 | cut -c1-700)
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
# ① 소멸성 축(키움 마스터)은 매일 — daily_wise.sh 는 raw 락을 물려받는다(WISE 는 18:05 로 이동)
if [ -z "$DRY" ]; then step "daily_wise" bash scripts/daily_wise.sh || true; fi
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
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
# 저녁 키움 보강 판정 줄(T-13)은 맨 앞 — 뒤 단계(DART) 출력이 tail 창에서 밀어내지 않게 따로 집는다
SUMMARY=$({ grep -E "^\[kw_daily\] cover 판정" "$RUN" | tail -1; grep -E "^  |──── .* 종료" "$RUN" | tail -8; } \
  | tr '\n' ' ' | cut -c1-900)
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_ledger 실패: $FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_ledger ${SKIPPED:-완료}" "$SUMMARY"
rm -f "$RUN"
