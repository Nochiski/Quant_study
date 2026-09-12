#!/usr/bin/env bash
# 텔레그램 알림 — quant-ledger 는 로그 채널(CHAT_ID_LOG)로 보낸다(사용자 지정 2026-09-09). 플랜 P0 Task 0.1.
#   사용: scripts/notify.sh <crit|warn|info> <title> [body]
#   · 같은 title 은 30분 쿨다운(/tmp/ql_notify_<hash>) — crit 은 쿨다운 없이 항상 보낸다
#   · 토큰·채팅방 ID 는 QL_ENV(없으면 ~/kael-system-v3/.env)에서 BOT_TOKEN·CHAT_ID_LOG 만 읽는다
#   · v3 infra/gpu_alert.sh 의 패턴을 그대로 옮겼다(실측으로 동작이 확인된 최소 구현)
set -uo pipefail
LEVEL="${1:?usage: notify.sh <crit|warn|info> <title> [body]}"
TITLE="${2:?title required}"
BODY="${3:-}"
ENV_FILE="${QL_ENV:-/home/kael/kael-system-v3/.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "notify: env file not found: $ENV_FILE" >&2
  exit 2
fi
# shellcheck disable=SC2046  # reason: KEY=VALUE 줄만 골라 export 한다(gpu_alert.sh 와 동일)
export $(grep -E "^(BOT_TOKEN|CHAT_ID_LOG)=" "$ENV_FILE" | xargs)
if [ -z "${BOT_TOKEN:-}" ] || [ -z "${CHAT_ID_LOG:-}" ]; then
  echo "notify: BOT_TOKEN / CHAT_ID_LOG missing in $ENV_FILE" >&2
  exit 2
fi
# 크론 로캘이 C/POSIX 면 ${TEXT:0:3900} 이 바이트 절단이라 한글 중간이 잘려 텔레그램이 400 을 낸다(검수 R4-09).
case "${LC_ALL:-${LANG:-}}" in *UTF-8*|*utf8*) ;; *) export LC_ALL=C.UTF-8 ;; esac
STAMP="/tmp/ql_notify_$(printf '%s' "$TITLE" | md5sum | cut -c1-12)"
NOW=$(date +%s)
LAST=0
[ -f "$STAMP" ] && LAST=$(stat -c %Y "$STAMP")
if [ "$LEVEL" != "crit" ] && [ $((NOW - LAST)) -lt 1800 ]; then
  echo "notify: cooldown, skipped ($TITLE)"
  exit 0
fi
case "$LEVEL" in
  crit) ICON="🚨" ;;
  warn) ICON="⚠️" ;;
  *)    ICON="ℹ️" ;;
esac
TEXT="${ICON} [quant-ledger] ${TITLE}
${BODY}"
RESP=$(curl -s -m 20 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
     --data-urlencode "chat_id=${CHAT_ID_LOG}" \
     --data-urlencode "text=${TEXT:0:3900}")
CRC=$?
# curl 은 HTTP 400 에도 rc 0 이다 — 응답 본문의 "ok":true 까지 봐야 "보냈다" 다.
if [ "$CRC" -eq 0 ] && printf '%s' "$RESP" | grep -q '"ok":true'; then
  touch "$STAMP"
  echo "notify: sent ($LEVEL: $TITLE)"
else
  echo "notify: send failed ($LEVEL: $TITLE) curl_rc=$CRC resp=${RESP:0:200}" >&2
  exit 1
fi
