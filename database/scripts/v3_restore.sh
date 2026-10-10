#!/usr/bin/env bash
# v3 quant.db 표 단위 복원 — 컷오버 트랙 QL-I(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 · §4, 절차서
#   `docs/CUTOVER_ROLLBACK.md` §3-3). `v3_backup.sh` 가 뜬 고정 백업에서 고른 표(기본 compat 9표)를 v3 quant.db 로
#   **한 트랜잭션**에 되돌린다 — 규칙 정본은 `src/compat/v3_restore.py` 머리 주석.
#   ① 백업 sha256 을 그 폴더 SHA256SUMS 와 먼저 대조(다르거나 줄이 없으면 멈춘다)
#   ② 표는 9표 안에서만(market_* 등 v3 가 계속 쓰는 표·_compat_meta 는 고를 수 없다), 표마다 열 이름이 백업과 같아야 한다
#   ③ ATTACH(백업 읽기 전용) · BEGIN IMMEDIATE · 표별 전체 DELETE · 백업 INSERT · `_compat_meta` 복원 기록 1행 · COMMIT
#      (`v3_post.sh` ④ 와 같은 원자성 — COMMIT 전에 실패하면 본 파일 무변경)
#   복원 기록 뒤 compat 반영은 다시 '첫 반영'이다 — 그 앞 반영 기록은 순서(T-35)·아침 7표(T-34) 판정에서 빠지고, 첫
#   제자리 반영은 --full 만 받는다(`src/compat/v3_post.py` 머리 주석).
#   --dry-run: ①② 뒤 표별 행 수(본 파일 → 백업)만 보이고 쓰지 않는다. 락도 잡지 않는다(v3 체인의 flock -n 을 막지 않게).
#   락(복원): v3 체인·v3_post.sh 와 같은 `/tmp/kael_v3_daily_all.lock`. 잡혀 있으면 풀릴 때까지 기다린다(시간 한도 없음 —
#     P9). 복원이 락을 쥔 동안 v3 crontab 의 `flock -n` 줄(daily_all)은 조용히 건너뛰어지므로 v3 체인 시각(20:05)을 피해
#     돌린다(절차서 §3-0).
#   사용: v3_restore.sh --backup FILE --v3-db FILE [--tables a,b] [--dry-run]
#   rc: 0 완료 · 2 복원 실패(COMMIT 전 — 본 파일 무변경) · 3 락 실패 · 4 홈 이동 실패 · 5 인자 오류
#   로그: logs/v3_restore/<KST YYYYMMDDTHHMMSS>.log
#   환경변수: QL_V3_LOCK_FILE 은 락 경로를 덮어쓴다 — 테스트·사본 리허설 전용(운영 v3 락을 잡지 않게). 운영 셸에 남기지 않는다.
set -uo pipefail
cd "${QL_HOME:-$HOME/quant-ledger}" || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
QL_HOME="$(pwd)"
export QL_HOME PYTHONPATH="$QL_HOME/src"
PY=.venv/bin/python
V3_LOCK="${QL_V3_LOCK_FILE:-/tmp/kael_v3_daily_all.lock}"
BACKUP=""; V3_DB=""; TABLES=""; DRY=""
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
die_arg() { echo "v3_restore 인자 오류: $1 — 실행하지 않았다(본 파일 무변경)" >&2; exit 5; }
need_val() { [ $# -ge 2 ] && [ -n "${2:-}" ] || die_arg "$1 에 값이 없다"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --backup) need_val "$@"; BACKUP="$2"; shift 2 ;;
    --v3-db) need_val "$@"; V3_DB="$2"; shift 2 ;;
    --tables) need_val "$@"; TABLES="$2"; shift 2 ;;
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
if [ -z "$DRY" ]; then
  exec 9>"$V3_LOCK"
  if ! flock -n 9; then
    echo "[$(kst)] v3 락 대기 시작 — $V3_LOCK 을 다른 실행이 쥐고 있다(풀리면 이어서 돈다)" | tee -a "$LOG"
    if ! flock 9; then
      echo "[$(kst)] v3_restore 락 실패 — $V3_LOCK 을 기다리다 flock 이 실패했다(본 파일 무변경)" | tee -a "$LOG" >&2
      exit 3
    fi
    echo "[$(kst)] v3 락 대기 끝" | tee -a "$LOG"
  fi
fi
RCF=$(mktemp)
{
  echo "════ [$(kst)] v3_restore 시작 backup=$BACKUP v3_db=$V3_DB tables=${TABLES:-9표 전부} $([ -n "$DRY" ] && echo dry-run) ════"
  "$PY" -m compat restore --backup "$BACKUP" --v3-db "$V3_DB" ${TABLES:+--tables "$TABLES"} ${DRY:+--dry-run}
  RC=$?
  echo "════ 종료 rc=$RC $(kst) ════"
  echo "$RC" > "$RCF"
} 2>&1 | tee -a "$LOG"
RC=$(cat "$RCF" 2>/dev/null)
rm -f "$RCF"
[ "${RC:-9}" -eq 0 ] && exit 0
exit 2
