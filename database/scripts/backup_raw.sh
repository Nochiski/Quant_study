#!/usr/bin/env bash
# 원장 온라인 백업 — `data/raw` 의 6개 SQLite 를 `sqlite3 .backup` 으로 `~/backups/quant-ledger/<YYYYMMDD>/` 에 뜬다.
#   크론 03:30 KST 예정(v3 자기 DB 백업 03:00 뒤). 플랜 v1 §9 Task 6.2 / v2 §2-2 "백업" 행 · 페이즈 B B.3 ④.
#   · 원장 45 GB 라 rsync·하드링크·cp 금지 — 반드시 `.backup`(온라인). 쓰는 중에도 일관된 사본이 나온다.
#   · 원장 락(`/tmp/quant_ledger_raw.lock`)은 잡지 않는다. 백업 API 는 동시 쓰기에 안전하고, 락을 잡으면
#     45 GB 를 뜨는 동안 수집 체인이 통째로 막힌다. 03:30 은 어느 체인과도 겹치지 않는다.
#   · **DB 별 격리**: 하나가 실패해도 나머지는 끝까지 간다. 실패 목록을 모아 한 번에 crit.
#   · 사본마다 `PRAGMA integrity_check` 가 `ok` 여야 하고, WAL 잔재(`-wal`·`-shm`)는 지운다.
#   · 보관: 최근 7일 + **매월 1일 사본은 영구**(디렉터리 이름 끝 두 자리가 `01`).
#   · 디스크 여유가 60 GB 미만이면 뜨기 전에 crit 후 중단(사본 45 GB + 여유).
#   사용: backup_raw.sh [--date YYYYMMDD] [--dry-run]
#   환경: QL_BACKUP_ROOT (기본 $HOME/backups/quant-ledger)
set -uo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
BACKUP_ROOT="${QL_BACKUP_ROOT:-$HOME/backups/quant-ledger}"
MIN_FREE_GB=60
KEEP_DAYS=7
DBS="krx kiwoom kis dart wisereport daily_run"
DATE_ARG=""; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "usage: backup_raw.sh [--date YYYYMMDD] [--dry-run]" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"
LOG="logs/backup_raw_$(TZ=Asia/Seoul date +%Y%m%d).log"
mkdir -p logs
RUN=$(mktemp)
FAILED=""
OK_LIST=""
KEPT=0; PURGED=0
DEST="$BACKUP_ROOT/$D"
SHORT="${DEST/#$HOME/~}"   # 알림에 절대 경로를 싣지 않는다(.claude/rules/error-messages.md)
{
echo "════ [$(kst)] backup_raw 시작 D=$D dry=${DRY:-no} → $SHORT ════"
mkdir -p "$BACKUP_ROOT" || { echo "  백업 루트를 만들 수 없다: ${BACKUP_ROOT/#$HOME/~}"; FAILED="백업루트생성실패"; }
if [ -z "$FAILED" ]; then
  FREE_GB=$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')
  echo "  디스크 여유 ${FREE_GB} GB (하한 ${MIN_FREE_GB} GB)"
  if [ "${FREE_GB:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "  ! 여유 부족 — 백업을 시작하지 않는다(중간에 채우면 원장까지 위험하다)"
    FAILED="디스크 여유 ${FREE_GB} GB < ${MIN_FREE_GB} GB"
  fi
fi
if [ -z "$FAILED" ]; then
  [ -z "$DRY" ] && mkdir -p "$DEST"
  for name in $DBS; do
    SRC="data/raw/$name.db"
    DST="$DEST/$name.db"
    if [ ! -f "$SRC" ]; then
      echo "  $name: 원본 없음 ($SRC)"
      FAILED="$FAILED $name(원본없음)"
      continue
    fi
    if [ -n "$DRY" ]; then
      echo "  DRY: sqlite3 $SRC \".backup '$DST'\" ($(du -m "$SRC" | cut -f1) MB)"
      OK_LIST="$OK_LIST $name"
      continue
    fi
    echo "──── $name 시작 $(kst) ────"
    if ! sqlite3 "$SRC" ".backup '$DST'"; then
      echo "  ! $name .backup 실패 — 부분 사본을 지운다"
      rm -f "$DST" "$DST-wal" "$DST-shm"
      FAILED="$FAILED $name(backup)"
      continue
    fi
    CHK=$(sqlite3 "$DST" "PRAGMA integrity_check;" 2>&1 | head -1)
    if [ "$CHK" != "ok" ]; then
      echo "  ! $name integrity_check=$CHK — 사본을 지운다"
      rm -f "$DST" "$DST-wal" "$DST-shm"
      FAILED="$FAILED $name(integrity=$CHK)"
      continue
    fi
    # WAL 잔재 정리 — 사본을 DELETE 저널로 바꿔 -wal·-shm 없이 파일 하나로 남긴다(복원 시 혼선 제거)
    sqlite3 "$DST" "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;" >/dev/null 2>&1
    rm -f "$DST-wal" "$DST-shm"
    echo "  $name ok $(du -m "$DST" | cut -f1) MB $(kst)"
    OK_LIST="$OK_LIST $name"
  done
fi
# 보관 루프 — 최근 KEEP_DAYS 일 + 매월 1일은 영구. 이름이 YYYYMMDD 라 사전순 = 시간순.
# 오늘 사본이 온전할 때만 돈다. 실패한 날 옛 사본까지 지우면 백업이 한 번에 사라진다.
if [ -n "$FAILED" ]; then
  echo "──── 보관 정리 건너뜀 — 이번 백업이 실패했다($FAILED) ────"
else
  CUTOFF=$(date -d "$KEEP_DAYS days ago" +%Y%m%d 2>/dev/null || echo "00000000")
  echo "──── 보관 정리 기준 $CUTOFF 이상 유지 · 매월 1일 영구 ────"
  for dir in "$BACKUP_ROOT"/*/; do
    base=$(basename "$dir")
    case "$base" in
      20[0-9][0-9][01][0-9][0-3][0-9]) ;;
      *) continue ;;
    esac
    if [ "${base#??????}" = "01" ]; then KEPT=$((KEPT + 1)); continue; fi
    if [ ! "$base" \< "$CUTOFF" ]; then KEPT=$((KEPT + 1)); continue; fi
    echo "  보관 만료 삭제 $base ($(du -sh "$dir" 2>/dev/null | cut -f1))"
    [ -z "$DRY" ] && rm -rf "$dir"
    PURGED=$((PURGED + 1))
  done
fi
TOTAL=$([ -d "$DEST" ] && du -sh "$DEST" 2>/dev/null | cut -f1 || echo "-")
echo "════ 종료 $(kst) 성공[${OK_LIST:- 없음}] 실패[${FAILED:- 없음}] 사본 $TOTAL · 보관 $KEPT 유지 / $PURGED 삭제 ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUMMARY=$(grep -E "^  |^════ 종료" "$RUN" | tail -10 | tr '\n' ' ' | cut -c1-900)
if [ -n "$FAILED" ]; then
  [ -z "$DRY" ] && scripts/notify.sh crit "backup_raw 실패:$FAILED" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
[ -z "$DRY" ] && scripts/notify.sh info "backup_raw 완료 $D" "$SUMMARY"
rm -f "$RUN"
