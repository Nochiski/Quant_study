#!/usr/bin/env bash
# 08:10 KST 빌드 체인 — KRX(T+1 08:00 공표) → 키움 KRX 대조·머지 → 원장 건전성 → 확정 빌드
# → 모델 단계(fi → 모델 → 일간 엑셀 발송, scripts/model_daily.sh — N-25 Q0)
# → 장 마감 판 아침 잇기(v3 아침 KRX 재반영 · 두 판 대조, scripts/postclose_chain.sh morning — 컷오버 PR-8) → 요약.
# 플랜 P1 Task 1.8 / 결정 R1. 확정 빌드(stage·equity basis=morning)는 scripts/build_morning.sh 가 한다
# (플랜 v2 Task B.1). `--no-build` 를 주면 원장 단계에서 멈춘다 — 크론의 --no-build 는 오케스트레이터가 뗀다.
#   사용: daily_build.sh [--date YYYYMMDD] [--no-build] [--dry-run] [--limit N]
#   환경: QL_SKIP_KW=1 이면 키움 merge 를 건너뛰고 건전성 판정의 kiwoom 항목을 skip 한다(앱키 분리 전 임시)
#         QL_FORCE=1 이면 "이미 확정판 있음" 가드를 무시하고 다시 돈다(--date 명시도 같은 효과)
#   원장 락: 다른 원장 작업(06:00 수집 체인 등)이 쥐고 있으면 끝날 때까지 기다렸다 이어서 돈다 — 시간 한도 없음
#         (N-23 ①, P9). 대기 시작은 실시간 출력과 notify 기록(dry-run 제외)에, 대기 시간은 체인 로그에 남고,
#         늦어짐은 10:30 워치독이 알린다. 원장 락 래퍼(`flock <원장 락> daily_build.sh …`) 안에서 부르지
#         않는다 — 자기 자신을 기다려 멈춘다. 부모가 이미 쥐었으면 QL_RAW_LOCK_HELD=1 로 물려준다.
#         대기자는 하나(이미 기다리는 중이면 두 번째 실행은 info 후 rc 3), 대기 중 KST 날짜가 바뀌면 crit 후 rc 3(--date 고정 실행도 — 다시 돌리면 된다, 배포 묶음 5-3).
#         규칙은 scripts/raw_lock.sh 한 곳이다(다른 원장 스크립트와 공용). QL_RAW_LOCK_FILE 은 테스트 전용(락 파일 경로 덮어쓰기)이다.
set -uo pipefail
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
PY=.venv/bin/python
# 인자는 락보다 먼저 읽는다 — 락 대기 알림이 dry-run 인지 알아야 한다
DATE_ARG=""; DRY=""; LIMIT=""; NOBUILD=""; SKIPPED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    --limit) LIMIT="--limit $2"; shift 2 ;;
    --no-build) NOBUILD=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
# 아직 오지 않은 D(--date 오타)는 원장 락·KRX 단계에 들어가기 전에 거부한다 — build_chain.sh 의 같은 거부까지
# 가면 그 앞 KRX 재시도(10분 간격 최대 6회) 동안 원장 락을 쥘 수 있다. 문구·rc 는 build_chain.sh 와 같다(N-24 3.14).
TODAY_KST=$(TZ=Asia/Seoul date +%Y%m%d)
if [ -n "$DATE_ARG" ] && [[ "$DATE_ARG" > "$TODAY_KST" ]]; then
  echo "D=$DATE_ARG 가 오늘(KST $TODAY_KST)보다 뒤다 — 아직 오지 않은 날의 판은 짓지 않는다(--date 오타?)" >&2
  exit 2
fi
# 앞 원장 작업(06:00 수집 체인)이 아직 돌면 건너뛰지 않고 끝나기를 기다렸다 이어서 돈다(N-23 ①, P9).
# 공시 마감일엔 06:00 체인이 10시를 넘기는데, 예전처럼 exit 3 으로 물러나면 그 D 확정판이 영구히 빠졌다.
# 대기 info 는 기록만(notify.sh → logs/notify.log) — 10:30 워치독 crit 를 '미실행'이 아니라 '대기'로 읽게 하고,
# 손으로 --date 를 또 돌려 체인을 한 번 더 도는 일을 막는다. LOCK_WAITED("N초 (시작 ~ 끝)")는 체인 로그에도 남긴다.
. scripts/raw_lock.sh
raw_lock_acquire daily_build "$DRY" \
  "다른 원장 작업(06:00 수집 등)이 $LOCK 을 쥐고 있다. 끝나면 이어서 돈다. 손으로 --date 를 돌리기 전에 확인" || exit $?
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
LOG="logs/daily_build_$(TZ=Asia/Seoul date +%Y%m%d).log"
RUN=$(mktemp)
FAILED=""
FAILED_SOFT=""     # 판은 쓸 수 있는 부분 실패(build_chain rc 1 — GC·완료 신호·contract 같은 후처리 실패). crit 이 아니라 warn 이다
step() {
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$name(rc=$rc)"; return 1; fi
  return 0
}
krx_step() {  # KRX D + 최근 10거래일 미완료 재수집. 미공표·유량·오류면 10분 간격 최대 6회(08:10→09:10)
  # 종전에는 `pending` 만 재시도 조건이라 네트워크·유량(rate)·응답오류(error)는 재시도 0회로 rc 0 이었다
  # (DEFECT-A05). 401(fatal)은 재시도해도 소용없으므로 즉시 실패로 올린다.
  local d="$1" from to pend KRC n_any
  from=$($PY -c 'import datetime as dt,sys; from daily import calendar as c
d=sys.argv[1]; print(c.load().prev_trading_day(dt.date(int(d[:4]),int(d[4:6]),int(d[6:8])), n=10).strftime("%Y-%m-%d"))' "$d")
  to="${d:0:4}-${d:4:2}-${d:6:2}"
  for attempt in 1 2 3 4 5 6; do
    pend=$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT group_concat(DISTINCT bas_dd) FROM ingest_log WHERE status IN ('pending','rate','error') AND bas_dd>='${from//-/}'" 2>/dev/null || true)
    $PY src/backfill_krx.py --from "$from" --to "$to" ${pend:+--refetch "$pend"} ${LIMIT:+$LIMIT}; KRC=$?
    if [ "$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT COUNT(*) FROM ingest_log WHERE status='fatal' AND bas_dd>='${from//-/}'")" != "0" ]; then
      echo "  KRX 401 Unauthorized — AUTH_KEY 만료·권한 없음. 재시도해도 소용없어 중단한다 (KRX 401)"
      return 1
    fi
    # 수집 프로세스가 D 행을 하나도 못 남기고 죽으면(sqlite 락·import 예외·OOM) "미완료 0건" 과 "전부 성공" 이
    # 같은 판정이 된다 — 기록이 없으면 성공이 아니다(리뷰 REC-3).
    n_any=$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT COUNT(*) FROM ingest_log WHERE bas_dd='$d'" 2>/dev/null || echo 0)
    if [ "${n_any:-0}" = "0" ]; then
      echo "  KRX D=$d 의 ingest_log 기록이 없다(backfill rc=$KRC) — 수집 0건은 성공이 아니다. 중단"
      return 1
    fi
    if [ "$(sqlite3 "file:data/raw/krx.db?mode=ro" "SELECT COUNT(*) FROM ingest_log WHERE bas_dd='$d' AND status IN ('pending','rate','error')")" = "0" ]; then return 0; fi
    echo "  KRX D=$d 아직 미완료(pending·rate·error) — 10분 뒤 재시도 ($attempt/6)"
    [ -n "$DRY" ] && return 3
    sleep 600
  done
  return 3
}
{
echo "════ [$(kst)] daily_build 시작 dry=${DRY:-no} no_build=${NOBUILD:-0} ════"
if [ -n "$LOCK_WAITED" ]; then echo "  원장 락 대기 $LOCK_WAITED — 앞 원장 작업이 끝난 뒤 이어서 돈다"; fi
D="${DATE_ARG:-$($PY -c 'import datetime as dt; from daily import calendar as c
print(c.load().prev_trading_day(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()).strftime("%Y%m%d"))')}"
echo "  대상 거래일 D=$D"
if [ -z "$D" ]; then
  # 캘린더를 못 읽으면(연도 파일 부재 → KeyError) 명령치환이 빈 문자열을 준다 — --date "" 로 체인이 돌면 안 된다
  echo "  ✗ 대상 거래일 D 산출 실패(캘린더 오류) — 중단"
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_build 중단 — 대상 거래일 산출 실패" "daily.calendar.prev_trading_day 가 값을 주지 않았다(연도 파일 부재?) | 로그 $LOG"
  cat "$RUN" >> "$LOG"; rm -f "$RUN"; exit 2
fi
# D 의 확정판(stage·equity 둘 다 ok)이 이미 있으면 여기서 끝 — 주말·연휴에 같은 D 를 재수집·재빌드하지
# 않는다(DEFECT-D01·A03·B10). 판정 근거는 인계 이력 `data/deliver/history/<D>_morning.json` 의 health 다.
# `--date` 를 명시했거나 QL_FORCE=1 이면 가드 없이 다시 돈다(재빌드는 사람이 요구한 것이다).
if [ -z "$DATE_ARG" ] && [ -z "${QL_FORCE:-}" ] && [ -z "$DRY" ] && $PY -c 'import json, sys
from pathlib import Path
p = Path(f"data/deliver/history/{sys.argv[1]}_morning.json")
if not p.exists():
    sys.exit(1)
h = json.loads(p.read_text(encoding="utf-8")).get("health") or {}
sys.exit(0 if h.get("stage") == "ok" and h.get("equity") == "ok" else 1)' "$D"; then
  SKIPPED="건너뜀(D=$D 확정판 완료)"
  echo "  D=$D 는 확정판이 이미 있다(data/deliver/history/${D}_morning.json health stage=ok equity=ok) — 종료"
  echo "════ 종료 rc=0 $(kst) ════"
else
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
  # build_chain 은 stage·equity·인계가 다 ok 이고 스냅샷 GC 만 실패하면 rc 1 로 끝난다(결정 V2-7 —
  # 판은 쓸 수 있다). 그걸 step() 에 맡기면 같은 사건에 info("준비")·warn("부분 실패")·crit("실패") 세
  # 등급이 동시에 나갔다(DEFECT-D07). rc 1 은 warn, rc >= 2 만 crit 이다.
  echo "──── build_morning 시작 $(kst) ────"
  bash scripts/build_morning.sh --date "$D"; BRC=$?
  echo "──── build_morning 종료 rc=$BRC $(kst) ────"
  if [ "$BRC" -eq 1 ]; then
    FAILED_SOFT="build_morning(rc=1)"
  elif [ "$BRC" -ne 0 ]; then
    FAILED="build_morning(rc=$BRC)"
  fi
  # 일간 모델 단계(N-25 Q0, 임시 — N-13 가동 전까지): 확정판을 쓸 수 있으면(rc 0, 또는 GC 같은 후처리만
  # 실패한 rc 1) 이어서 fi → 모델 → 일간 엑셀 발송(P9). soft step — 실패해도 확정판 rc·요약 등급은
  # 그대로다. 실패 단계 이름·rc 는 model_daily.sh 가 crit(notify.log 기록만)로 남기고, 아래 요약 본문에
  # 결과 줄이 붙는다. 이미 보낸 D 는 deliver 가 발송 장부로 건너뛴다(같은 D 재빌드에도 중복 발송 없음).
  if [ "$BRC" -eq 0 ] || [ "$BRC" -eq 1 ]; then
    echo "──── 모델 단계 시작 $(kst) ────"
    bash scripts/model_daily.sh --date "$D"; MRC=$?
    echo "──── 모델 단계 종료 rc=$MRC $(kst) ────"
    # 장 마감 판 아침 잇기(컷오버 PR-8 ⑧) — 확정판 뒤 v3 아침 KRX 재반영(compat 만, T-34)과 두 판 대조(PR-7). 모델 단계
    # 성패와 무관하게 돈다(T-34: 가격 재반영이 아침 모델 실패에 묶이지 않는다 — 반영 표는 compat 이 고른다). soft — 결과는
    # 자기 런 로그·notify 로 남기고 확정판 rc·요약 등급은 그대로다. 요약 줄은 '모델 단계 시작' 앞에서 잘라 영향 없음.
    echo "──── 장 마감 판 아침 잇기 시작 $(kst) ────"
    bash scripts/postclose_chain.sh morning --date "$D"; PRC=$?
    echo "──── 장 마감 판 아침 잇기 종료 rc=$PRC $(kst) ────"
  fi
fi
# 통합 일일 리포트는 읽기 전용이라 게이트 실패일·--no-build 에도 돈다(가장 필요한 날이 실패일이다. 검수 R4-03).
[ -z "$DRY" ] && [ -x scripts/daily_report.py ] && { $PY scripts/daily_report.py --date "$D" || true; }
echo "════ 종료 rc=$RC $(kst) ════"
fi
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
# 모델 단계 결과 줄(완료/실패 단계·rc)과 종료 rc 를 맨 앞에 둔다 — 확정판 줄이 길어도 cut -c1-900 에
# 잘리지 않게. 확정판 부분은 종전 그대로 고르고 '모델 단계 시작' 앞에서 자른다(모델 단계 안쪽 줄
# ──── factor_inputs 종료 … 이 tail 창을 밀어내지 않게). 모델 단계가 없는 날은 종전과 같은 바이트다.
SUMMARY=$({ grep -E "^모델 단계 (완료|실패)|^──── 모델 단계 종료" "$RUN" | tail -2
           sed '/^──── 모델 단계 시작/,$d' "$RUN" | grep -E "원장 락 대기|^원장 건전성|──── .* 종료|아직 미완료|KRX 401" | tail -8; } | tr '\n' ' ' | cut -c1-900)
if [ -n "$SKIPPED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh info "daily_build $SKIPPED" "D=$D | 확정판이 이미 있어 재수집·재빌드하지 않았다 — 다시 돌리려면 QL_FORCE=1 또는 --date $D | 로그 $LOG"
  rm -f "$RUN"; exit 0
fi
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "daily_build 실패: $FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
if [ -n "$FAILED_SOFT" ]; then
  [ -z "$DRY" ] && scripts/notify.sh warn "daily_build 확정 빌드 후처리 실패: $FAILED_SOFT" \
    "$SUMMARY | 확정판 자체는 쓸 수 있다(build_chain 이 준비 info + 부분 실패 warn 을 이미 보냈다) | 로그 $LOG"
  rm -f "$RUN"; exit 1
fi
[ -z "$DRY" ] && scripts/notify.sh info "daily_build 완료" "$SUMMARY"
rm -f "$RUN"
