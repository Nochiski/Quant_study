#!/usr/bin/env bash
# 장 마감 체인 — 컷오버 트랙 PR-8(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P3 · T-2·T-3·T-4·T-7·T-26·T-29·
#   T-31·T-34·T-35·T-37·T-38, DECISIONS N-35 · N-42 Q3·Q4). 15:41 장 마감 수집에서 v3 반영까지를 **완료 감지로** 잇는다
#   (P9) — 대기 한도·재시도 시각이 없고, 고정 시각은 외부 공개 시각(KRX 정규장 수급 확정 15:40 → 15:41 크론)뿐이다.
#   사용: scripts/postclose_chain.sh close   [--date T] [--dry-run]   15:41 KST 크론(T 기본 = 오늘 KST) — ⓪~⑥
#         scripts/postclose_chain.sh refill  --date T  [--dry-run]    daily_evening.sh 가 21:05 키움 원장 커밋 뒤 — ⑦
#         scripts/postclose_chain.sh morning --date D  [--dry-run]    daily_build.sh 가 확정판·모델 단계 뒤 — ⑧
#
#   close — 앞 단계가 실패하면 뒤 단계는 돌지 않는다. 산출은 data/model_db/ 아래만(T-3 · T-29)
#     ⓪ T 가 휴장이면(판정 달력 daily.calendar) 수집 전에 info 로 끝난다(수집기는 휴장일에 rc 0 이라 체인이 먼저 본다).
#     ① 수집  python -m daily.postclose --date T — 수집기 자체 락만 쓰고 원장 락은 기다리지 않는다(T-4). rc 0(완료·16:00
#             컷오프·16:00 뒤 시작)이면 다음 단계. rc 3 이고 T 가 세션 예외일(수집기와 같은 달력 모듈의
#             load_session_exceptions)이면 그날 체인 전체를 건너뛴다(T-26 — 기록은 수집기 런 로그 session_exception +
#             notify warn). 그 밖의 rc 3(수집기 락 경합·15:41 전)과 rc 2 는 실패다.
#     ①' 고정 판 확인 — 직전 거래일 D' 아침 인계 이력 data/deliver/history/<D'>_morning.json 이 그 날짜이고 health ok
#             (읽기는 equity.handoff 한 곳)인지 **빌드 락을 잡기 전에** 본다. 아니면 락 없이 crit — 그날 판은 없고 다음 날
#             아침판이 대체 발송 경로다(T-7). 수집(①)은 그보다 앞이라 판이 없는 날에도 21:05 경로(T-38)의 원장은 쌓인다.
#     ② stage python -m stage --table stg_flow_postclose_kiwoom --basis evening --stage-root data/model_db/stage
#             --snapshot-root data/model_db/snapshots(T-29 — 연구 루트에 지으면 연구 인계 stage_builds 에 섞인다), 이어서
#             그 표만 건전성 stage.health --tables(리포트 logs/health/postclose_stage_<T>.json — 연구 판 리포트
#             stage_<D>_<basis>.json 과 이름을 가른다. 일일 리포트가 그 이름을 연구 판으로 읽는다). 뒤에 장 마감 스냅샷
#             GC(keep = snapshot.KEEP_DEFAULT, 현재 판 보호 — 기록형, 실패해도 체인은 간다)
#     ③ fi    python -m factor_inputs build --date T --basis evening --root data/model_db/factor_inputs
#             --builds-from data/deliver/history/<D'>_morning.json --calendar-dir data/calendar(D' = 직전 거래일, T-2).
#             rc 0 이어도 판 manifest(_runs/<T>_evening.json) 게이트 중 metrics.warn 이 참인 것(FG5 — 후보 중 T-6 보류
#             비율, T-37)은 런 로그 detail 에 warn:<게이트> 를 남기고 notify warn 한 줄을 낸다(판정은 그대로)
#     ④ 모델  python -m model build --date T --basis evening --root data/model_db/model --fi-root data/model_db/factor_inputs
#     ⑤ 엑셀  python -m deliver model-daily --date T --basis evening --model-root … --fi-root … --out-root data/model_db/deliver
#             — 발송(--send)은 설정 POSTCLOSE_SEND=1 일 때만(T-7: 그림자 기간 미발송)
#     ⑥ v3    scripts/v3_post.sh --date T --basis evening --v3-db <v3 quant.db> --model-root data/model_db/model
#             --builds-from data/deliver/history/<D'>_morning.json — fi 와 같은 고정 판(P1). 그림자는 --shadow(v3 본 파일
#             무변경). 설정 POSTCLOSE_V3=in-place 면 제자리 반영이고, POSTCLOSE_V3_POST_CMD 가 있으면 --v3-post-cmd(v3
#             daily_post, T-31). 점수 두 표를 v3 에 쓰는 것은 이 ⑥ 하나다
#   refill — 21:05 저녁 원장 뒤 v3 재반영(QL-D 후속 · T-38). ⑥ 결과와 상관없이 늘 점수 없는 7표 반영
#     v3_post.sh --basis evening --no-scores --builds-from <D'>_morning.json(QL-F2, compat 만 — daily_post 없음).
#     16:00 컷오프로 못 받은 종목을 21:05 원장 값으로 채우고, 장 마감 판이 없는 날(판 실패·세션 예외일)엔 그날 저녁 v3
#     T 행을 넣는 유일한 경로다(T-26 · T-38). 점수는 ⑥ 만 쓴다 — 판이 없던 날의 점수는 다음 날 아침 재반영이 채운다(T-34).
#     부르기 전에 고정 판(<D'>_morning.json)의 날짜·health 를 본다 — 쓸 수 없으면 v3_post 를 부르지 않고 crit(compat 은
#     health 를 보지 않아 equity 일부만 실패한 날 옛 판이 7표에 조용히 섞인다, P1).
#   morning — 다음 날 아침 잇기. ⓐ v3_post.sh --date D --basis morning --builds-from data/deliver/history/<D>_morning.json
#     (compat 만 — 반영 표 7·9 는 compat 이 장 마감 반영 기록으로 고른다, T-34. 고정 판 <D>_morning.json 의 날짜·health 를
#     먼저 보고 쓸 수 없으면 부르지 않고 crit — refill 과 같은 이유) ⓑ 두 판 대조 python -m daily.board_compare
#     --date D --evening-root data/model_db --research-root data(PR-7) — 그날 장 마감 모델 판(④ 의 마지막 런 ok)이 있을
#     때만. 둘은 서로 막지 않는다(대조는 v3 반영의 소비자가 아니다). 대조 rc 1 은 **이번 실행이 쓴**
#     data/model_db/compare/<D>.json 이 불일치 판정(verdict fail · rc 1 · 그 날짜)일 때만 mismatch(warn — 판정은 연속 창
#     집계 몫)이고, 그 밖의 rc 1(모듈 없음·예외)은 실패다. 그날(D) 수집기 런(kiwoom_postclose)이 아예 없으면 15:41 close
#     크론이 돌지 않은 것이라 끝 알림을 info 대신 warn 으로 낸다(세션 예외일엔 수집기 런 session_exception 이 남는다).
#
#   설정 config/postclose_chain.env(배포로 코드와 함께 나간다) — 켜고 끄는 자리. 컷오버(PR-9)가 켠다. 켜는 쪽만 정확한
#     값을 요구한다(P1) — 그 밖의 값·빈 값·파일 없음은 꺼짐·그림자다.
#     POSTCLOSE_ENABLED=1 일 때만 세 모드가 돈다. 꺼져 있으면 info 한 줄·rc 0(크론이 등록됐으면 16:30 워치독이 crit).
#     POSTCLOSE_SEND=1 일 때만 엑셀 발송, POSTCLOSE_V3=in-place 일 때만 제자리 반영(그 밖은 미발송 · --shadow).
#     in-place 인데 발송이 꺼져 있으면 rc 5 로 거부한다(crit — 보내지 않은 점수가 v3 에 들어간다).
#     POSTCLOSE_V3_POST_CMD 는 ⑥ 에만 넘긴다 — refill·morning 은 compat 만이다(v3_post.sh 가 아침·--no-scores + post 명령을
#     인자 오류로 막는다). 환경의 QL_V3_POST_CMD 는 비운다(설정만 정본). 읽기·판정은 scripts/postclose_conf.sh 한 곳이다
#     (model_daily.sh·watchdog.sh 와 공용 — ENABLED=1 그리고 SEND=1 이면 원천 전환 뒤라 아침판은 짓기만·대체 발송, PR-9).
#     ⑤ 엑셀이 ok 면(발송 장부 줄) ⑥ v3 가 실패해도 다음 날 아침 대체 발송은 없다 — 대체 발송 조건은 장 마감 발송 장부뿐이고,
#     그 D 의 v3 점수는 아침 재반영(T-34)이 아침 모델 판으로 채운다.
#   v3 quant.db: QL_V3_DB(기본 $HOME/kael-system-v3/data/quant.db — COMPAT_LAYER §8 V3-C 와 같은 자리). 그림자도 읽는다
#     (스테이징 사본을 뜬다).
#   락 — 장 마감 체인 락 /tmp/quant_ledger_postclose_chain.lock: close 는 비대기(이미 돌면 warn·rc 3 — 같은 T 를 두 번 돌지
#     않는다), refill 은 기다린다(돌고 있는 close 의 ⑥ 과 같은 v3 반영 — 그림자 스테이징 경로·v3 락 — 을 겹치지 않고 그 뒤에
#     잇는다. 시간 한도 없음 — P9). morning 은 잡지 않는다
#     (어제 T 의 일이라 오늘 close 를 막으면 안 된다). 빌드 락 /tmp/quant_ledger_build.lock(build_chain·model_daily·
#     deploy·gc 공용)은 ②~⑤ 동안만 쥔다 — 쥐여 있으면 끝날 때까지 기다린다(model_daily.sh 와 같은 모양, 대기 시작 info).
#     ①·①' 은 락 밖이다 — 16:00 창을 빌드·배포에 묶지 않고, 판이 없는 날은 락을 잡지 않는다. ⑥·refill·morning 은 빌드 락이
#     필요 없다(compat 은 고정 판을 읽는다). 원장 락도 잡지 않는다(원장은 읽기만). 연구 21:20 저녁 빌드는 그림자 시작 때
#     크론에서 뺀다(N-42 Q4 — README '크론 전체표'). QL_BUILD_LOCK_HELD=1 이면 부모가 쥔 것으로 본다.
#     QL_BUILD_LOCK_FILE · QL_POSTCLOSE_CHAIN_LOCK_FILE 은 테스트 전용(락 파일 경로) — 운영 크론·대화형 셸에 남기지 않는다.
#   런 로그 data/raw/daily_run.db(daily.runlog) — ① 은 수집기가 source=kiwoom_postclose 로, ②~⑥ 은 이 셸이 단계마다
#     daily.runlog.POSTCLOSE_STEPS(postclose_stage·_fi·_model·_excel·_v3)로, refill·morning 은 POSTCLOSE_FOLLOWUPS
#     (postclose_v3_refill · postclose_v3_morning · postclose_compare)로 남긴다. 날짜 = 판의 거래일. 16:30 워치독
#     (watchdog.sh postclose_board)과 일일 리포트(마지막 런 규칙)가 읽는다.
#   알림(notify.sh — logs/notify.log 기록만): 준비 info · 단계 실패 crit · 휴장 info · 꺼짐 info · 세션 예외일 warn ·
#     락 경합 warn · 게이트 경고(FG5) warn · 설정 오류 crit.
#   로그 logs/postclose/<D>_<mode>.log(단계 상세). 표준 출력에는 한 줄 요약만 — 부르는 쪽(크론·daily_evening·daily_build)
#     로그에 남는다.
#   rc: 0 완료·건너뜀·꺼짐 · 1 morning 대조 불일치(warn) · 2 단계 실패·인자 오류 · 3 체인 락 경합·락 실패 · 4 홈 이동 실패 ·
#       5 설정 오류(in-place 인데 발송 꺼짐)
set -uo pipefail
cd "$HOME/quant-ledger" || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 돌지 않는다" >&2; exit 4; }
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
PY=.venv/bin/python
MDB=data/model_db
RUN_DB=data/raw/daily_run.db
HIST=data/deliver/history
CHAIN_LOCK="${QL_POSTCLOSE_CHAIN_LOCK_FILE:-/tmp/quant_ledger_postclose_chain.lock}"
BUILD_LOCK="${QL_BUILD_LOCK_FILE:-/tmp/quant_ledger_build.lock}"
V3_DB="${QL_V3_DB:-$HOME/kael-system-v3/data/quant.db}"
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
usage() { echo "usage: postclose_chain.sh <close|refill|morning> [--date YYYYMMDD] [--dry-run]" >&2; exit 2; }

MODE="${1:-}"
[ $# -gt 0 ] && shift
case "$MODE" in close|refill|morning) ;; *) usage ;; esac
D=""; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    # 값 없는 --date 는 D 를 비운 채 넘긴다 — 아래 형식 검사가 rc 2 로 거부한다
    --date) if [ $# -ge 2 ]; then D="$2"; shift 2; else shift; fi ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done
TODAY_KST=$(TZ=Asia/Seoul date +%Y%m%d)
[ "$MODE" = close ] && D="${D:-$TODAY_KST}"
if [[ ! "$D" =~ ^[0-9]{8}$ ]]; then
  # refill·morning 은 기본값이 없다 — 아침 잇기의 D 는 전날이라 오늘로 잘못 잡으면 다른 날 판을 대조·반영한다
  echo "--date YYYYMMDD 가 필요하다(mode=$MODE, 받은 값: '$D') — 아무 단계도 돌지 않았다" >&2
  exit 2
fi
if [[ "$D" > "$TODAY_KST" ]]; then
  echo "D=$D 가 오늘(KST $TODAY_KST)보다 뒤다 — 아직 오지 않은 날의 판은 짓지 않는다(--date 오타?)" >&2
  exit 2
fi

# ── 설정 — 켜는 쪽만 정확한 값. 파일이 없거나 값이 다르면 꺼짐·그림자(규칙은 scripts/postclose_conf.sh 한 곳) ──
# shellcheck source=scripts/postclose_conf.sh
. scripts/postclose_conf.sh
postclose_conf_load
unset QL_V3_POST_CMD
SWITCH="발송 $([ -n "$SEND" ] && echo on || echo off) · v3 $([ -n "$SHADOW" ] && echo shadow || echo in-place)$([ -n "$POST_CMD" ] && echo ' + daily_post')"
CONF_ERR=""
[ -z "$SHADOW" ] && [ -z "$SEND" ] && \
  CONF_ERR="POSTCLOSE_V3=in-place 인데 POSTCLOSE_SEND≠1 — 보내지 않은 장 마감 점수가 v3 에 들어간다"
if [ -n "$ENABLED" ] && [ -n "$CONF_ERR" ]; then
  echo "postclose_chain $MODE D=$D 설정 오류(rc 5): $CONF_ERR — 아무 단계도 돌지 않았다" >&2
  [ -z "$DRY" ] && scripts/notify.sh crit "장 마감 체인 설정 오류 — $MODE 거부" "D=$D | $CONF_ERR | $CONF"
  exit 5
fi
if [ -z "$ENABLED" ] && [ -z "$DRY" ]; then
  echo "postclose_chain $MODE D=$D 꺼짐(POSTCLOSE_ENABLED≠1) — 아무것도 하지 않음 · $CONF_NOTE"
  scripts/notify.sh info "장 마감 체인 꺼짐 — $MODE 건너뜀" \
    "D=$D | $CONF_NOTE · POSTCLOSE_ENABLED≠1 — 크론이 등록돼 있으면 16:30 워치독이 crit 을 낸다"
  exit 0
fi

# ── 판정 도우미(읽기 전용) ─────────────────────────────────────────────────────
trading() {   # rc 0 거래일 · 1 휴장 · 2 판정 불가(달력을 못 읽음 — 휴장으로 위장하지 않는다)
  $PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
try:
    ok = c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])))
except Exception as e:  # noqa: BLE001  # reason: 어떤 예외든 "판정 불가" 로 올려 crit 을 내야 한다
    print(f"calendar error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
sys.exit(0 if ok else 1)' "$1"
}
prev_day() {  # 직전 거래일 YYYYMMDD — 못 구하면 빈 출력·rc≠0
  $PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
print(c.load().prev_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))).strftime("%Y%m%d"))' "$1"
}
session_reason() {  # T 가 세션 예외일이면 사유를 찍고 rc 0 · 아니면 1 · 표를 못 읽으면 2(세션 예외로 위장하지 않는다)
  $PY -c 'import sys
from daily import calendar as c
try:
    days = c.load_session_exceptions("data/calendar")
except Exception as e:  # noqa: BLE001  # reason: 표를 못 읽으면 판정 불가 — 건너뜀 근거로 쓰지 않는다
    print(f"session table error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
if sys.argv[1] in days:
    print(days[sys.argv[1]])
    sys.exit(0)
sys.exit(1)' "$1"
}
board_ok() {  # 고정 판 — 인계 이력 <HIST>/<$1>_morning.json 이 그 날짜·health ok 인가(읽기는 equity.handoff 한 곳). 사유를 찍는다
  $PY -c 'import sys
from pathlib import Path
from equity import handoff
d, path = sys.argv[1], Path(sys.argv[2])
try:
    h = handoff.load(path)
except handoff.HandoffError as e:
    print(e)
    sys.exit(1)
if h.date != d or not h.health_ok:
    print(f"인계 이력 {path} 이 쓸 수 있는 판이 아니다: date={h.date} health={h.health}")
    sys.exit(1)
print(f"고정 판 {path} date={d} health ok")' "$1" "$HIST/${1}_morning.json"
}
last_status() {  # 그 D·source 의 마지막 런 상태(없으면 빈 출력)
  $PY -c 'import sys
from daily import runlog
rows = [r for r in runlog.recent(sys.argv[1], source=sys.argv[3], limit=500) if r.date == sys.argv[2]]
print(rows[0].status if rows else "")' "$RUN_DB" "$D" "$1"
}

# ── 명령 ──────────────────────────────────────────────────────────────────────
DPREV=""
STAGE_STARTED=""
STEP_NOTE=""     # 단계 함수가 그 단계 런 로그 detail 에 덧붙일 말(step 이 매번 비운다)
CHAIN_WARN=""    # 판정은 그대로인 경고 — 끝에 notify warn 한 줄
collect_cmd() { $PY -m daily.postclose --date "$D"; }
stage_step() {
  # 표 한 개 단독 빌드 — 빌드 id 접두 e_(장 마감 판). 건전성은 그 표만(--tables), C1·C2 는 이번 실행 뒤 판만 본다
  # (--started-at), 판이 커밋된 날은 오늘(--built-on — 지난 T 를 손으로 다시 지을 때도 맞게)
  STAGE_STARTED=$(date -u +%FT%TZ)
  $PY -m stage --table stg_flow_postclose_kiwoom --basis evening \
     --stage-root "$MDB/stage" --snapshot-root "$MDB/snapshots" || return $?
  $PY -m stage.health --stage-root "$MDB/stage" --basis evening --date "$D" \
     --built-on "$(TZ=Asia/Seoul date +%Y%m%d)" --tables stg_flow_postclose_kiwoom \
     --started-at "$STAGE_STARTED" --out "logs/health/postclose_stage_${D}.json"
}
snapshot_gc() {
  # 단독 빌드는 실행마다 postclose.db 전체 스냅샷을 뜬다 — 지우지 않으면 날마다 쌓인다. 규칙은 연구 체인 gc_step 과
  # 같다(keep = KEEP_DEFAULT, 현재 판이 선 스냅샷 보호)
  $PY - "$MDB" <<'PY'
import sys
from pathlib import Path

from stage import snapshot

mdb = Path(sys.argv[1])
protect = snapshot.current_snapshot_ids(mdb / "stage")
r = snapshot.gc(mdb / "snapshots", keep=snapshot.KEEP_DEFAULT, protect=protect)
print(f"  장 마감 스냅샷 {r.summary()} (보호 {len(protect)}판)")
PY
}
fi_cmd() {
  $PY -m factor_inputs build --date "$D" --basis evening --root "$MDB/factor_inputs" \
     --builds-from "$HIST/${DPREV}_morning.json" --calendar-dir data/calendar || return $?
  # 판정은 통과지만 사람이 봐야 하는 게이트 경고(FG5 metrics.warn — T-37)를 판 manifest 에서 읽는다(fi 코드는 그대로)
  local warns
  warns=$($PY - "$MDB/factor_inputs/_runs/${D}_evening.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        run = json.load(f)
    gates = run["gates"]
except (OSError, ValueError, KeyError, TypeError) as e:
    print(f"manifest_unreadable\t판 manifest {sys.argv[1]} 를 읽지 못해 게이트 경고를 확인하지 못했다"
          f"({type(e).__name__})")
    raise SystemExit(0) from None
for g in gates:
    if isinstance(g, dict) and isinstance(g.get("metrics"), dict) and g["metrics"].get("warn") is True:
        print(f"{g.get('name')}\t{str(g.get('detail'))[:300]}")
PY
)
  if [ -n "$warns" ]; then
    STEP_NOTE="warn:$(printf '%s\n' "$warns" | cut -f1 | paste -sd, -)"
    CHAIN_WARN="${CHAIN_WARN:+$CHAIN_WARN; }fi $(printf '%s\n' "$warns" | tr '\t\n' ' ;')"
    echo "  fi 게이트 경고 — $STEP_NOTE"
  fi
  return 0
}
model_cmd() { $PY -m model build --date "$D" --basis evening --root "$MDB/model" --fi-root "$MDB/factor_inputs"; }
excel_cmd() {
  # shellcheck disable=SC2086  # reason: ${SEND:+--send} 는 있거나 없는 단일 플래그다
  $PY -m deliver model-daily --date "$D" --basis evening --model-root "$MDB/model" \
     --fi-root "$MDB/factor_inputs" --out-root "$MDB/deliver" ${SEND:+--send}
}
# v3_post 세 갈래 — 셋 다 고정 판(--builds-from)을 넘긴다(P1 — fi 와 같은 판). 판이 없으면 compat 이 멈추고 v3_post 가
# crit 을 낸다. 락 fd 는 v3 쪽 자식에 넘기지 않는다
v3_evening_cmd() {  # $1 = daily_post 명령(⑥ 에만, 그 밖엔 빈 값)
  bash scripts/v3_post.sh --date "$D" --basis evening --v3-db "$V3_DB" --model-root "$MDB/model" \
     --builds-from "$HIST/${DPREV}_morning.json" ${SHADOW:+--shadow} ${1:+--v3-post-cmd "$1"} 6>&- 7>&-
}
v3_noscores_cmd() {  # refill — 점수 두 표를 뺀 7표(T-38, QL-F2). 점수는 ⑥ 만 쓴다
  STEP_NOTE="no-scores"
  bash scripts/v3_post.sh --date "$D" --basis evening --v3-db "$V3_DB" --no-scores \
     --builds-from "$HIST/${DPREV}_morning.json" ${SHADOW:+--shadow} 6>&- 7>&-
}
v3_morning_cmd() {
  bash scripts/v3_post.sh --date "$D" --basis morning --v3-db "$V3_DB" \
     --builds-from "$HIST/${D}_morning.json" ${SHADOW:+--shadow} 6>&- 7>&-
}
compare_cmd() {
  local t0 rc
  t0=$(date +%s)
  $PY -m daily.board_compare --date "$D" --evening-root "$MDB" --research-root data; rc=$?
  [ "$rc" -eq 1 ] || return "$rc"
  # rc 1 은 PR-7 의 '불일치'(미설명·Spearman 하한 미달)지만 모듈 없음·import 예외도 rc 1 이다 — 이번 실행이 쓴 보고서가
  # 그 날짜의 불일치 판정(verdict fail · rc 1)일 때만 1 을 돌려준다. 아니면 실패(2)로 올린다
  if $PY - "$MDB/compare/${D}.json" "$D" "$t0" <<'PY'
import json
import os
import sys

path, d, t0 = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    fresh = os.path.getmtime(path) >= t0
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
except (OSError, ValueError):
    sys.exit(1)
iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
sys.exit(0 if fresh and isinstance(rep, dict) and rep.get("verdict") == "fail" and rep.get("rc") == 1
         and str(rep.get("date")) == iso else 1)
PY
  then return 1; fi
  STEP_NOTE="board_compare rc 1 이지만 이번 실행의 $MDB/compare/${D}.json 이 불일치 판정이 아니다(모듈 없음·예외)"
  echo "  $STEP_NOTE — 실패로 본다"
  return 2
}

if [ -n "$DRY" ]; then
  # 계획만 출력한다 — 락·단계·런 로그·알림 전부 없음
  KEEP=$($PY -c 'from stage import snapshot; print(snapshot.KEEP_DEFAULT)' 2>/dev/null)
  echo "════ dry-run postclose_chain $MODE D=$D $(kst) — $([ -n "$ENABLED" ] && echo 켜짐 || echo '꺼짐(POSTCLOSE_ENABLED≠1 — 실제 실행은 아무것도 하지 않는다)') · $SWITCH ($CONF_NOTE) ════"
  case "$MODE" in
    close)
      trading "$D"; TD=$?
      DPREV=$(prev_day "$D" 2>/dev/null)
      echo "  거래일 판정 rc=$TD (0 거래일 · 1 휴장 → 건너뜀 · 2 판정 불가) · D'=${DPREV:-계산 불가}"
      echo "  0. 체인 락 $CHAIN_LOCK flock -n (이미 돌면 rc 3)"
      echo "  1. $PY -m daily.postclose --date $D   (rc 3 + 세션 예외일이면 체인 전체 건너뜀)"
      echo "  1' 고정 판 $HIST/${DPREV:-?}_morning.json — 지금: $( [ -n "$DPREV" ] && board_ok "$DPREV" 2>&1 || echo 'D 계산 불가')"
      echo "  -  빌드 락 $BUILD_LOCK (쥐여 있으면 기다림) — 2~5 동안만"
      echo "  2. $PY -m stage --table stg_flow_postclose_kiwoom --basis evening --stage-root $MDB/stage --snapshot-root $MDB/snapshots"
      echo "     $PY -m stage.health --stage-root $MDB/stage --basis evening --date $D --tables stg_flow_postclose_kiwoom --out logs/health/postclose_stage_${D}.json"
      echo "     스냅샷 GC $MDB/snapshots (keep ${KEEP:-?} = snapshot.KEEP_DEFAULT, 현재 판 보호 — 기록형)"
      echo "  3. $PY -m factor_inputs build --date $D --basis evening --root $MDB/factor_inputs --builds-from $HIST/${DPREV:-?}_morning.json --calendar-dir data/calendar"
      echo "  4. $PY -m model build --date $D --basis evening --root $MDB/model --fi-root $MDB/factor_inputs"
      echo "  5. $PY -m deliver model-daily --date $D --basis evening --model-root $MDB/model --fi-root $MDB/factor_inputs --out-root $MDB/deliver${SEND:+ --send}"
      echo "  6. scripts/v3_post.sh --date $D --basis evening --v3-db $V3_DB --model-root $MDB/model --builds-from $HIST/${DPREV:-?}_morning.json${SHADOW:+ --shadow}${POST_CMD:+ --v3-post-cmd '<설정 POSTCLOSE_V3_POST_CMD>'}" ;;
    refill)
      DPREV=$(prev_day "$D" 2>/dev/null)
      echo "  0. 체인 락 $CHAIN_LOCK (돌고 있는 close 가 끝날 때까지 기다림)"
      echo "  -  고정 판 $HIST/${DPREV:-?}_morning.json — 지금: $( [ -n "$DPREV" ] && board_ok "$DPREV" 2>&1 || echo 'D 계산 불가')"
      echo "  1. 점수 없는 7표(⑥ 결과와 무관, T-38): scripts/v3_post.sh --date $D --basis evening --v3-db $V3_DB --no-scores --builds-from $HIST/${DPREV:-?}_morning.json${SHADOW:+ --shadow}" ;;
    morning)
      echo "  -  고정 판 $HIST/${D}_morning.json — 지금: $(board_ok "$D" 2>&1)"
      echo "  1. scripts/v3_post.sh --date $D --basis morning --v3-db $V3_DB --builds-from $HIST/${D}_morning.json${SHADOW:+ --shadow}   (compat 만)"
      echo "  2. 그날 ④ postclose_model 마지막 런이 ok 일 때만: $PY -m daily.board_compare --date $D --evening-root $MDB --research-root data" ;;
  esac
  echo "════ dry-run 종료 (원장·판·v3 무변경, 런 로그·알림 없음) ════"
  exit 0
fi

# ── 락 ───────────────────────────────────────────────────────────────────────
if [ "$MODE" != morning ]; then
  exec 7>"$CHAIN_LOCK" || { scripts/notify.sh warn "장 마감 체인 락 열기 실패" "mode=$MODE D=$D | $CHAIN_LOCK 을 열 수 없다 — 아무 단계도 돌지 않았다"; exit 3; }
  if ! flock -n 7; then
    if [ "$MODE" = close ]; then
      echo "[$(kst)] 장 마감 체인이 이미 돈다($CHAIN_LOCK) — 같은 T 를 두 번 돌지 않는다. 건너뜀"
      scripts/notify.sh warn "장 마감 체인 이미 실행 중 — 건너뜀" "T=$D | $CHAIN_LOCK 을 다른 실행이 쥐고 있다 — 아무 단계도 돌지 않았다"
      exit 3
    fi
    W0_KST=$(kst)
    echo "[$W0_KST] 장 마감 체인 락 대기 시작 — 돌고 있는 close 가 끝나면 그 ⑥ 결과로 판정한다"
    scripts/notify.sh info "장 마감 재반영 체인 락 대기" "T=$D 시작 $W0_KST — 장 마감 체인이 아직 돈다. 끝나면 이어서 판정한다"
    if ! flock 7; then
      scripts/notify.sh warn "장 마감 재반영 락 실패" "T=$D | $CHAIN_LOCK 을 기다리다 flock 이 실패했다 — 재반영 안 함"
      exit 3
    fi
    echo "[$(kst)] 장 마감 체인 락 대기 끝 ($W0_KST ~)"
  fi
fi
BUILD_HELD=""
if [ "$MODE" = close ] && [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 6>"$BUILD_LOCK" || { scripts/notify.sh crit "장 마감 체인 실패: 빌드 락 열기(rc=3)" "T=$D | $BUILD_LOCK 을 열 수 없다 — 아무 단계도 돌지 않았다"; exit 3; }
fi
build_lock() {   # ②~⑤ 앞. 쥐여 있으면 끝날 때까지 기다린다(시간 한도 없음 — P9)
  [ -n "${QL_BUILD_LOCK_HELD:-}" ] && return 0
  if ! flock -n 6; then
    local w0 w0_kst
    w0=$(date +%s); w0_kst=$(kst)
    echo "[$w0_kst] 빌드 락 대기 시작 — 다른 빌드(연구 빌드·배포·gc)가 $BUILD_LOCK 을 쥐고 있다(끝나면 이어서 돈다)"
    scripts/notify.sh info "장 마감 체인 빌드 락 대기" \
      "T=$D 시작 $w0_kst — 다른 빌드가 $BUILD_LOCK 을 쥐고 있다. 끝나면 이어서 돈다(늦어짐은 16:30 워치독)"
    flock 6 || return 3
    echo "[$(kst)] 빌드 락 대기 끝 — $(( $(date +%s) - w0 ))초"
  fi
  BUILD_HELD=1
}
build_unlock() { [ -n "$BUILD_HELD" ] && flock -u 6; BUILD_HELD=""; return 0; }

# ── 단계 실행 ─────────────────────────────────────────────────────────────────
FAILED=""; FAILED_SOFT=""; WARNED=""; DONE=""; SKIP=""; SKIP_LEVEL=""; NOTE=""; NO_CLOSE=""
ALT_RC=""; ALT_STATUS=""   # 그 단계의 rc 하나를 다른 상태로 기록한다 — 대조 1 → mismatch(warn)
step() {  # step <런 로그 source> <이름> <명령…> — source 한 행(running → 상태). 실패하면 FAILED 에 남기고 1
  local src="$1" name="$2" rid rc t0 status
  shift 2
  t0=$(date +%s)
  STEP_NOTE=""
  rid=$($PY -c 'import sys
from daily import runlog
print(runlog.start(sys.argv[1], date=sys.argv[2], source=sys.argv[3]))' "$RUN_DB" "$D" "$src") || rid=""
  [ -n "$rid" ] || echo "  ! 런 로그 시작 기록 실패 — source=$src(워치독이 '없음'으로 본다)"
  echo "──── $name 시작 $(kst) ────"
  "$@"; rc=$?
  echo "──── $name 종료 rc=$rc $(( $(date +%s) - t0 ))초 $(kst) ────"
  status=failed
  [ "$rc" -eq 0 ] && status=ok
  [ -n "$ALT_RC" ] && [ "$rc" -eq "$ALT_RC" ] && status="$ALT_STATUS"
  if [ -n "$rid" ]; then
    $PY -c 'import sys
from daily import runlog
runlog.finish(sys.argv[1], int(sys.argv[2]), status=sys.argv[3], detail=sys.argv[4])' \
      "$RUN_DB" "$rid" "$status" "rc=$rc $SWITCH${STEP_NOTE:+ $STEP_NOTE}" \
      || echo "  ! 런 로그 종료 기록 실패 — source=$src run_id=$rid"
  fi
  case "$status" in
    ok) DONE="$DONE $name"; return 0 ;;
    mismatch) WARNED="${WARNED:+$WARNED, }$name($status)"; return 0 ;;
    *) FAILED="${FAILED:+$FAILED, }$name(rc=$rc)"; return 1 ;;
  esac
}

close_main() {
  trading "$D"; local td=$?
  if [ "$td" -eq 2 ]; then FAILED="거래일 판정(rc=2)"; return; fi
  if [ "$td" -eq 1 ]; then
    SKIP="휴장"; SKIP_LEVEL=info
    echo "  T=$D 는 거래일이 아니다 — 수집 전에 건너뜀"
    return
  fi
  DPREV=$(prev_day "$D")
  if [ -z "$DPREV" ]; then FAILED="직전 거래일 계산"; return; fi
  echo "  T=$D D'=$DPREV · $SWITCH ($CONF_NOTE)"
  echo "──── 장 마감 수집 시작 $(kst) ────"
  collect_cmd; local crc=$?
  echo "──── 장 마감 수집 종료 rc=$crc $(kst) ────"
  if [ "$crc" -eq 3 ]; then
    local why src
    why=$(session_reason "$D"); src=$?
    if [ "$src" -eq 0 ]; then
      SKIP="세션 예외일"; SKIP_LEVEL=warn
      NOTE="$why | 수집기 런 로그 session_exception · v3 소비자 T 행은 21:05 뒤 점수 없는 7표 반영(T-38), 모델은 다음 날 아침판(T-26 · T-7 대체 발송 경로)"
      echo "  T=$D 세션 예외일 — $why. 체인 전체를 건너뛴다(T-26)"
      return
    fi
    if [ "$src" -eq 2 ]; then
      FAILED="장 마감 수집(rc=3)"; NOTE="세션 예외표를 읽지 못해 rc 3 을 판정할 수 없다"
    else
      FAILED="장 마감 수집(rc=3)"; NOTE="세션 예외일 아님 — 수집기 락 경합 또는 15:41 전 시작(로그 확인)"
    fi
    return
  fi
  if [ "$crc" -ne 0 ]; then FAILED="장 마감 수집(rc=$crc)"; return; fi
  local board
  if ! board=$(board_ok "$DPREV" 2>&1); then
    FAILED="고정 판 확인"
    NOTE="$board — 빌드 락을 잡지 않았다. 그날 장 마감 판 없음(T-7 대체 발송 경로 · 21:05 뒤 점수 없는 7표 반영 T-38)"
    echo "  $NOTE"
    return
  fi
  echo "  $board"
  if ! build_lock; then FAILED="빌드 락 대기(rc=3)"; return; fi
  if step postclose_stage "stage 단독 빌드" stage_step; then
    echo "──── 장 마감 스냅샷 GC 시작 $(kst) ────"
    snapshot_gc || FAILED_SOFT="장 마감 스냅샷 GC"
    step postclose_fi "fi 장 마감 판" fi_cmd \
      && step postclose_model "모델 장 마감 판" model_cmd \
      && step postclose_excel "엑셀" excel_cmd
  fi
  build_unlock
  [ -z "$FAILED" ] && step postclose_v3 "v3 반영" v3_evening_cmd "$POST_CMD"
}

refill_main() {
  DPREV=$(prev_day "$D")
  if [ -z "$DPREV" ]; then FAILED="직전 거래일 계산"; return; fi
  local board
  if ! board=$(board_ok "$DPREV" 2>&1); then
    FAILED="고정 판 확인"; NOTE="$board — v3_post 를 부르지 않았다(옛 판이 7표에 섞이지 않게, P1)"
    echo "  $NOTE"
    return
  fi
  NOTE="점수 없는 7표 — 16:00 컷오프 종목은 21:05 원장 값, 장 마감 판이 없던 날은 그날 v3 T 행의 유일한 경로(T-38 · T-26)"
  echo "  T=$D D'=$DPREV $board · $NOTE"
  step postclose_v3_refill "21:05 원장 뒤 7표 반영" v3_noscores_cmd
}

morning_main() {
  local board
  if board=$(board_ok "$D" 2>&1); then
    echo "  $board"
    step postclose_v3_morning "v3 아침 KRX 재반영" v3_morning_cmd
  else
    FAILED="고정 판 확인"
    NOTE="$board — v3 아침 재반영(v3_post)을 부르지 않았다(옛 판이 섞이지 않게, P1)"
    echo "  $NOTE"
  fi
  if [ -z "$(last_status kiwoom_postclose)" ]; then
    NO_CLOSE="그날 장 마감 수집 런(kiwoom_postclose)이 없다 — 15:41 close 크론이 돌지 않았다(크론 누락?)"
    echo "  D=$D $NO_CLOSE"
  fi
  if [ "$(last_status postclose_model)" = ok ]; then
    ALT_RC=1; ALT_STATUS=mismatch
    step postclose_compare "두 판 대조" compare_cmd
    ALT_RC=""; ALT_STATUS=""
  else
    NOTE="${NOTE:+$NOTE · }그날 장 마감 모델 판(④ postclose_model 마지막 런 ok)이 없다 — 두 판 대조 건너뜀"
    echo "  D=$D $NOTE"
  fi
}

mkdir -p logs/postclose logs/health
LOG="logs/postclose/${D}_${MODE}.log"
T0=$(date +%s)
{
echo "════ [$(kst)] postclose_chain $MODE D=$D · $SWITCH ════"
case "$MODE" in
  close) close_main ;;
  refill) refill_main ;;
  morning) morning_main ;;
esac
echo "════ 종료 $(( ($(date +%s) - T0) / 60 ))분 failed=${FAILED:-없음} warned=${WARNED:-없음} skip=${SKIP:-없음} $(kst) ════"
} >> "$LOG" 2>&1

BODY="D=$D · $SWITCH | 완료:${DONE:- 없음}${NOTE:+ | $NOTE}${NO_CLOSE:+ | $NO_CLOSE} | 로그 $LOG"
case "$MODE" in close) LABEL="장 마감 체인" ;; refill) LABEL="장 마감 재반영" ;; *) LABEL="장 마감 판 아침 잇기" ;; esac
RC=0
if [ -n "$FAILED" ]; then
  scripts/notify.sh crit "$LABEL 실패: $FAILED" "$BODY"
  RC=2
elif [ -n "$SKIP" ]; then
  scripts/notify.sh "$SKIP_LEVEL" "$LABEL $SKIP — 건너뜀" "$BODY"
elif [ -n "$WARNED" ]; then
  scripts/notify.sh warn "$LABEL — 두 판 대조 불일치" "$BODY | 판정은 연속 창 집계($MDB/compare/${D}.json)"
  RC=1
elif [ "$MODE" = close ]; then
  scripts/notify.sh info "장 마감 판 준비 $(TZ=Asia/Seoul date +%H:%M)" "$BODY | $(( ($(date +%s) - T0) / 60 ))분"
elif [ -n "$NO_CLOSE" ]; then
  scripts/notify.sh warn "$LABEL 완료 — 그날 장 마감 체인 런 없음" "$BODY"
else
  scripts/notify.sh info "$LABEL 완료" "$BODY"
fi
if [ -n "$CHAIN_WARN" ]; then
  scripts/notify.sh warn "$LABEL 게이트 경고" "D=$D | $CHAIN_WARN | 판정은 그대로 — 사람이 본다 | 로그 $LOG"
fi
if [ -n "$FAILED_SOFT" ]; then
  scripts/notify.sh warn "$LABEL 기록형 단계 실패: $FAILED_SOFT" "판정에는 넣지 않는다 — $BODY"
fi
echo "postclose_chain $MODE D=$D rc=$RC failed=${FAILED:-없음} warned=${WARNED:-없음} skip=${SKIP:-없음} · $SWITCH · 로그 $LOG"
exit "$RC"
