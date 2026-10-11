#!/usr/bin/env bash
# 주말 보강판(DECISIONS N-45 · §7 #8 · 플랜 docs/plans/2026-10-12-excel-confirmed-impl.md §2 E5) — 휴장 앞 마지막 거래일 D 의
#   판을 휴일 아침에 받은 WISE 컨센서스(WISE 기준일 = D)로 한 번 더 지어 보낸다. 정규 판(아침·장 마감)과 섞지 않는 별도
#   판이다(basis supplement — fi·모델 `_runs/<D>_supplement.json`·`latest_supplement.json`, 판 id `s_`).
#   사용: scripts/weekend_supplement.sh --date D --wise-asof W [--force]
#   호출: daily_build.sh 끝(08:10 체인) — 스위치 켜짐(config/supplement.env SUPPLEMENT_ENABLED=1) · 오늘 W 가 D 다음 첫 휴일
#         (daily.calendar supplement_target) · 오늘 06:00 체인의 보강 WISE 수집 ok(런 로그 source=supplement_wise · date=W).
#         고정 시각 크론이 아니라 앞 체인의 끝에 잇는다(P9 — 대기 한도·재시도 시각 없음). 손으로 다시 돌릴 때도 같은 명령이다
#         (체인과 같은 환경 — 아래 cd·export).
#   순서: 스위치 → 이미 완료 검사 → 고정 판 확인(data/deliver/history/<D>_morning.json 이 D·health ok — 아침 fi 와 같은
#         stage·equity 판이고, 그 stage 는 휴일 아침 WISE 수집 뒤에 지어져 휴일 수집분이 들어 있다) → [빌드 락]
#         ① fi    python -m factor_inputs build --date D --basis supplement --wise-asof W --builds-from <그 이력>
#                 (WISE 컨센서스 입력만 W 까지, 가격·수급·DART·속성은 D — factor_inputs/build.py '주말 보강판')
#         ② 모델  python -m model build --date D --basis supplement
#         ③ 엑셀  python -m deliver model-daily --date D --basis supplement --send
#         앞 단계가 실패하면 뒤 단계는 돌지 않는다(발송 0). fi 게이트 경고(metrics.warn — postclose_conf.sh fi_gate_warns)는
#         warn 한 줄(판정 그대로).
#   한 번만: 런 로그(data/raw/daily_run.db) source=supplement_board · date=D 에 ok 가 있으면 건너뛴다. --force 또는 QL_FORCE=1
#         이면 다시 돈다(같은 D 엑셀을 다시 보낼지는 deliver 발송 장부가 정한다). daily_build.sh 는 QL_FORCE 를 비워 부른다.
#   정규 판과 무관: 정규 판의 fi·모델 판 기록(`_morning`·`_evening`)·인계 이력·발송 장부를 쓰지 않는다. 실패해도 정규 판과
#         다음 거래일 판은 그대로다.
#   알림(notify.sh — logs/notify.log 기록만): 완료 info · 단계 실패 crit · 고정 판 없음 warn · 완료 판정 불가 crit ·
#         이미 완료 info · 꺼짐 info · fi 게이트 경고 warn.
#   빌드 락: model_daily.sh 와 같다(/tmp/quant_ledger_build.lock — 쥐여 있으면 끝날 때까지 기다린다, 한도 없음 — P9).
#         QL_BUILD_LOCK_HELD=1 이면 부모가 쥔 것으로 본다. QL_BUILD_LOCK_FILE 은 테스트 전용(락 파일 경로 덮어쓰기).
#   rc: 0 완료·건너뜀(꺼짐·이미 완료) · 1 짓지 못함(단계 실패·고정 판 없음·완료 판정 불가 — 알림은 여기서 남긴다) ·
#       2 인자 오류 · 3 빌드 락 열기·대기 실패 · 4 홈 이동 실패
set -uo pipefail
cd "$HOME/quant-ledger" || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 돌지 않는다" >&2; exit 4; }
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4) — 배포 rsync --delete 밖, 바깥 값·옛 시스템 파일을 쓰지 않는다
PY=.venv/bin/python
RUN_DB=data/raw/daily_run.db
HIST=data/deliver/history
D=""; W=""; FORCE="${QL_FORCE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    # 값 없는 --date·--wise-asof 는 빈 값으로 둔다 — 아래 형식 검사가 rc 2 로 거부한다
    --date) if [ $# -ge 2 ]; then D="$2"; shift 2; else shift; fi ;;
    --wise-asof) if [ $# -ge 2 ]; then W="$2"; shift 2; else shift; fi ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
TODAY_KST=$(TZ=Asia/Seoul date +%Y%m%d)
if [[ ! "$D" =~ ^[0-9]{8}$ ]] || [[ ! "$W" =~ ^[0-9]{8}$ ]]; then
  echo "--date D 와 --wise-asof W(YYYYMMDD)가 필요하다(받은 값: D='$D' W='$W') — 아무 단계도 돌지 않았다" >&2
  exit 2
fi
if [[ ! "$W" > "$D" ]] || [[ "$W" > "$TODAY_KST" ]]; then
  echo "--wise-asof W 는 D 보다 뒤이고 오늘(KST $TODAY_KST) 이전이어야 한다(D=$D W=$W) — 아무 단계도 돌지 않았다" >&2
  exit 2
fi
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
# shellcheck source=scripts/postclose_conf.sh
. scripts/postclose_conf.sh   # supplement_on · fi_gate_warns — 스위치·경고 판정 한 곳
if ! supplement_on; then
  echo "주말 보강판 꺼짐(config/supplement.env SUPPLEMENT_ENABLED≠1) — 아무것도 하지 않음 D=$D W=$W"
  scripts/notify.sh info "주말 보강판 꺼짐 — 건너뜀" "D=$D W=$W | config/supplement.env SUPPLEMENT_ENABLED≠1"
  exit 0
fi

# ── 한 번만 — 런 로그 supplement_board(date = D) ok 가 있으면 건너뛴다 ───────────────────
# rc 0 완료(마지막 줄 = 그 런 detail) · 1 아직 · 2 런 로그를 못 읽음
DONE_WHY=$($PY -c '# 주말 보강판(supplement_board) 완료 판정
import sys
from daily import runlog
try:
    rows = [r for r in runlog.recent(sys.argv[1], source="supplement_board", limit=200)
            if r.date == sys.argv[2] and r.status == "ok"]
except Exception as e:  # noqa: BLE001  # reason: 런 로그를 못 읽으면 완료 여부를 모른다 — 짐작하지 않는다
    print(f"런 로그 {sys.argv[1]} 를 못 읽음: {type(e).__name__}: {e}")
    sys.exit(2)
if rows:
    detail = rows[0].detail or "-"
    print(f"run_id={rows[0].run_id} {detail}")
    sys.exit(0)
sys.exit(1)' "$RUN_DB" "$D" 2>&1); DONE_RC=$?
DONE_WHY=$(printf '%s\n' "$DONE_WHY" | tail -1)
if [ "$DONE_RC" -eq 0 ] && [ -z "$FORCE" ]; then
  echo "주말 보강판 이미 완료 D=$D — $DONE_WHY (다시 하려면 --force)"
  scripts/notify.sh info "주말 보강판 이미 완료 — 건너뜀" "D=$D W=$W | 런 로그 supplement_board $DONE_WHY — 다시 하려면 --force"
  exit 0
elif [ "$DONE_RC" -ne 0 ] && [ "$DONE_RC" -ne 1 ]; then
  echo "주말 보강판 실패: 완료 판정 불가 — $DONE_WHY D=$D"
  scripts/notify.sh crit "주말 보강판 실패: 완료 판정 불가" \
    "D=$D W=$W | $DONE_WHY — 보냈는지 몰라 짓지 않는다. 런 로그를 고친 뒤 scripts/weekend_supplement.sh --date $D --wise-asof $W"
  exit 1
fi

# ── 런 로그 시작 · 끝 ───────────────────────────────────────────────────────────
RID=$($PY -c '# 주말 보강판(supplement_board) 런 시작
import sys
from daily import runlog
print(runlog.start(sys.argv[1], date=sys.argv[2], source="supplement_board"))' "$RUN_DB" "$D") || RID=""
[ -n "$RID" ] || echo "  ! 런 로그 시작 기록 실패 — source=supplement_board(다음 실행이 '아직'으로 본다)"
finish_run() {   # finish_run <상태> <detail>
  [ -n "$RID" ] || return 0
  $PY -c '# 주말 보강판(supplement_board) 런 종료
import sys
from daily import runlog
runlog.finish(sys.argv[1], int(sys.argv[2]), status=sys.argv[3], detail=sys.argv[4])' \
    "$RUN_DB" "$RID" "$1" "$2" || echo "  ! 런 로그 종료 기록 실패 — source=supplement_board run_id=$RID"
}

# ── 고정 판 — D 아침 확정판 인계 이력(읽기는 equity.handoff 한 곳) ─────────────────────
BOARD_WHY=$($PY -c '# 주말 보강판(supplement) 고정 판 확인
import sys
from pathlib import Path
from equity import handoff
d, path = sys.argv[1], Path(sys.argv[2])
try:
    h = handoff.load(path)
except handoff.HandoffError as e:
    print(e)
    sys.exit(1)
if h.date != d or not h.health_ok or h.basis != "morning":
    print(f"인계 이력 {path} 이 쓸 수 있는 판이 아니다: date={h.date} basis={h.basis} health={h.health}")
    sys.exit(1)
print(f"고정 판 {path} date={d} health ok")' "$D" "$HIST/${D}_morning.json" 2>&1); BOARD_RC=$?
BOARD_WHY=$(printf '%s\n' "$BOARD_WHY" | tail -1)
if [ "$BOARD_RC" -ne 0 ]; then
  echo "주말 보강판 안 지음 — D 아침 확정판이 없다: $BOARD_WHY D=$D"
  finish_run failed "W=$W 고정 판 없음: $BOARD_WHY"
  scripts/notify.sh warn "주말 보강판 안 지음 — D 확정판 없음" \
    "D=$D W=$W | $BOARD_WHY — 정규 판 실패는 08:10 체인 알림을 본다. 확정판을 다시 지은 뒤 scripts/weekend_supplement.sh --date $D --wise-asof $W"
  exit 1
fi
echo "  $BOARD_WHY"

# ── 빌드 락(model_daily.sh 와 같은 모양 — fd 9. daily_build 에서 물려받은 원장 락 fd 9 는 이 셸에서만 닫힌다) ──────
LOCK="${QL_BUILD_LOCK_FILE:-/tmp/quant_ledger_build.lock}"
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK" || {
    echo "주말 보강판 실패: 빌드 락 열기(rc=3) — $LOCK 을 열 수 없다, 아무 단계도 돌지 않았다 D=$D"
    finish_run failed "W=$W 빌드 락 열기 실패"
    scripts/notify.sh crit "주말 보강판 실패: 빌드 락 열기(rc=3)" "D=$D W=$W | $LOCK 을 열 수 없다 — 아무 단계도 돌지 않았다(엑셀 발송 0)"
    exit 3
  }
  if ! flock -n 9; then
    W0=$(date +%s); W0_KST=$(kst)
    echo "[$W0_KST] 주말 보강판 빌드 락 대기 시작 — 다른 빌드가 $LOCK 을 쥐고 있다(끝나면 이어서 돈다) D=$D"
    scripts/notify.sh info "주말 보강판 빌드 락 대기" \
      "D=$D 시작 $W0_KST — 다른 빌드(stage·equity·모델)가 $LOCK 을 쥐고 있다. 끝나면 이어서 돈다"
    if ! flock 9; then
      echo "주말 보강판 실패: 빌드 락 대기(rc=3) — $LOCK 을 기다리다 flock 이 실패했다 D=$D"
      finish_run failed "W=$W 빌드 락 대기 실패"
      scripts/notify.sh crit "주말 보강판 실패: 빌드 락 대기(rc=3)" \
        "D=$D W=$W | $LOCK 을 기다리다 flock 이 실패했다 — 아무 단계도 돌지 않았다(엑셀 발송 0)"
      exit 3
    fi
    echo "[$(kst)] 주말 보강판 빌드 락 대기 끝 — $(( $(date +%s) - W0 ))초 ($W0_KST ~ $(kst))"
  fi
  export QL_BUILD_LOCK_HELD=1
fi

# ── 세 단계 ─────────────────────────────────────────────────────────────────────
FAILED=""
step() {
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$name(rc=$rc)"; fi
  return "$rc"
}
echo "════ [$(kst)] weekend_supplement D=$D W=$W basis=supplement${FORCE:+ --force} ════"
step "factor_inputs" $PY -m factor_inputs build --date "$D" --basis supplement --wise-asof "$W" \
    --builds-from "$HIST/${D}_morning.json" \
  && step "model" $PY -m model build --date "$D" --basis supplement \
  && step "deliver" $PY -m deliver model-daily --date "$D" --basis supplement --send
RC=$?
# fi 게이트 경고 — 판정은 통과지만 사람이 볼 것(metrics.warn). fi 가 실패했으면 그 crit 이 따로 나간다
if [[ "$FAILED" != factor_inputs* ]]; then
  FI_WARNS=$(fi_gate_warns "data/factor_inputs/_runs/${D}_supplement.json")
  if [ -n "$FI_WARNS" ]; then
    echo "fi 게이트 경고 — $(printf '%s\n' "$FI_WARNS" | cut -f1 | paste -sd, -) D=$D"
    scripts/notify.sh warn "주말 보강판 fi 게이트 경고" \
      "D=$D W=$W basis=supplement | $(printf '%s\n' "$FI_WARNS" | tr '\t\n' ' ;')| 판정은 그대로 — 사람이 본다"
  fi
fi
if [ "$RC" -ne 0 ]; then
  if [[ "$FAILED" == deliver* ]]; then
    SENT="발송 여부는 deliver 출력과 발송 장부로 확인 — rc 3 은 발송 뒤 장부 기록 실패일 수 있다"
  else
    SENT="그 뒤 단계는 돌지 않았다(엑셀 발송 0)"
  fi
  echo "주말 보강판 실패: $FAILED — $SENT D=$D"
  finish_run failed "W=$W failed=$FAILED"
  scripts/notify.sh crit "주말 보강판 실패: $FAILED" \
    "D=$D W=$W basis=supplement | $SENT. 정규 판은 그대로다. 원인을 고친 뒤 scripts/weekend_supplement.sh --date $D --wise-asof $W"
  exit 1
fi
finish_run ok "W=$W"
echo "주말 보강판 완료 D=$D W=$W"
scripts/notify.sh info "주말 보강판 완료" "D=$D W=$W basis=supplement | fi → 모델 → 엑셀 발송"
