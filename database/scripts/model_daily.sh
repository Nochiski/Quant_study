#!/usr/bin/env bash
# 일간 모델 단계 — 확정판(basis=morning) 뒤 factor_inputs → model → 일간 엑셀 텔레그램 발송.
# 결정 N-25 Q0(임시 — 장 마감 직후 수집 N-13 이 가동되기 전까지) · Q9(발송 장부) · P9(앞 작업 끝에 잇는다).
#   사용: scripts/model_daily.sh --date YYYYMMDD [--resend]
#   호출: scripts/daily_build.sh 가 확정 빌드(build_morning) rc 0·1(판을 쓸 수 있음) 뒤에 자기 D 를 넘긴다.
#         손으로 다시 돌릴 때도 같은 명령이다(체인과 같은 환경 — 아래 cd·export).
#   순서: factor_inputs build → model build → deliver model-daily --send (전부 --basis morning).
#         원천 전환 뒤(컷오버 PR-9 · T-7)엔 deliver 의 --send 를 장 마감 발송 장부로 정한다 — 아래 '원천 전환' 절.
#         한 단계라도 실패하면 뒤 단계는 돌지 않는다(발송 0). 실패 단계 이름·rc 는 notify crit 로
#         남긴다 — notify.sh 는 logs/notify.log 기록만이다(운영 로그 텔레그램 금지, P8). 모델 엑셀
#         발송은 deliver 몫이다.
#   중복 발송: deliver 가 발송 장부(data/deliver/sent_model_daily.jsonl)를 보고 이미 보낸 D 는 건너뛴다
#         (rc 0). 같은 날 다시 보내려면 --resend(캡션에 '정정 n'·판 id·생성 시각).
#   빌드 락: 세 단계 전체를 stage·equity 와 같은 빌드 락 안에서 돈다 — 아래 락 절 주석.
#   rc: 0 완료 · 실패하면 그 단계의 rc(deliver: 1 발송 실패 · 2 입력 오류 · 3 예상 밖 예외) · 2 인자 오류
#       · 3 빌드 락 열기·대기 실패 · 4 홈(~/quant-ledger) 이동 실패
#       · 5 원천 전환 뒤 장 마감 발송 장부 판정 불가(세 단계는 돌았고 엑셀은 보내지 않았다)
set -uo pipefail
cd "$HOME/quant-ledger" || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 돌지 않는다" >&2; exit 4; }
export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일 고정(RG-C7-4) — 배포 rsync --delete 밖, 바깥 값·옛 시스템 파일을 쓰지 않는다
PY=.venv/bin/python
D=""; RESEND=""
while [ $# -gt 0 ]; do
  case "$1" in
    # 값 없는 --date 는 D 를 비운 채 넘긴다 — 아래 형식 검사가 rc 2 로 거부한다(shift 2 실패 무한 반복 방지)
    --date) if [ $# -ge 2 ]; then D="$2"; shift 2; else shift; fi ;;
    --resend) RESEND="--resend"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [[ ! "$D" =~ ^[0-9]{8}$ ]]; then
  echo "--date YYYYMMDD 가 필요하다(받은 값: '$D') — 아무 단계도 돌지 않았다" >&2
  exit 2
fi
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
# 빌드 락(stage·equity 공용) — 하나로 두 가지를 막는다: ① 체인과 손 실행이 같은 D 를 동시에 돌려 발송
# 장부 확인~기록 사이에 둘 다 보내는 것 ② 모델 단계 도중 손 equity 재빌드가 섞인 판을 fi 가 읽는 것.
# daily_build 안에서는 build_chain 이 빌드 락을 푼 직후라 대기가 생기지 않는다(확정 빌드가 빌드 락을
# 못 잡으면 BRC 3 → 모델 단계 자체가 안 돈다). 쥐고 있으면 끝날 때까지 기다렸다 이어서 돈다 — 시간
# 한도 없음(P9, daily_build 원장 락 대기와 같은 모양). 빌드 락을 쥔 채 원장 락을 기다리는 스크립트는
# 없다(교착 없음). 부모가 이미 쥐었으면 QL_BUILD_LOCK_HELD=1 로 물려준다.
LOCK="${QL_BUILD_LOCK_FILE:-/tmp/quant_ledger_build.lock}"   # QL_BUILD_LOCK_FILE 은 테스트 전용(운영 락을 잡지 않게)
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  # 락 파일을 못 열면 멈춘다 — bash 는 exec 리다이렉트 실패에 멈추지 않아, 그대로 가면 daily_build 에서
  # 물려받은 fd 9(원장 락)가 빌드 락 행세를 한다
  exec 9>"$LOCK" || {
    echo "모델 단계 실패: 빌드 락 열기(rc=3) — $LOCK 을 열 수 없다, 아무 단계도 돌지 않았다(엑셀 발송 0) D=$D"
    scripts/notify.sh crit "모델 단계 실패: 빌드 락 열기(rc=3)" \
      "D=$D basis=morning | $LOCK 을 열 수 없다 — 아무 단계도 돌지 않았다(엑셀 발송 0)"
    exit 3
  }
  if ! flock -n 9; then
    W0=$(date +%s); W0_KST=$(kst)
    echo "[$W0_KST] model_daily 빌드 락 대기 시작 — 다른 빌드가 $LOCK 을 쥐고 있다(끝나면 이어서 돈다) D=$D"
    # 기록만(notify.sh → logs/notify.log). 늦은 엑셀을 '실패'가 아니라 '앞 빌드 대기'로 읽게 한다.
    scripts/notify.sh info "model_daily 빌드 락 대기" \
      "D=$D 시작 $W0_KST — 다른 빌드(stage·equity·모델)가 $LOCK 을 쥐고 있다. 끝나면 이어서 돈다"
    if ! flock 9; then
      # 대기형 flock 이 실패하면 락을 못 잡은 것이다 — 락 없이 fi·모델·발송을 돌지 않는다
      echo "모델 단계 실패: 빌드 락 대기(rc=3) — $LOCK 을 기다리다 flock 이 실패했다, 아무 단계도 돌지 않았다(엑셀 발송 0) D=$D"
      scripts/notify.sh crit "모델 단계 실패: 빌드 락 대기(rc=3)" \
        "D=$D basis=morning | $LOCK 을 기다리다 flock 이 실패했다 — 아무 단계도 돌지 않았다(엑셀 발송 0)"
      exit 3
    fi
    echo "[$(kst)] model_daily 빌드 락 대기 끝 — $(( $(date +%s) - W0 ))초 ($W0_KST ~ $(kst))"
  fi
  export QL_BUILD_LOCK_HELD=1
fi
# 원천 전환(컷오버 PR-9 · T-7) — 판정은 scripts/postclose_conf.sh 한 곳(config/postclose_chain.env 의 POSTCLOSE_ENABLED=1
# 그리고 POSTCLOSE_SEND=1 이면 전환 뒤, 그 밖은 전환 전 = 지금처럼 --send). 전환 뒤에는 장 마감 체인 ⑤ 가 같은 D 의
# 엑셀을 먼저 보내므로 아침판은 짓기만 한다(deliver 에 --send 없음 — 판은 v3 아침 재반영 T-34 가 쓴다). 장 마감 발송
# 장부(⑤ 의 --out-root data/model_db/deliver 아래)에 그 D 의 basis=evening 줄이 없을 때만 대체 발송한다(판 실패·세션
# 예외일 등 — warn 한 줄). 줄 판정은 deliver 의 장부 읽기(_sent·LEDGER_NAME)를 그대로 쓰고, 파일이 아예 없으면 '줄
# 없음'이다. 줄이 없으면 런 로그(data/raw/daily_run.db — 읽기는 daily.window_judge.read_runs, mode=ro)에서 그 D 의
# 장 마감 엑셀(postclose_excel) 런을 **전부** 본다(B-58 — 마지막 런만 보면 앞 런의 발송을 뒤 런이 가린다). 런의 rc·발송
# 표시는 장 마감 체인 step() 이 detail 에 남긴 'rc=<rc> 발송 on|off …' 다. 보냈을 수 있는 런이 하나라도 있으면 판정
# 불가: rc 3(발송 뒤 장부 기록 실패일 수 있다) · rc 0 '발송 on'(보냈거나 이미 보낸 D 를 건너뛴 런이라 장부 줄이 있어야
# 한다 — 없으면 장부 유실) · rc·발송 표시를 모름(끝 기록 없는 running · 그 밖의 rc 등). 런이 없거나 전부 rc 1·2 · rc 0
# '발송 off'(그림자 판)면 보내지 않은 것이라 대체 발송이다(deliver 계약 — 발송이 성공하면 장부 줄을 쓰고, 그 쓰기가
# 실패하면 rc 3). rc 1 의 모호함은 받아들인다 — 텔레그램 업로드 뒤 응답 시간 초과도 rc 1 이라 그날 대체 발송은 중복일
# 수 있다(docs/MODEL_DELIVER.md §2).
# 판정 불가 — 장부를 못 읽음(열기·파싱 실패·자리가 파일이 아님) · 런 로그를 못 읽음(파일·run 표 없음·sqlite 오류) ·
# 위의 보냈을 수 있는 런 · 판정 코드 예외 — 이면 보냈는지 모르므로 보내지 않고 crit · rc 5(P1 — 중복 발송·무발송 어느
# 쪽도 자동으로 고르지 않는다. 사람이 텔레그램을 보고 --resend 로 정한다).
# ⑤ 엑셀 ok·⑥ v3 실패인 날은 장부 줄이 있으므로 대체 발송하지 않는다(그 D 의 v3 점수는 T-34 아침 재반영이 채운다).
# --resend(사람 손 정정 발송)는 스위치와 무관하게 지금처럼 보낸다.
# shellcheck source=scripts/postclose_conf.sh
. scripts/postclose_conf.sh
postclose_conf_load
SEND_ARG="--send"; SEND_NOTE=""; LEDGER_UNKNOWN=""
EVENING_OUT=data/model_db/deliver
if [ -n "$CUTOVER" ] && [ -z "$RESEND" ]; then
  # rc 0 그 D 줄 있음 · 3 보내지 않음(줄 없음 + 엑셀 런이 없거나 전부 보내지 않은 런) · 그 밖(2 판정 불가 · 1 예외 등)은
  # 판정 불가.
  # 마지막 줄이 사람이 읽을 사유다
  EV_WHY=$($PY - "$EVENING_OUT" "$D" 2>&1 <<'PY'
import re
import sys
from pathlib import Path

from daily import window_judge as wj
from deliver.__main__ import LEDGER_NAME, _sent
from deliver.reader import DeliverError

p, d = Path(sys.argv[1]) / LEDGER_NAME, sys.argv[2]
try:
    if p.exists() and not p.is_file():
        raise DeliverError(f"발송 장부 {p} 가 파일이 아니다")
    rows = _sent(p, f"{d[:4]}-{d[4:6]}-{d[6:]}", "evening")
except DeliverError as e:
    print(e)
    sys.exit(2)
where = p if p.exists() else f"{p}(파일 없음)"
if rows:
    print(f"장 마감 발송 장부 {where} 의 D={d} basis=evening 줄 {len(rows)}건")
    sys.exit(0)
head = f"장 마감 발송 장부 {where} 의 D={d} basis=evening 줄 0건"
try:
    runs = [r for r in wj.read_runs(wj.RUN_DB).get(d, []) if r.source == "postclose_excel"]
except wj.InputError as e:
    print(f"{head} — 런 로그로 장 마감 엑셀 런을 확인하지 못했다: {e}")
    sys.exit(2)
if not runs:
    print(f"{head} · 런 로그 {wj.RUN_DB} 에 그 D 의 장 마감 엑셀(postclose_excel) 런 없음")
    sys.exit(3)


def maybe_sent(r: wj.Run) -> str | None:
    """보냈을 수 있으면 그 사유, 보내지 않은 런이면 None. detail 은 장 마감 체인 step() 의 'rc=<rc> 발송 on|off …'
    (끝 기록이 없으면 비어 있다)."""
    m = re.match(r"rc=(\d+)(?: 발송 (on|off))?(?: |$)", r.detail or "")
    rc, send = (int(m.group(1)), m.group(2)) if m else (None, None)
    if rc in (1, 2) or (rc == 0 and send == "off"):
        return None
    if rc == 3:
        return "rc 3 — 장 마감 발송 뒤 장부 기록 실패일 수 있다(B-58)"
    if rc == 0 and send == "on":
        return "rc 0 · 발송 on — 보냈다면 장부 줄이 있어야 하는데 없다(장부 유실 가능)"
    return f"status={r.status} detail={r.detail!r} — rc·발송 여부를 알 수 없다"


src = f"{wj.RUN_DB} 의 그 D 장 마감 엑셀(postclose_excel) 런 {len(runs)}건"
bad = [(r, why) for r in runs if (why := maybe_sent(r))]
if not bad:
    print(f"{head} · {src} 전부 보내지 않은 런({', '.join((r.detail or '?').split(' · ')[0] for r in runs)})")
    sys.exit(3)
r, why = bad[0]
more = f" 외 {len(bad) - 1}건" if len(bad) > 1 else ""
print(f"{head} · {src} 중 run_id={r.run_id} {why}{more}. 텔레그램 확인 뒤 손 발송(--resend)")
sys.exit(2)
PY
); EV_RC=$?
  EV_WHY=$(printf '%s\n' "$EV_WHY" | tail -1)
  case "$EV_RC" in
    0)
      SEND_ARG=""; SEND_NOTE="짓기만(장 마감 판 발송됨)"
      echo "원천 전환 뒤 — $EV_WHY → 아침판은 짓기만(발송 없음, T-7) D=$D"
      scripts/notify.sh info "모델 단계 짓기만 — 장 마감 판 발송됨" \
        "D=$D basis=morning | $EV_WHY — 아침판은 짓기만 한다(T-7)" ;;
    3)
      SEND_NOTE="대체 발송(장 마감 판 미발송)"
      echo "원천 전환 뒤 — $EV_WHY → 아침판 대체 발송(T-7) D=$D"
      scripts/notify.sh warn "장 마감 판 미발송 → 아침판 대체 발송" \
        "D=$D | $EV_WHY — 장 마감 판이 이 D 를 보내지 못했다(판 실패·세션 예외일 등). 아침판을 보낸다(T-7)" ;;
    *)
      SEND_ARG=""; LEDGER_UNKNOWN=1
      echo "원천 전환 뒤 — 장 마감 발송 장부 판정 불가(rc=5): $EV_WHY → 보내지 않는다(짓기만) D=$D"
      scripts/notify.sh crit "모델 단계 실패: 장 마감 발송 장부 판정 불가(rc=5)" \
        "D=$D basis=morning | 장 마감 발송 장부($EVENING_OUT) — $EV_WHY — 장 마감 판을 보냈는지 몰라 아침판을 보내지 않는다(짓기만, P1). 텔레그램에서 그 D 장 마감 판 도착을 확인하고, 안 왔으면 손 발송 scripts/model_daily.sh --date $D --resend(장부가 손상됐으면 먼저 고친다)" ;;
  esac
fi
FAILED=""
step() {
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$name(rc=$rc)"; fi
  return "$rc"
}
echo "════ [$(kst)] model_daily D=$D basis=morning${RESEND:+ $RESEND} ════"
step "factor_inputs" $PY -m factor_inputs build --date "$D" --basis morning \
  && step "model" $PY -m model build --date "$D" --basis morning \
  && step "deliver" $PY -m deliver model-daily --date "$D" --basis morning $SEND_ARG $RESEND
RC=$?
if [ "$RC" -ne 0 ]; then
  if [[ "$FAILED" == deliver* ]] && [ -n "$SEND_ARG" ]; then
    # deliver 실패는 '발송 0' 이라 단정할 수 없다 — rc 3 은 발송 성공 뒤 장부 쓰기 실패일 수 있다(B-58)
    SENT="발송 여부는 deliver 출력과 장부(data/deliver/sent_model_daily.jsonl)로 확인 — rc 3 은 발송 뒤 장부 기록 실패일 수 있다"
  elif [[ "$FAILED" == deliver* ]]; then
    SENT="--send 없는 실행(원천 전환 뒤 짓기만)이라 엑셀 발송 0"
  else
    SENT="그 뒤 단계는 돌지 않았다(엑셀 발송 0)"
  fi
  echo "모델 단계 실패: $FAILED — $SENT D=$D"
  scripts/notify.sh crit "모델 단계 실패: $FAILED" \
    "D=$D basis=morning | $SENT. 원인을 고친 뒤 scripts/model_daily.sh --date $D"
  exit "$RC"
fi
if [ -n "$LEDGER_UNKNOWN" ]; then
  # crit 은 판정 때 냈다. daily_build 요약이 이 줄을 맨 앞에 싣는다
  echo "모델 단계 실패: 장 마감 발송 장부 판정 불가(rc=5) — 아침판은 지었고 보내지 않았다 D=$D"
  exit 5
fi
echo "모델 단계 완료 D=$D${SEND_NOTE:+ · $SEND_NOTE}"
