#!/usr/bin/env bash
# v3 quant.db 반영 — 컷오버 트랙 QL-F·QL-F2(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 · T-16 · T-27 ·
#   T-31 · T-34 · T-35 · T-38, 로드맵 §8 K3-2·K3-3). compat 을 v3 파일에 직접 돌리면 표마다 따로 커밋해 반영 도중 소비자가 일부
#   표만 바뀐 상태를 읽고(G9), 필수 열이 빈 행을 5% 까지 조용히 건너뛴다(G11). 그래서 아래 순서로 한다.
#   ① 스테이징: v3 quant.db 를 sqlite 온라인 백업(CLI `.backup` 과 같은 API)으로 임시 파일에 뜬다(`compat stage` —
#      경로 가드 · 디스크 여유 본 파일 × 2 · 실패하면 부분 사본을 지운다)
#   ①' 반영 표(`compat v3-tables`, T-34 · T-38): 기본 9표. `--basis morning` 은 본 파일에 같은 D 의 점수 두 표를
#      반영한 장 마감 ok 기록이 있으면 점수 두 표를 뺀 7표(저녁에 보낸 엑셀과 v3 점수가 같게, 가격 재반영이 아침 모델
#      판에 묶이지 않게). `--no-scores` 는 늘 7표(아래)
#   ② compat export --in-place --tables <반영 표> 를 스테이징에(QL-B 가드 — stocks.market_cap 은 전 종목 all 고정)
#   ③ 게이트(COMMIT 전): 이번 compat 기록 1행·status ok·날짜·basis · 순서(T-35 — 본 파일에 더 나중 반영 기록이
#      있으면 실패, 재생은 --allow-older) · 반영 표 전부(반영 표 밖의 표를 쓴 기록도 실패) · 필수 열이 빈 행 0건(그림자
#      compat 의 5% 허용을 제자리에서는 0 으로 — P1) · 신선도(이번 compat 이 daily_prices 에 쓴 D 행 ≥ 1, T-31 ③) ·
#      표마다 반영 범위 행 > 0
#   ④ 통과하면 반영 표를 v3 quant.db 에 한 트랜잭션으로(ATTACH · BEGIN IMMEDIATE · 표별 범위 DELETE/INSERT · COMMIT).
#      범위는 compat 이 쓴 그대로(스테이징 `_compat_meta` 기록의 창) — compat 이 쓰지 않는 표(market_* 등, T-27)는
#      건드리지 않는다. 범위·게이트 정의는 `src/compat/v3_post.py` 머리 주석이 정본
#   ⑤ v3 daily_post 호출(--v3-post-cmd, `--basis evening` 만). 명령이 없으면 건너뛰고 기록만
#   ③④ 는 `compat apply` 한 번이다. COMMIT 전에 실패하면 v3 본 파일은 그대로다.
#
#   daily_post(T-31) = v3 job_runner 의 holiday_gate·export_scores 두 잡이다. calendar_refresh 는 넣지 않는다(KIS 를
#     불러 `.kis_holidays.json` 을 덮는다 — 휴장 파일 정본은 `daily.calendar_export`, T-24). insight·wiki 는 v3 의 지금
#     20:05 자리 별도 체인 `daily_insight` 로 두고 이 셸이 부르지 않는다(변경 목록 COMPAT_LAYER §8).
#   --shadow(그림자 기간): ①~③ 만 한다. v3 본 파일에 쓰지 않고 v3 락도 잡지 않는다 — v3 20:05 daily_all 크론이
#     `flock -n` 이라 우리가 락을 쥐고 있으면 그날 v3 체인이 조용히 건너뛰어진다. daily_post 도 부르지 않는다.
#     스테이징 파일은 대조용으로 남긴다(기본 경로에 `_shadow` 접미 — 제자리 실행의 사본과 섞이지 않게).
#   기본 스테이징 경로는 `data/_v3_post/staging_<basis>[_noscores][_shadow].db` — 점수 없는 refill 이 그날 ⑥(점수 포함)이
#     실패해 남긴 스테이징을 지우지 않게 `_noscores` 를 붙인다(QL-F2 리뷰 MINOR-1).
#   락(제자리 모드): v3 체인과 같은 `/tmp/kael_v3_daily_all.lock`(v3 crontab daily_all 줄의 `flock -n` — v3 로컬 사본
#     `scripts/cron_schedule.sh:3`). ①~⑤ 동안 쥔다. 잡혀 있으면 풀릴 때까지 기다린다(시간 한도 없음 — P9, 늦어짐
#     경보는 워치독 몫, 대기 순서 문제는 ③ 순서 가드가 막는다). --v3-post-cmd 는 같은 락을 다시 잡지 않는 형태여야
#     한다(v3 crontab 줄에서 flock 만 뺀 것 — 다시 잡으면 자기 자신을 기다린다). 자식에게는 락 fd 를 넘기지 않는다
#     (raw_lock.sh 5-3 리뷰 중-1 과 같은 이유).
#   호출 시점(이 PR 은 기록만 — 크론·체인 연결은 PR-8·PR-9): ⓐ 장 마감 판 체인 끝 `--basis evening` + daily_post
#     ⓑ 다음 날 아침 KRX 확정 반영 뒤 `--basis morning`, compat 만 — post 명령을 주면 인자 오류(rc 5)다.
#     ⓒ 21:05 키움 원장 커밋 뒤 재반영(refill) — 늘 `--basis evening --no-scores`, compat 만(T-38). 점수는 ⓐ 만 쓴다.
#     점수 두 표를 뺀 7표라 장 마감 모델 판이 없는 날(판 실패일·세션 예외일 T-26)에도 돈다. T 행 원천은 QL-D
#     그대로(postclose.db 에 그날 행이 없으면 전 종목 21:05 원장 — 파일 자체가 없으면 멈춘다). 그날 ⓐ 의 점수 포함
#     기록이 있으면 다음 날 아침 ⓑ 는 7표, 없으면 아침 모델 판으로 점수를 채운다(이 기록엔 점수 표가 없어 T-34 가
#     세지 않는다). 아침·post 명령과 함께 주면 인자 오류(rc 5) — 아침 반영 표는 T-34 가 정하고, daily_post
#     (export_scores)는 그날 점수 없이 부르지 않는다.
#     휴장 파일 내보내기(QL-Q)는 06:00 체인 몫이라 여기 없다.
#   daily_post 의 v3 잡은 그날을 `date.today()`(프로세스 로컬 시간대)로 정한다(v3 `scripts/check_today_business.py:17`·
#     `scripts/export_and_send.py:124`). 그래서 이 셸의 로컬 날짜(`date +%Y%m%d` — 자식과 같은 시간대)가 D 일 때만
#     부른다. 다르면 부르지 않고 warn(플랜 `2026-09-24-v3-merge.md` 위험표 — 자정 넘김).
#   창 밖 adj_close NULL(QL-E): 게이트 줄의 `rebase_null=N` 이 0 이 아니면 warn 한 줄(정지 아님) — compat 이 창 안
#     사건 종목의 창 밖 행을 다시 맞출 때 equity 행이 없는 날은 adj_close 를 NULL 로 둔다(P1).
#   rc: 0 완료 · 2 반영 실패(crit — COMMIT 전이면 v3 본 파일 무변경) · 3 락 실패(warn) · 4 홈 이동 실패 ·
#       5 인자 오류(warn — 스테이징 경로가 본 파일과 같은 파일, 아침 + post 명령, --no-scores + 아침,
#         --no-scores + post 명령 포함) ·
#       6 반영 완료, daily_post 실패 또는 날짜가 달라 미호출(warn)
#   사용: v3_post.sh --date YYYYMMDD --basis evening|morning --v3-db PATH
#                    [--v3-post-cmd CMD] [--shadow] [--allow-older] [--staging PATH] [--model-root PATH]
#                    [--full] [--builds-from PATH] [--consensus-asof YYYYMMDD] [--no-scores]
#     --full 은 첫 반영(K3-2 — 730일 창을 한 트랜잭션으로)과 얕은 대상용이다. 매일 쓰지 않는다(COMPAT_LAYER §8 V3-C).
#     --allow-older 는 재생 전용이다(T-35). ② compat export 와 ③④ apply 둘 다에 넘긴다 — compat 의 장 마감 판도
#     같은 순서 판정으로 더 나중 반영 위에 쓰기를 거부한다(QL-D).
#   ② 의 장 마감 판(--basis evening)은 가격·수급 T 행을 원장 두 개에서 만든다(QL-D) — QL_POSTCLOSE_DB(기본
#     data/raw/postclose.db, 15:41 수집)·QL_KIWOOM_DB(기본 data/raw/kiwoom.db, 21:05 저녁 수집)를 저녁에만 넘긴다.
#     T 의 직전 거래일과 증분 창(--full 아니면 D + 앞 10거래일 = 08:10 KRX 재수집 창, K1-9d)은 compat 이 판정 달력(QL_HOME/data/calendar)으로 센다.
#   환경변수: QL_V3_DB · QL_V3_POST_CMD(인자 대신) · QL_EQUITY_ROOT · QL_STAGE_ROOT · QL_MODEL_ROOT(compat 원천 루트,
#     compat_export.sh 와 같다) · QL_POSTCLOSE_DB · QL_KIWOOM_DB(장 마감 판 T 행 원장). QL_V3_LOCK_FILE · QL_V3_POST_TODAY(YYYYMMDD) 는 테스트 전용 — 락 경로·오늘 날짜를
#     덮어쓴다. 운영 크론·대화형 셸에 남겨 두지 않는다.
set -uo pipefail
cd "${QL_HOME:-$HOME/quant-ledger}" || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
QL_HOME="$(pwd)"
export QL_HOME PYTHONPATH="$QL_HOME/src"
PY=.venv/bin/python
V3_LOCK="${QL_V3_LOCK_FILE:-/tmp/kael_v3_daily_all.lock}"
EQUITY_ROOT="${QL_EQUITY_ROOT:-data/equity}"
STAGE_ROOT="${QL_STAGE_ROOT:-data/stage}"
MODEL_ROOT="${QL_MODEL_ROOT:-data/model}"
POSTCLOSE_DB="${QL_POSTCLOSE_DB:-data/raw/postclose.db}"
KIWOOM_DB="${QL_KIWOOM_DB:-data/raw/kiwoom.db}"
V3_DB="${QL_V3_DB:-}"; POST_CMD="${QL_V3_POST_CMD:-}"
D=""; BASIS=""; SHADOW=""; STAGING=""; FULL=""; BUILDS=""; CONS=""; OLDER=""; NOSCORES=""
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
die_arg() {
  echo "v3_post 인자 오류: $1" >&2
  scripts/notify.sh warn "v3_post 인자 오류" "$1 — 실행하지 않았다"
  exit 5
}
need_val() { [ $# -ge 2 ] && [ -n "${2:-}" ] || die_arg "$1 에 값이 없다"; }
# 스테이징이 v3 본 파일(·-wal/-shm/-journal)과 같은 파일인가 — ① 이 스테이징 경로를 지우고 뜨므로 본 파일이
# 사라진다(QL-F 리뷰 MAJOR-1). `-ef` 는 링크를 풀어 장치·inode 로 본다. 파이썬 쪽(`compat.v3_post.guard_paths`)도 본다.
same_as_v3() {
  local a b
  for a in "" -wal -shm -journal; do
    for b in "" -wal -shm -journal; do
      [ "$STAGING$a" -ef "$V3_DB$b" ] && return 0
    done
  done
  return 1
}
while [ $# -gt 0 ]; do
  case "$1" in
    --date) need_val "$@"; D="$2"; shift 2 ;;
    --basis) need_val "$@"; BASIS="$2"; shift 2 ;;
    --v3-db) need_val "$@"; V3_DB="$2"; shift 2 ;;
    --v3-post-cmd) need_val "$@"; POST_CMD="$2"; shift 2 ;;
    --staging) need_val "$@"; STAGING="$2"; shift 2 ;;
    --model-root) need_val "$@"; MODEL_ROOT="$2"; shift 2 ;;
    --builds-from) need_val "$@"; BUILDS="$2"; shift 2 ;;
    --consensus-asof) need_val "$@"; CONS="$2"; shift 2 ;;
    --shadow) SHADOW=1; shift ;;
    --allow-older) OLDER=1; shift ;;
    --no-scores) NOSCORES=1; shift ;;
    --full) FULL="--full"; shift ;;
    *) die_arg "모르는 인자 '$1'" ;;
  esac
done
case "$D" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) die_arg "--date 는 YYYYMMDD 여야 한다(받은 값 '${D}') — 아침 재반영은 전날 D 라 기본값을 두지 않는다" ;;
esac
case "$BASIS" in
  evening|morning) ;;
  *) die_arg "--basis 는 evening|morning 이어야 한다(받은 값 '${BASIS}')" ;;
esac
[ "$BASIS" = morning ] && [ -n "$POST_CMD" ] &&
  die_arg "--basis morning 에 daily_post 명령(--v3-post-cmd·QL_V3_POST_CMD)을 줬다 — 아침 재반영은 compat 만(T-31)"
[ -n "$NOSCORES" ] && [ "$BASIS" != evening ] &&
  die_arg "--no-scores 는 --basis evening 전용이다 — 아침 반영 표는 T-34 가 정한다(T-38)"
[ -n "$NOSCORES" ] && [ -n "$POST_CMD" ] &&
  die_arg "--no-scores 에 daily_post 명령(--v3-post-cmd·QL_V3_POST_CMD)을 줬다 — 점수 없는 반영은 compat 만(T-38)"
[ -n "$V3_DB" ] || die_arg "--v3-db(또는 QL_V3_DB)가 없다 — v3 quant.db 경로는 인자로만 받는다"
[ -f "$V3_DB" ] || die_arg "--v3-db 파일이 없다: $V3_DB"
STAGING="${STAGING:-data/_v3_post/staging_${BASIS}${NOSCORES:+_noscores}${SHADOW:+_shadow}.db}"
same_as_v3 && die_arg "--staging 이 v3 본 파일과 같은 파일이다($STAGING = $V3_DB) — 스테이징을 뜨기 전에 지우므로 거부"
MODE=$([ -n "$SHADOW" ] && echo shadow || echo in-place)
# 장 마감 판 T 행 원천(QL-D) — 저녁에만 넘긴다(compat_export.sh 와 같은 방식)
LEDGERS=()
[ "$BASIS" = evening ] && LEDGERS=(--postclose-db "$POSTCLOSE_DB" --kiwoom-db "$KIWOOM_DB")
mkdir -p logs/v3_post
LOG="logs/v3_post/${D}_${BASIS}.log"
if [ -z "$SHADOW" ]; then
  exec 9>"$V3_LOCK"
  if ! flock -n 9; then
    echo "[$(kst)] v3 락 대기 시작 — $V3_LOCK 을 다른 실행이 쥐고 있다(풀리면 이어서 돈다)" | tee -a "$LOG"
    scripts/notify.sh info "v3_post $D $BASIS v3 락 대기" "$V3_LOCK 을 다른 실행이 쥐고 있다 — 풀리면 이어서 돈다"
    if ! flock 9; then
      scripts/notify.sh warn "v3_post $D $BASIS 락 실패" "$V3_LOCK 을 기다리다 flock 이 실패했다 — v3 본 파일 무변경"
      exit 3
    fi
    echo "[$(kst)] v3 락 대기 끝" | tee -a "$LOG"
  fi
fi
# 파이프(`| tee`) 안의 RC 는 서브셸에 갇힌다 — 파일로 꺼낸다(compat_export.sh 와 같은 방식).
# COMMIT 표식(CF)은 `compat apply` 가 COMMIT 직후 만든다 — 실패 알림의 '무변경' 을 사실일 때만 쓴다(NIT 2).
RCF=$(mktemp)
CF="$RCF.commit"
{
echo "════ [$(kst)] v3_post 시작 date=$D basis=$BASIS scores=$([ -n "$NOSCORES" ] && echo no || echo yes) mode=$MODE v3_db=$V3_DB staging=$STAGING ════"
RC=0; STEP=""; TABLES=""
step() {  # step <이름> <명령…> — 실패하면 RC=2 와 STEP 을 남긴다
  local src
  STEP="$1"; shift
  echo "──── [$(kst)] $STEP"
  "$@"; src=$?
  if [ "$src" -ne 0 ]; then RC=2; echo "──── $STEP 실패 rc=$src $(kst)"; return 1; fi
}
plan_tables() {
  TABLES=$("$PY" -m compat v3-tables --v3-db "$V3_DB" --date "$D" --basis "$BASIS" ${NOSCORES:+--no-scores}) ||
    return 1
  echo "반영 표: $TABLES"
}
step "① 스테이징" "$PY" -m compat stage --v3-db "$V3_DB" --out "$STAGING" &&
step "①' 반영 표" plan_tables &&
# shellcheck disable=SC2086  # reason: $FULL 은 있거나 없는 단일 플래그다
step "② compat export --in-place" "$PY" -m compat export --date "$D" --basis "$BASIS" \
    --equity-root "$EQUITY_ROOT" --stage-root "$STAGE_ROOT" --model-root "$MODEL_ROOT" \
    --target "$STAGING" --in-place --tables "$TABLES" $FULL ${CONS:+--consensus-asof "$CONS"} \
    ${BUILDS:+--builds-from "$BUILDS"} ${OLDER:+--allow-older} ${LEDGERS[@]+"${LEDGERS[@]}"} &&
step "③④ 게이트·반영" "$PY" -m compat apply --staging "$STAGING" --v3-db "$V3_DB" \
    --date "$D" --basis "$BASIS" --commit-flag "$CF" ${SHADOW:+--shadow} ${OLDER:+--allow-older} \
    ${NOSCORES:+--no-scores}
POST=""
if [ "$RC" -eq 0 ]; then
  if [ -n "$SHADOW" ]; then
    POST="그림자 — daily_post 부르지 않음"
  elif [ -z "$POST_CMD" ]; then
    POST="daily_post 명령 없음 — 건너뜀(기록만)"
  elif [ "${QL_V3_POST_TODAY:-$(date +%Y%m%d)}" != "$D" ]; then
    POST="daily_post 미호출 — 로컬 날짜 ${QL_V3_POST_TODAY:-$(date +%Y%m%d)} ≠ D $D(v3 잡이 date.today() 로 그날을 정한다)"
    RC=6
  else
    echo "──── [$(kst)] ⑤ daily_post: $POST_CMD"
    bash -c "$POST_CMD" 9>&-
    PRC=$?
    if [ "$PRC" -eq 0 ]; then POST="daily_post 완료"; else POST="daily_post 실패 rc=$PRC"; RC=6; fi
  fi
  echo "──── $POST"
fi
echo "════ 종료 rc=$RC $(kst) ════"
printf '%s\n%s\n%s\n' "$RC" "$STEP" "$POST" > "$RCF"
} 2>&1 | tee -a "$LOG"
RC=$(sed -n 1p "$RCF" 2>/dev/null); STEP=$(sed -n 2p "$RCF" 2>/dev/null); POST=$(sed -n 3p "$RCF" 2>/dev/null)
COMMITTED=""; [ -f "$CF" ] && COMMITTED=1
rm -f "$RCF" "$CF"
RC="${RC:-9}"
if [ "$RC" = 0 ] || [ "$RC" = 6 ]; then        # 이번 실행의 게이트 줄(로그 맨 끝 쪽)
  RB_NULL=$(grep -oE "rebase_null=[0-9]+" "$LOG" | tail -1 | cut -d= -f2)
  if [ "${RB_NULL:-0}" -gt 0 ]; then
    scripts/notify.sh warn "v3_post $D $BASIS 창 밖 adj_close NULL ${RB_NULL}행" \
      "창 안 사건 종목의 창 밖 행 중 equity 행이 없는 날(QL-E, P1) — 정지 아님, 대상 행 확인 | 로그 $LOG"
  fi
fi
SUMMARY=$(grep -E "^compat |compat 실패|Error|Traceback" "$LOG" | tail -6 | tr '\n' ' ' | cut -c1-900)
case "$RC" in
  0)
    if [ -n "$SHADOW" ]; then
      scripts/notify.sh info "v3_post 그림자 $D $BASIS 게이트 통과" \
        "v3 본 파일 무변경 · 스테이징 $STAGING | $SUMMARY | 로그 $LOG"
    else
      same_as_v3 || rm -f "$STAGING" "$STAGING-wal" "$STAGING-shm"
      scripts/notify.sh info "v3_post $D $BASIS 반영 완료" "$POST | $SUMMARY | 로그 $LOG"
    fi ;;
  6) scripts/notify.sh warn "v3_post $D $BASIS 반영 완료, $POST" \
       "반영 표는 COMMIT 됐다 — export 는 사람이 확인 | 로그 $LOG" ;;
  *)
    if [ -n "$COMMITTED" ]; then
      STATE="v3 본 파일은 COMMIT 됐다(그 뒤 단계 실패) — 반영 기록·로그를 사람이 확인"
    else
      STATE="v3 본 파일 무변경(COMMIT 전 실패) — v3 소비자가 옛 값을 읽는다"
    fi
    scripts/notify.sh crit "v3_post $D $BASIS 실패(${STEP:-?}) rc=$RC" \
      "$STATE | 스테이징 $STAGING 보존 | $SUMMARY | 로그 $LOG" ;;
esac
exit "$RC"
