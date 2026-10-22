#!/usr/bin/env bash
# S07 주간 리밸런싱 예약 실행(서버). 사용: weekly_rebalance_cron.sh prepare|open|chase|close|fills [HH:MM]
# 언제 도는지의 정본은 같은 폴더의 weekly_rebalance.crontab(서버 crontab, UTC)이다.
#
# 설정 파일(비밀값 없음, 기본 ~/s07/s07.env):
#   S07_BACKEND           배포한 backend 폴더(.venv 포함)
#   S07_WORKDIR           원장·주문안·기록 폴더
#   S07_MODEL_DB          모델 DB 폴더(quant-ledger data/model_db)
#   S07_LEDGER_HOME       quant-ledger 폴더 — 휴장 판정은 그 daily.calendar 한 곳을 읽는다
#   S07_REQUIRE_APPROVAL  1이면 승인한 주문안만 보낸다(매주 승인, 10-21 사용자 결정), 0이면 승인 없이 보낸다
#   STRATEGY_WORKBENCH_KIWOOM_ENV_FILE · STRATEGY_WORKBENCH_KIWOOM_KEY_PAIR  키를 읽을 .env와 쌍 번호
#   STRATEGY_WORKBENCH_TELEGRAM_ENV_FILE · _TOKEN_VAR · _CHAT_ID  주문지를 보낼 봇 토큰(.env의 이름)과 채팅
#
# 단계(휴장일에는 아무것도 하지 않는다):
#   fills    장중 실시간 체결 알림 기록(두 번째 인자 HH:MM 까지, crontab 이 넣는다)
#   open     직전 거래일 주문안이 있으면 전 종목을 낸다(매도 먼저)
#   chase    잔량을 취소하고 그때 2호가로 다시 낸다(마지막 회차 주문은 장 마감까지)
#   close    장 마감 대사·하루 기록·키움 원본
#   prepare  그 주 마지막 거래일이면 장 마감 빌드가 준비된 것을 확인한 뒤 주문안을 만들고
#            텔레그램으로 주문지를 보낸다(만들지 못했으면 보류·실패 알림)
# 휴장 판정을 할 수 없으면(연도 파일 부재 등) 휴장으로 위장하지 않고 실패한다.
# 단계 묶음마다 작업 폴더의 잠금 파일을 잡는다. open·chase 는 같은 잠금이라 겹쳐 돌지 않고, 앞 실행이
# 아직 돌면 이번 회차는 건너뛴다.
set -euo pipefail

CONF="${S07_CONF:-$HOME/s07/s07.env}"
set -a
# shellcheck disable=SC1090  # 서버마다 다른 설정 파일
. "$CONF"
set +a
PHASE="${1:?단계를 주세요: prepare|open|chase|close|fills}"
TODAY="$(TZ=Asia/Seoul date +%F)"

log() { echo "[$(TZ=Asia/Seoul date '+%F %T')] [$PHASE] $*"; }

calendar() {  # calendar trading|week_end|prev|next → stdout
  (cd "$S07_LEDGER_HOME" && PYTHONPATH=src .venv/bin/python - "$1" "$TODAY" <<'PY'
import datetime as dt
import sys

from daily import calendar as c

question, today = sys.argv[1], dt.date.fromisoformat(sys.argv[2])
cal = c.load()


def next_trading_day(day: dt.date) -> dt.date:
    for offset in range(1, 15):
        candidate = day + dt.timedelta(days=offset)
        if cal.is_trading_day(candidate):
            return candidate
    raise SystemExit(f"no trading day within 14 days after {day}")


if question == "trading":
    print("yes" if cal.is_trading_day(today) else "no")
elif question == "week_end":
    last = cal.is_trading_day(today) and (
        next_trading_day(today).isocalendar()[:2] != today.isocalendar()[:2]
    )
    print("yes" if last else "no")
elif question == "prev":
    print(cal.prev_trading_day(today).isoformat())
elif question == "next":
    print(next_trading_day(today).isoformat())
else:
    raise SystemExit(f"unknown question {question}")
PY
  )
}

wr() { (cd "$S07_BACKEND" && .venv/bin/python -m strategy_workbench.bootstrap.weekly_rebalance "$@"); }

approval_flag() { [ "${S07_REQUIRE_APPROVAL:-1}" = "0" ] && echo "--without-approval" || true; }

hold_lock() {  # hold_lock <묶음> — 같은 묶음 실행이 이미 돌면 건너뛴다
  mkdir -p "$S07_WORKDIR"
  exec 9>"$S07_WORKDIR/.lock-$1"
  if ! flock -n 9; then
    log "앞 실행이 아직 돈다(잠금 $1) — 건너뜀"
    exit 0
  fi
}

TRADING="$(calendar trading)"
if [ "$TRADING" != "yes" ]; then
  log "휴장일 — 건너뜀"
  exit 0
fi

case "$PHASE" in
  fills)
    hold_lock fills
    wr record-fills --workdir "$S07_WORKDIR" --until "${2:?끝낼 시각 HH:MM 을 주세요}"
    ;;
  open | chase)
    hold_lock orders
    SIGNAL="$(calendar prev)"
    PLAN="weekly-$SIGNAL"
    if [ ! -f "$S07_WORKDIR/plans/$PLAN.json" ]; then
      log "직전 거래일($SIGNAL) 주문안 없음 — 건너뜀"
      exit 0
    fi
    # shellcheck disable=SC2046  # 플래그가 없을 때 빈 인자를 넘기지 않는다
    wr execute --workdir "$S07_WORKDIR" --plan "$PLAN" --phase "$PHASE" --send $(approval_flag)
    ;;
  close)
    hold_lock close
    if ! ls "$S07_WORKDIR"/plans/weekly-*.json >/dev/null 2>&1; then
      log "주문안이 아직 없음 — 건너뜀"
      exit 0
    fi
    wr close --workdir "$S07_WORKDIR"
    ;;
  prepare)
    # 대입문에서 판정해야 실패가 set -e 로 멈춘다. `[ "$(…)" ]` 안의 실패는 빈 값이 되어 건너뜀으로 가려진다.
    WEEK_END="$(calendar week_end)"
    if [ "$WEEK_END" != "yes" ]; then
      log "그 주 마지막 거래일 아님 — 건너뜀"
      exit 0
    fi
    hold_lock prepare
    # 장 마감 빌드가 준비된 것을 확인하고 잇는다. 준비 판정은 신호 어댑터 한 곳(signal-status)이 한다:
    # 0 = 준비, 3 = 아직, 그 밖 = 다른 이유로 못 씀(prepare 가 판정해 알린다). 23시까지 1분마다 보고,
    # 그때도 아직이면 prepare 가 신호를 읽지 못한 것으로 끝나며 실패 알림을 보낸다.
    while true; do
      READY=0
      wr signal-status --workdir "$S07_WORKDIR" --model-root "$S07_MODEL_DB" \
        --signal-date "$TODAY" >/dev/null || READY=$?
      [ "$READY" -ne 3 ] && break
      if [ "$(TZ=Asia/Seoul date +%H)" -ge 23 ]; then
        log "오늘 장 마감 빌드가 끝나지 않음"
        break
      fi
      sleep 60
    done
    NEXT="$(calendar next)"
    wr prepare --workdir "$S07_WORKDIR" --model-root "$S07_MODEL_DB" --signal-date "$TODAY" \
      --notify --execute-on "$NEXT"
    ;;
  *)
    log "알 수 없는 단계"
    exit 2
    ;;
esac
