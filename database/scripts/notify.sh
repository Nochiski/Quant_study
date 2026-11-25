#!/usr/bin/env bash
# 알림 — 모든 등급을 먼저 로컬 logs/notify.log 에 한 줄 남긴다. 텔레그램은 서버 전용 설정이 고른 등급만 보낸다(N-73).
# 플랜 P0 Task 0.1.
#   사용: scripts/notify.sh <crit|warn|info> <title> [body]
#   · 보낼지는 서버 전용 local/notify.env 가 정한다(배포가 건드리지 않는 자리 — 채널 · 켜고 끄기는 공개 저장소에 두지 않는다,
#     10-27 D9). 셸 대입 형식 세 줄이다:
#       QL_NOTIFY_TELEGRAM_LEVELS   보낼 등급(공백 · 쉼표로 나열, 예: crit). 비면 보내지 않는다
#       QL_NOTIFY_CHAT_VAR          QL_ENV 에서 읽을 채팅 변수 이름(예: CHAT_ID_AIPLAYGROUND)
#       QL_NOTIFY_SKIP_TITLES       제목이 이 정규식(grep -E)에 맞으면 보내지 않는다(예: '^일일 리포트' — 다른 crit 의 요약)
#     파일이 없으면 기록만 한다(2026-10-01 사용자 지시 그대로) — 파일을 지우면 곧바로 꺼진다
#   · QL_NOTIFY_TELEGRAM=1 은 옛 스위치다 — 모든 등급을 CHAT_ID_LOG 로 보낸다(crit 은 쿨다운 없음)
#   · 같은 title 은 30분에 한 번만 보낸다(<QL_NOTIFY_STAMP_DIR — 기본 /tmp>/ql_notify_<hash>). 설정 파일 경로에서는 crit 도
#     같다 — 사건 하나가 crit 여러 통을 낸다(11-21 Q2). 기록은 매번 남는다
#   · 토큰·채팅방 ID 는 QL_ENV 에서 BOT_TOKEN 과 그 채팅 변수만 읽는다 — 보낼 때만. QL_ENV 가 비면
#     다른 파일로 대신하지 않고 rc 2(RG-C7-4). 기록만 하는 날은 비밀 파일 없이 돈다
#   · 전송 실패는 stderr 뿐 아니라 logs/notify_failed.log 에도 한 줄 남긴다(DEFECT-D04) —
#     수집 체인 3개는 크론 리다이렉트가 없어 stderr 가 버려지므로, 그 파일이 유일한 사후 증거다
#   · v3 infra/gpu_alert.sh 의 패턴을 그대로 옮겼다(실측으로 동작이 확인된 최소 구현)
set -uo pipefail
LEVEL="${1:?usage: notify.sh <crit|warn|info> <title> [body]}"
TITLE="${2:?title required}"
BODY="${3:-}"
ROOT="${QL_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
# 기록이 먼저다 — 보내다 죽어도 세션 시작 crit 보고 · 연속 창 판정이 읽는 줄은 남는다
NOTIFY_LOG="$ROOT/logs/notify.log"
mkdir -p "$(dirname "$NOTIFY_LOG")" 2>/dev/null
# 로캘이 C 이면 ${BODY:0:900} 이 바이트로 잘린다 — 끝에서 잘린 글자 조각은 iconv -c 가 버린다(#630)
printf '%s %s %s | %s\n' "$(date -u +%FT%TZ)" "$LEVEL" "$TITLE" \
  "$(printf '%s' "${BODY:0:900}" | iconv -c -f UTF-8 -t UTF-8 2>/dev/null | tr '\n\t' '  ')" >> "$NOTIFY_LOG" 2>/dev/null

# 전송 실패를 파일로 남긴다 — stderr 는 호출부(크론 리다이렉트 없는 체인)에서 버려지고 호출부가 rc 를 안 본다(D04)
fail_log() {
  local f="$ROOT/logs/notify_failed.log"
  mkdir -p "$(dirname "$f")" 2>/dev/null
  printf '%s %s %s %s\n' "$(date -u +%FT%TZ)" "$LEVEL" "$TITLE" "$1" >> "$f" 2>/dev/null
}

# 보낼지 · 어디로 — 옛 스위치가 먼저, 다음이 서버 전용 설정 파일. 환경 변수만으로는 켜지지 않는다
SEND=0; CHAT_VAR=CHAT_ID_LOG; CRIT_COOLDOWN=0
if [ "${QL_NOTIFY_TELEGRAM:-0}" = "1" ]; then
  SEND=1
elif [ -f "$ROOT/local/notify.env" ]; then
  QL_NOTIFY_TELEGRAM_LEVELS=""; QL_NOTIFY_CHAT_VAR=""; QL_NOTIFY_SKIP_TITLES=""
  set +u
  # shellcheck source=/dev/null  # reason: 서버에만 있는 설정 파일(변수 대입만)이라 정적 분석 대상이 아니다
  . "$ROOT/local/notify.env"
  set -u
  CHAT_VAR="${QL_NOTIFY_CHAT_VAR:-}"; CRIT_COOLDOWN=1
  case " ${QL_NOTIFY_TELEGRAM_LEVELS//,/ } " in *" $LEVEL "*) SEND=1 ;; esac
  if [ "$SEND" = 1 ] && [ -n "${QL_NOTIFY_SKIP_TITLES:-}" ] \
     && printf '%s' "$TITLE" | grep -Eq -- "$QL_NOTIFY_SKIP_TITLES"; then
    echo "notify: logged only — skip title ($LEVEL: $TITLE)"
    exit 0
  fi
fi
if [ "$SEND" != 1 ]; then
  echo "notify: logged only ($LEVEL: $TITLE)"
  exit 0
fi
if [ -z "${QL_ENV:-}" ]; then
  echo "notify: QL_ENV is empty — set it to the quant-ledger secret file (\$HOME/quant-ledger/.env)" >&2
  fail_log "config QL_ENV empty"
  exit 2
fi
ENV_FILE="$QL_ENV"
if [ ! -f "$ENV_FILE" ]; then
  echo "notify: env file not found: $ENV_FILE" >&2
  fail_log "config env file not found"
  exit 2
fi
if [[ ! "$CHAT_VAR" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
  echo "notify: QL_NOTIFY_CHAT_VAR is not a variable name — fix local/notify.env" >&2
  fail_log "config QL_NOTIFY_CHAT_VAR invalid"
  exit 2
fi
# shellcheck disable=SC2046  # reason: KEY=VALUE 줄만 골라 export 한다(gpu_alert.sh 와 동일)
export $(grep -E "^(BOT_TOKEN|${CHAT_VAR})=" "$ENV_FILE" | xargs)
CHAT_ID="${!CHAT_VAR:-}"
if [ -z "${BOT_TOKEN:-}" ] || [ -z "$CHAT_ID" ]; then
  echo "notify: BOT_TOKEN / $CHAT_VAR missing in $ENV_FILE" >&2
  fail_log "config BOT_TOKEN / $CHAT_VAR missing"
  exit 2
fi
# 크론 로캘이 C/POSIX 면 ${TEXT:0:3900} 이 바이트 절단이라 한글 중간이 잘려 텔레그램이 400 을 낸다(검수 R4-09).
case "${LC_ALL:-${LANG:-}}" in *UTF-8*|*utf8*) ;; *) export LC_ALL=C.UTF-8 ;; esac
STAMP="${QL_NOTIFY_STAMP_DIR:-/tmp}/ql_notify_$(printf '%s' "$TITLE" | md5sum | cut -c1-12)"
if { [ "$LEVEL" != "crit" ] || [ "$CRIT_COOLDOWN" = 1 ]; } && [ -n "$(find "$STAMP" -mmin -30 2>/dev/null)" ]; then
  echo "notify: cooldown, skipped ($TITLE)"
  exit 0
fi
case "$LEVEL" in
  crit) ICON="🚨" ;;
  warn) ICON="⚠️" ;;
  *)    ICON="ℹ️" ;;
esac
TEXT="${ICON} [quant-ledger] ${TITLE}
$(TZ=Asia/Seoul date '+%m-%d %H:%M KST') ${BODY}"
RESP=$(curl -s -m 20 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
     --data-urlencode "chat_id=${CHAT_ID}" \
     --data-urlencode "text=${TEXT:0:3900}")
CRC=$?
# curl 은 HTTP 400 에도 rc 0 이다 — 응답 본문의 "ok":true 까지 봐야 "보냈다" 다.
if [ "$CRC" -eq 0 ] && printf '%s' "$RESP" | grep -q '"ok":true'; then
  touch "$STAMP"
  echo "notify: sent ($LEVEL: $TITLE)"
else
  fail_log "curl_rc=$CRC resp=$(printf '%s' "${RESP:0:200}" | tr '\n\t' '  ')"
  echo "notify: send failed ($LEVEL: $TITLE) curl_rc=$CRC resp=${RESP:0:200}" >&2
  exit 1
fi
