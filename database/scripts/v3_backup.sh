#!/usr/bin/env bash
# v3 quant.db 고정 백업 2벌 — 컷오버 트랙 QL-I(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 · T-21, 절차서
#   `docs/CUTOVER_ROLLBACK.md` §1). 컷오버 날 V3-A~E(COMPAT_LAYER §8-1) **전에** 한 번 돈다. 서버 밖 백업은 하지
#   않는다(N-39 보류) — 서버 안 서로 다른 두 경로다. 규칙 정본은 `src/compat/v3_restore.py` 머리 주석.
#   두 경로 각각에 남기는 것(stamp = KST YYYYMMDDTHHMMSS, 전부 0444, SHA256SUMS 에 덧붙임 — `sha256sum -c` 형식):
#     quant_<stamp>.db                 v3 quant.db 온라인 백업(sqlite backup API — `compat stage` 와 같다) ·
#                                      integrity_check ok · rollback journal(읽기 전용으로 열어도 -wal/-shm 없음)
#     job_runner.py.bak.<stamp>        V3-A — `<v3 루트>/scripts/job_runner.py`(daily_post·daily_insight 체인을 더한다)
#     .kis_holidays.json.bak.<stamp>   V3-D·QL-Q 연결 — `<v3 루트>/data/.kis_holidays.json`(휴장 파일 쓰기 주체가 바뀐다)
#     kael_db.py.bak.<stamp>           V3-E — `<uni 루트>/sources/kael_db.py`(점수 조회 날짜 조건)
#     crontab.bak.<stamp>              V3-A·B·D — `crontab -l`(daily_insight 줄 · daily_all 줄 · 휴장 쓰기 2줄)
#   같은 이름이 이미 있으면 덮어쓰지 않고 멈춘다. 디스크 여유는 파일시스템마다 백업 크기 × 2 × 그 경로에 뜨는 벌 수.
#   실패하면 이번에 만든 파일을 지운다(SHA256SUMS 에 줄이 없는 파일은 복원이 받지 않는다).
#   락은 잡지 않는다 — 온라인 백업은 한 시점의 일관된 사본이고, v3 체인 락(`flock -n`)을 쥐면 그 시각 v3 체인이 조용히
#   건너뛰어진다. v3 체인이 돌지 않는 때 돌린다(절차서 §1).
#   사용: v3_backup.sh --v3-db PATH --dest DIR --dest DIR --v3-root DIR --uni-root DIR
#     경로는 전부 인자로만 받는다(저장소는 공개 — 서버 경로를 코드에 두지 않는다). 두 --dest 는 서로 다른 폴더.
#   rc: 0 완료 · 2 백업 실패(crontab 읽기 실패 포함 — 이번에 만든 파일은 지운다) · 4 홈 이동 실패 · 5 인자 오류
#   로그: logs/v3_backup/<stamp>.log
set -uo pipefail
cd "${QL_HOME:-$HOME/quant-ledger}" || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
QL_HOME="$(pwd)"
export QL_HOME PYTHONPATH="$QL_HOME/src"
PY=.venv/bin/python
V3_DB=""; V3_ROOT=""; UNI_ROOT=""; DESTS=()
die_arg() { echo "v3_backup 인자 오류: $1 — 아무것도 만들지 않았다" >&2; exit 5; }
need_val() { [ $# -ge 2 ] && [ -n "${2:-}" ] || die_arg "$1 에 값이 없다"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --v3-db) need_val "$@"; V3_DB="$2"; shift 2 ;;
    --dest) need_val "$@"; DESTS+=("$2"); shift 2 ;;
    --v3-root) need_val "$@"; V3_ROOT="$2"; shift 2 ;;
    --uni-root) need_val "$@"; UNI_ROOT="$2"; shift 2 ;;
    *) die_arg "모르는 인자 '$1'" ;;
  esac
done
[ -n "$V3_DB" ] || die_arg "--v3-db 가 없다"
[ -f "$V3_DB" ] || die_arg "--v3-db 파일이 없다: $V3_DB"
[ "${#DESTS[@]}" -eq 2 ] || die_arg "--dest 는 두 번(서로 다른 두 경로) 준다 — 받은 ${#DESTS[@]}개(T-21)"
[ -n "$V3_ROOT" ] || die_arg "--v3-root(v3 저장소 루트)가 없다"
[ -n "$UNI_ROOT" ] || die_arg "--uni-root(unitelegram 루트)가 없다"
# V3-A~E 가 바꿀 파일 — 목록 정본은 COMPAT_LAYER §8-1(crontab 은 아래에서 따로 뜬다)
FILES=("$V3_ROOT/scripts/job_runner.py" "$V3_ROOT/data/.kis_holidays.json" "$UNI_ROOT/sources/kael_db.py")
for f in "${FILES[@]}"; do
  [ -f "$f" ] || die_arg "사본 대상 파일이 없다: $f(COMPAT_LAYER §8-1 — 경로를 확인한다)"
done
STAMP=$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S)
mkdir -p logs/v3_backup
LOG="logs/v3_backup/${STAMP}.log"
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT
{
  echo "════ [$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] v3_backup 시작 stamp=$STAMP v3_db=$V3_DB dest=${DESTS[*]} ════"
  if ! crontab -l > "$TMPD/crontab"; then
    echo "crontab -l 실패 — V3-A·B·D 되돌리기에 쓸 crontab 사본을 뜰 수 없어 멈춘다(아무것도 만들지 않았다)"
    echo 2 > "$TMPD/rc"
  else
    "$PY" -m compat backup --v3-db "$V3_DB" --dest "${DESTS[0]}" --dest "${DESTS[1]}" --stamp "$STAMP" \
      --file "${FILES[0]}" --file "${FILES[1]}" --file "${FILES[2]}" --file "$TMPD/crontab"
    echo $? > "$TMPD/rc"
  fi
  echo "════ 종료 rc=$(cat "$TMPD/rc") ════"
} 2>&1 | tee -a "$LOG"
RC=$(cat "$TMPD/rc" 2>/dev/null)
[ "${RC:-9}" -eq 0 ] && exit 0
exit 2
