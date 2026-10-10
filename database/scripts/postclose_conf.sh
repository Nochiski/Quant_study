# 장 마감 체인 설정 읽기 — config/postclose_chain.env 를 읽어 스위치를 정하는 공용 조각(컷오버 PR-8 · PR-9).
#   쓰는 곳: postclose_chain.sh(세 모드) · model_daily.sh(아침 발송 — 전환 뒤 짓기만·대체 발송) ·
#            watchdog.sh morning_build(B-57 발송 장부 — 전환 뒤 두 장부) · daily_ledger.sh·daily_evening.sh(휴장 파일
#            내보내기 스위치 calendar_export_v3_on 만 — T-48). 설정 판정은 이 파일 한 곳이다 —
#            설정 파일이 셸 대입이라 셸이 읽은 결과를 쓰고, 워치독 파이썬에는 결과(CUTOVER)를 인자로 넘긴다.
#   사용(quant-ledger 홈으로 cd 한 뒤, set -u 셸에서): . scripts/postclose_conf.sh; postclose_conf_load
#   규칙 — 켜는 쪽만 정확한 값을 요구한다(P1). 그 밖의 값·빈 값·파일 없음은 꺼짐·그림자다. 읽기는 set +u 안에서 한다
#     (빈 변수 참조가 셸을 조용히 끝내지 않게). 읽다 오류가 나면 읽힌 값만 쓴다(CONF_NOTE 에 남긴다).
#   결과 변수:
#     ENABLED=1   POSTCLOSE_ENABLED=1 — 장 마감 체인 세 모드가 돈다
#     SEND=1      POSTCLOSE_SEND=1 — 장 마감 판 엑셀을 보낸다(⑤ deliver --send)
#     SHADOW=1    POSTCLOSE_V3≠in-place — v3 그림자 반영(본 파일 무변경)
#     POST_CMD    제자리 반영일 때만 POSTCLOSE_V3_POST_CMD(그 밖엔 빈 값)
#     CUTOVER=1   원천 전환 뒤(PR-9 · T-7) = ENABLED=1 그리고 SEND=1. 장 마감 체인 ⑤ 가 D 의 엑셀을 먼저 보내므로
#                 아침판(model_daily)은 짓기만 하고 그 D 의 장 마감 발송 장부 줄이 없을 때만 대체 발송하며, 10:30
#                 워치독은 두 장부 중 하나의 그 D 줄을 발송 기록으로 본다. 새 변수를 두지 않는다 — 장 마감 발송이 켜졌는데
#                 아침도 보내면 중복, 꺼졌는데 아침이 안 보내면 무발송이라 두 값이 따로 놀 수 없다(P4). 되돌리기는
#                 POSTCLOSE_SEND=0 하나(아침이 다시 보낸다 — CUTOVER_ROLLBACK.md 4-1)
#     CONF_NOTE   사람이 읽을 설정 출처 한 줄(파일 없음·읽기 오류 표시)
CONF=config/postclose_chain.env
postclose_conf_load() {
  POSTCLOSE_ENABLED=""; POSTCLOSE_SEND=""; POSTCLOSE_V3=""; POSTCLOSE_V3_POST_CMD=""
  CONF_NOTE="설정 $CONF"
  if [ -f "$CONF" ]; then
    set +u
    # shellcheck source=/dev/null  # reason: 배포 산출물 설정 파일(변수 대입만)이라 정적 분석 대상이 아니다
    . "$CONF" || CONF_NOTE="설정 $CONF 읽기 오류 — 읽힌 값만 쓴다"
    set -u
  else
    CONF_NOTE="설정 $CONF 없음 — 꺼짐·그림자 기본값"
  fi
  ENABLED=""; [ "${POSTCLOSE_ENABLED:-}" = 1 ] && ENABLED=1
  SEND=""; [ "${POSTCLOSE_SEND:-}" = 1 ] && SEND=1
  SHADOW=1; [ "${POSTCLOSE_V3:-}" = in-place ] && SHADOW=""
  POST_CMD=""; [ -z "$SHADOW" ] && POST_CMD="${POSTCLOSE_V3_POST_CMD:-}"
  CUTOVER=""; [ -n "$ENABLED" ] && [ -n "$SEND" ] && CUTOVER=1
  return 0
}
# 조용한 손실 차단 스위치(K1-4a) — config/silent_loss.env 의 SILENT_LOSS_BLOCK 이 정확히 1 일 때만 rc 0(켜짐). 그 밖의 값·
# 빈 값·파일 없음·읽기 실패는 rc 1(꺼짐 — 기록형). 셸이 파이썬 없이 판정한다: 꺼져 있으면 15:41 장 마감 체인이 관문 파이썬을
# 부르지도 않아, 결과 파일·관문 예외·모듈 import 실패 어느 것도 체인을 막지 못한다. 규칙은 `daily.silent_loss.block_enabled`
# 와 같다(주석·빈 줄 건너뜀, 첫 '=' 로 가름, 이름·값 앞뒤 공백과 값 앞뒤 따옴표를 벗김, 마지막 대입이 이김) —
# tests/test_silent_loss.py 가 같은 입력으로 둘을 대조한다. source 하지 않는다(파일 안 명령이 돌지 않게)
silent_loss_block_on() {
  local v
  [ -f config/silent_loss.env ] || return 1
  v=$(awk '{ line = $0; gsub(/^[ \t\r]+|[ \t\r]+$/, "", line)
             if (line == "" || substr(line, 1, 1) == "#") next
             i = index(line, "="); if (i == 0) next
             name = substr(line, 1, i - 1); val = substr(line, i + 1)
             gsub(/^[ \t\r]+|[ \t\r]+$/, "", name); if (name != "SILENT_LOSS_BLOCK") next
             gsub(/^[ \t\r]+|[ \t\r]+$/, "", val); gsub(/^["\047]+|["\047]+$/, "", val); v = val }
           END { printf "%s", v }' config/silent_loss.env 2>/dev/null) || return 1
  [ "$v" = 1 ]
}
# 휴장 파일 내보내기 스위치(T-48 · QL-Q2) — config/calendar_export.env 의 CALENDAR_EXPORT_V3 가 정확히 1 일 때만 rc 0(켜짐).
# 그 밖의 값·빈 값·파일 없음·읽기 실패는 rc 1(꺼짐 — 지금 동작). 쓰는 곳: daily_ledger.sh(06:00 내보내기 + v3 사본 동기화
# 건너뜀) · daily_evening.sh(18:05 v3 사본 동기화 건너뜀) — 두 체인이 같은 판정을 쓰도록 여기 한 곳에 둔다. 읽기 규칙은
# 위 silent_loss_block_on 과 같다(주석·빈 줄 건너뜀, 첫 '=' 로 가름, 앞뒤 공백·값 따옴표를 벗김, 마지막 대입이 이김).
# source 하지 않는다 — 수집 체인이 설정 파일 안의 명령에 막히지 않게
calendar_export_v3_on() {
  local v
  [ -f config/calendar_export.env ] || return 1
  v=$(awk '{ line = $0; gsub(/^[ \t\r]+|[ \t\r]+$/, "", line)
             if (line == "" || substr(line, 1, 1) == "#") next
             i = index(line, "="); if (i == 0) next
             name = substr(line, 1, i - 1); val = substr(line, i + 1)
             gsub(/^[ \t\r]+|[ \t\r]+$/, "", name); if (name != "CALENDAR_EXPORT_V3") next
             gsub(/^[ \t\r]+|[ \t\r]+$/, "", val); gsub(/^["\047]+|["\047]+$/, "", val); v = val }
           END { printf "%s", v }' config/calendar_export.env 2>/dev/null) || return 1
  [ "$v" = 1 ]
}
