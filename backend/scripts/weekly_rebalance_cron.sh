#!/usr/bin/env bash
# S07 주간 리밸런싱 예약 실행(서버). 사용: weekly_rebalance_cron.sh prepare|open|chase|close|fills
#
# 설정 파일(비밀값 없음, 기본 ~/s07/s07.env):
#   S07_BACKEND           배포한 backend 폴더(.venv 포함)
#   S07_WORKDIR           원장·주문안·기록 폴더
#   S07_MODEL_DB          모델 DB 폴더(quant-ledger data/model_db)
#   S07_LEDGER_HOME       quant-ledger 폴더 — 휴장 판정은 그 daily.calendar 한 곳을 읽는다
#   S07_REQUIRE_APPROVAL  1이면 승인한 주문안만 보낸다(매주 승인, 10-21 사용자 결정), 0이면 승인 없이 보낸다
#   STRATEGY_WORKBENCH_KIWOOM_ENV_FILE · STRATEGY_WORKBENCH_KIWOOM_KEY_PAIR  키를 읽을 .env와 쌍 번호
#
# 단계(모두 KST, 휴장일에는 아무것도 하지 않는다):
#   fills    08:55  장중 실시간 체결 알림 기록(15:35까지)
#   open     09:05  직전 거래일 주문안이 있으면 전 종목을 낸다(매도 먼저)
#   chase    09:15~11:30 10분마다  잔량을 취소하고 그때 2호가로 다시 낸다(11:30 주문은 장 마감까지)
#   close    15:40  장 마감 대사·하루 기록·키움 원본
#   prepare  15:45  그 주 마지막 거래일이면 장 마감 빌드가 끝난 것을 확인한 뒤 주문안을 만든다
# 휴장 판정을 할 수 없으면(연도 파일 부재 등) 휴장으로 위장하지 않고 실패한다.
set -euo pipefail

CONF="${S07_CONF:-$HOME/s07/s07.env}"
set -a
# shellcheck disable=SC1090  # 서버마다 다른 설정 파일
. "$CONF"
set +a
PHASE="${1:?단계를 주세요: prepare|open|chase|close|fills}"
TODAY="$(TZ=Asia/Seoul date +%F)"

log() { echo "[$(TZ=Asia/Seoul date '+%F %T')] [$PHASE] $*"; }

calendar() {  # calendar trading|week_end|prev → stdout
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
else:
    raise SystemExit(f"unknown question {question}")
PY
  )
}

wr() { (cd "$S07_BACKEND" && .venv/bin/python -m strategy_workbench.bootstrap.weekly_rebalance "$@"); }

approval_flag() { [ "${S07_REQUIRE_APPROVAL:-1}" = "0" ] && echo "--without-approval" || true; }

TRADING="$(calendar trading)"
if [ "$TRADING" != "yes" ]; then
  log "휴장일 — 건너뜀"
  exit 0
fi

case "$PHASE" in
  fills)
    wr record-fills --workdir "$S07_WORKDIR" --until 15:35
    ;;
  open | chase)
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
    if ! ls "$S07_WORKDIR"/plans/weekly-*.json >/dev/null 2>&1; then
      log "주문안이 아직 없음 — 건너뜀"
      exit 0
    fi
    wr close --workdir "$S07_WORKDIR"
    ;;
  prepare)
    if [ "$(calendar week_end)" != "yes" ]; then
      log "그 주 마지막 거래일 아님 — 건너뜀"
      exit 0
    fi
    LATEST="$S07_MODEL_DB/model/latest_evening.json"
    # 장 마감 빌드가 끝난 것을 확인하고 잇는다. 날이 바뀌기 전까지 1분마다 본다.
    until python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("status")=="ok" and d.get("date")==sys.argv[2] else 1)' "$LATEST" "$TODAY"; do
      if [ "$(TZ=Asia/Seoul date +%H)" -ge 23 ]; then
        log "오늘 장 마감 빌드가 끝나지 않음 — 주문안을 만들지 못함"
        exit 1
      fi
      sleep 60
    done
    wr prepare --workdir "$S07_WORKDIR" --model-root "$S07_MODEL_DB" --signal-date "$TODAY"
    ;;
  *)
    log "알 수 없는 단계"
    exit 2
    ;;
esac
