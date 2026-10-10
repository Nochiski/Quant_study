#!/usr/bin/env bash
# v3 quant.db 표 단위 복원 — 컷오버 트랙 QL-I(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 · T-42 · §4, 절차서
#   `docs/CUTOVER_ROLLBACK.md` 4-2). `v3_backup.sh` 가 뜬 고정 백업에서 고른 표를 v3 quant.db 로 **한 트랜잭션**에
#   되돌린다 — 규칙 정본은 `src/compat/v3_restore.py` 머리 주석.
#   표: 기본은 점수 두 표를 뺀 7표다(T-42 — 점수 두 표는 컷오버 기간에 실제로 엑셀로 나간 점수라 남긴다). 되돌리는 이유가
#     점수 오류일 때만 --with-scores 로 9표(점수 표를 --tables 로 고를 때도 --with-scores 가 있어야 한다). market_* 등 v3 가
#     계속 쓰는 표와 _compat_meta 는 고를 수 없다.
#   ① 락을 잡기 **전에** 검증: 백업 sha256 을 그 폴더 SHA256SUMS 와 대조(다르거나 줄이 없으면 멈춘다) · 표 · 열 이름 ·
#      표별 행 수(본 파일 → 백업). 본 파일은 읽기 전용으로 연다. --dry-run 이면 여기서 끝(락 없음, 쓰지 않음)
#   ② v3 락을 잡고(대기형) 복원: sha 를 다시 대조한 뒤 ATTACH(백업 읽기 전용) · BEGIN IMMEDIATE · 표별 전체 DELETE ·
#      백업 INSERT · `_compat_meta` 복원 기록 1행 · COMMIT(`v3_post.sh` ④ 와 같은 원자성 — COMMIT 전에 실패하면 본 파일
#      무변경)
#   복원 기록 뒤 compat 반영은 다시 '첫 반영'이다 — 그 앞 반영 기록은 순서(T-35)·아침 7표(T-34) 판정에서 빠지고, 첫
#   제자리 반영은 복원 뒤에 계산된 기록만 받는다(증분 창이어도 된다 — `src/compat/v3_post.py` 머리 주석, T-42 · T-46).
#   락: v3 체인·v3_post.sh 와 같은 `/tmp/kael_v3_daily_all.lock`. 잡혀 있으면 풀릴 때까지 기다린다(시간 한도 없음 — P9).
#     절차서 순서(4-1 장 마감 체인 그림자 → 4-2 복원 → 4-3 v3 크론 복구)에서는 V3-B 로 daily_all 이 없고 제자리 반영이
#     꺼져 있어 쥘 주체가 없다. 잡혀 있으면 무엇이 쥐었는지 먼저 본다(절차서 4-2). 복원이 락을 쥔 동안 v3 crontab 의
#     `flock -n` 줄은 조용히 건너뛰어진다.
#   사용: v3_restore.sh --backup FILE --v3-db FILE [--tables a,b] [--with-scores] [--dry-run]
#   rc: 0 완료 · 2 검증·복원 실패(COMMIT 전 — 본 파일 무변경) · 3 락 실패 · 4 홈 이동 실패 · 5 인자 오류
#   로그: logs/v3_restore/<KST YYYYMMDDTHHMMSS>[_dry].log
#   환경변수: QL_V3_LOCK_FILE 은 락 경로를 덮어쓴다 — 테스트·사본 리허설 전용(운영 v3 락을 잡지 않게). 운영 셸에 남기지 않는다.
set -uo pipefail
cd "${QL_HOME:-$HOME/quant-ledger}" || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
QL_HOME="$(pwd)"
export QL_HOME PYTHONPATH="$QL_HOME/src"
PY=.venv/bin/python
V3_LOCK="${QL_V3_LOCK_FILE:-/tmp/kael_v3_daily_all.lock}"
BACKUP=""; V3_DB=""; TABLES=""; DRY=""; SCORES=""
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
die_arg() { echo "v3_restore 인자 오류: $1 — 실행하지 않았다(본 파일 무변경)" >&2; exit 5; }
need_val() { [ $# -ge 2 ] && [ -n "${2:-}" ] || die_arg "$1 에 값이 없다"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --backup) need_val "$@"; BACKUP="$2"; shift 2 ;;
    --v3-db) need_val "$@"; V3_DB="$2"; shift 2 ;;
    --tables) need_val "$@"; TABLES="$2"; shift 2 ;;
    --with-scores) SCORES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) die_arg "모르는 인자 '$1'" ;;
  esac
done
[ -n "$BACKUP" ] || die_arg "--backup 이 없다"
[ -f "$BACKUP" ] || die_arg "--backup 파일이 없다: $BACKUP"
[ -n "$V3_DB" ] || die_arg "--v3-db 가 없다"
[ -f "$V3_DB" ] || die_arg "--v3-db 파일이 없다: $V3_DB"
mkdir -p logs/v3_restore
LOG="logs/v3_restore/$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S)$([ -n "$DRY" ] && echo _dry).log"
ARGS=(--backup "$BACKUP" --v3-db "$V3_DB")
[ -n "$TABLES" ] && ARGS+=(--tables "$TABLES")
[ -n "$SCORES" ] && ARGS+=(--with-scores)
# compat restore 한 번 — 출력은 로그로, rc 는 파일로 꺼낸다(파이프 안 RC 는 서브셸에 갇힌다)
run() {
  local rcf rc
  rcf=$(mktemp)
  { "$PY" -m compat restore "${ARGS[@]}" "$@"; echo $? > "$rcf"; } 2>&1 | tee -a "$LOG"
  rc=$(cat "$rcf" 2>/dev/null); rm -f "$rcf"
  return "${rc:-9}"
}
echo "════ [$(kst)] v3_restore 시작 backup=$BACKUP v3_db=$V3_DB tables=${TABLES:-$([ -n "$SCORES" ] && echo 9표 || echo '7표(점수 제외)')} $([ -n "$DRY" ] && echo dry-run) ════" | tee -a "$LOG"
echo "──── [$(kst)] ① 검증(락 전 — sha · 표 · 열 · 행 수, 읽기만)" | tee -a "$LOG"
if ! run --dry-run; then
  echo "════ 종료 rc=2 — 검증 실패(락을 잡지 않았다, 본 파일 무변경) $(kst) ════" | tee -a "$LOG"
  exit 2
fi
if [ -n "$DRY" ]; then
  echo "════ 종료 rc=0 — dry-run(락 없음, 쓰지 않았다) $(kst) ════" | tee -a "$LOG"
  exit 0
fi
exec 9>"$V3_LOCK"
if ! flock -n 9; then
  echo "[$(kst)] v3 락 대기 시작 — $V3_LOCK 을 다른 실행이 쥐고 있다(풀리면 이어서 돈다). 무엇이 쥐었는지 본다: pgrep -af 'kael_v3_daily_all.lock|v3_post.sh|job_runner.py'" | tee -a "$LOG"
  if ! flock 9; then
    echo "[$(kst)] v3_restore 락 실패 — $V3_LOCK 을 기다리다 flock 이 실패했다(본 파일 무변경)" | tee -a "$LOG" >&2
    exit 3
  fi
  echo "[$(kst)] v3 락 대기 끝" | tee -a "$LOG"
fi
echo "──── [$(kst)] ② 복원(v3 락 안 — sha 재대조 · 한 트랜잭션)" | tee -a "$LOG"
if run; then RC=0; else RC=2; fi
echo "════ 종료 rc=$RC $(kst) ════" | tee -a "$LOG"
exit "$RC"
