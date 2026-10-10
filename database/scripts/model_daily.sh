#!/usr/bin/env bash
# 일간 모델 단계 — 확정판(basis=morning) 뒤 factor_inputs → model → 일간 엑셀 텔레그램 발송.
# 결정 N-25 Q0(임시 — 장 마감 직후 수집 N-13 이 가동되기 전까지) · Q9(발송 장부) · P9(앞 작업 끝에 잇는다).
#   사용: scripts/model_daily.sh --date YYYYMMDD [--resend]
#   호출: scripts/daily_build.sh 가 확정 빌드(build_morning) rc 0·1(판을 쓸 수 있음) 뒤에 자기 D 를 넘긴다.
#         손으로 다시 돌릴 때도 같은 명령이다(체인과 같은 환경 — 아래 cd·export).
#   순서: factor_inputs build → model build → deliver model-daily --send (전부 --basis morning).
#         한 단계라도 실패하면 뒤 단계는 돌지 않는다(발송 0). 실패 단계 이름·rc 는 notify crit 로
#         남긴다 — notify.sh 는 logs/notify.log 기록만이다(운영 로그 텔레그램 금지, P8). 모델 엑셀
#         발송은 deliver 몫이다.
#   중복 발송: deliver 가 발송 장부(data/deliver/sent_model_daily.jsonl)를 보고 이미 보낸 D 는 건너뛴다
#         (rc 0). 같은 날 다시 보내려면 --resend(캡션에 '정정 n'·판 id·생성 시각).
#   빌드 락: 세 단계 전체를 stage·equity 와 같은 빌드 락 안에서 돈다 — 아래 락 절 주석.
#   rc: 0 완료 · 실패하면 그 단계의 rc(deliver: 1 발송 실패 · 2 입력 오류 · 3 예상 밖 예외) · 2 인자 오류
#       · 3 빌드 락 열기·대기 실패 · 4 홈(~/quant-ledger) 이동 실패
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
  && step "deliver" $PY -m deliver model-daily --date "$D" --basis morning --send $RESEND
RC=$?
if [ "$RC" -ne 0 ]; then
  if [[ "$FAILED" == deliver* ]]; then
    # deliver 실패는 '발송 0' 이라 단정할 수 없다 — rc 3 은 발송 성공 뒤 장부 쓰기 실패일 수 있다(B-58)
    SENT="발송 여부는 deliver 출력과 장부(data/deliver/sent_model_daily.jsonl)로 확인 — rc 3 은 발송 뒤 장부 기록 실패일 수 있다"
  else
    SENT="그 뒤 단계는 돌지 않았다(엑셀 발송 0)"
  fi
  echo "모델 단계 실패: $FAILED — $SENT D=$D"
  scripts/notify.sh crit "모델 단계 실패: $FAILED" \
    "D=$D basis=morning | $SENT. 원인을 고친 뒤 scripts/model_daily.sh --date $D"
  exit "$RC"
fi
echo "모델 단계 완료 D=$D"
