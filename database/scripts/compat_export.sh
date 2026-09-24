#!/usr/bin/env bash
# v3 quant.db 호환 계층 export — 플랜 `docs/plans/2026-09-24-v3-merge.md` T1.3 2.
#   equity/stage 판을 읽어 v3 `quant.db` 9표 중 우리가 채우는 표를 upsert 한다(읽기 전용 소비).
#   M1~M3 대상은 별도 파일 `data/compat/quant.db`(기본값), M4 컷오버부터 QL_COMPAT_TARGET 으로
#   v3 `~/kael-system-v3/data/quant.db` 제자리(결정 D-2).
#
#   락: 자체 락 `/tmp/quant_ledger_compat.lock` — 빌드 락(`/tmp/quant_ledger_build.lock`)은 잡지
#       않는다. equity 판을 **읽기만** 하고 MANIFEST current_build 로 판을 고정해 읽으므로
#       빌드와 동시에 돌아도 안전하다. 같은 대상 sqlite 에 두 개가 동시에 쓰는 것만 막는다.
#   rc: 0 완료(info) · 2 export 실패(crit) · 3 락 실패(warn) · 4 홈 이동 실패
#       어느 경로도 알림 없이 끝나지 않는다(결정 V2-7). --dry-run 만 예외.
#   사용: compat_export.sh --date YYYYMMDD --basis evening|morning
#                          [--target PATH] [--full] [--dry-run] [--consensus-asof YYYYMMDD]
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패" >&2; exit 4; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
LOCK=/tmp/quant_ledger_compat.lock
EQUITY_ROOT="${QL_EQUITY_ROOT:-data/equity}"
STAGE_ROOT="${QL_STAGE_ROOT:-data/stage}"
TARGET="${QL_COMPAT_TARGET:-data/compat/quant.db}"
DATE_ARG=""; BASIS=""; FULL=""; DRY=""; CONS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --basis) BASIS="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --consensus-asof) CONS="$2"; shift 2 ;;
    --full) FULL="--full"; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
D="${DATE_ARG:-$(TZ=Asia/Seoul date +%Y%m%d)}"
case "$BASIS" in
  evening|morning) ;;
  *) echo "--basis 는 evening|morning 이어야 한다: '${BASIS}'" >&2; exit 2 ;;
esac
mkdir -p logs/compat
LOG="logs/compat/${D}_${BASIS}.log"
exec 9>"$LOCK"
if ! flock -n 9; then
  [ -z "$DRY" ] && scripts/notify.sh warn "compat_export 락 실패" \
    "다른 compat 실행이 $LOCK 을 쥐고 있다 — 이번 실행 건너뜀"
  exit 3
fi
# 파이프(`| tee`) 안의 RC 는 서브셸에 갇힌다 — 파일로 꺼낸다(wics_weekly 09-20 자체 검수 교훈)
RCF=$(mktemp)
{
echo "════ [$(kst)] compat_export 시작 date=$D basis=$BASIS target=$TARGET full=${FULL:-no} ════"
if [ -n "$DRY" ]; then
  echo "  dry-run — 실행할 명령:"
  echo "  $PY -m compat export --date $D --basis $BASIS --equity-root $EQUITY_ROOT" \
       "--stage-root $STAGE_ROOT --target $TARGET $FULL ${CONS:+--consensus-asof $CONS}"
  RC=0
else
  # shellcheck disable=SC2086  # reason: $FULL 은 있거나 없는 단일 플래그다
  $PY -m compat export --date "$D" --basis "$BASIS" --equity-root "$EQUITY_ROOT" \
      --stage-root "$STAGE_ROOT" --target "$TARGET" $FULL ${CONS:+--consensus-asof "$CONS"}
  RC=$?
  echo "──── compat export 종료 rc=$RC $(kst) ────"
fi
echo "════ 종료 rc=$RC $(kst) ════"
echo "$RC" > "$RCF"
} 2>&1 | tee -a "$LOG"
RC=$(cat "$RCF" 2>/dev/null || echo 9); rm -f "$RCF"
SUMMARY=$(grep -E "^compat |compat 실패|Error|Traceback" "$LOG" | tail -4 | tr '\n' ' ' | cut -c1-900)
if [ -n "$DRY" ]; then exit "$RC"; fi
case "$RC" in
  0) scripts/notify.sh info "compat export $D $BASIS 완료" "$SUMMARY | 로그 $LOG" ;;
  *) scripts/notify.sh crit "compat export $D $BASIS 실패 rc=$RC" \
       "v3 후단이 옛 값을 읽는다 — 원천 판·as-of·스키마 확인 | $SUMMARY | 로그 $LOG" ;;
esac
exit "$RC"
