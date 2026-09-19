#!/usr/bin/env bash
# equity 29표 전량 재빌드(S24 coverage_daily 포함, 2026-09-11) — 의존 순서대로 1회 지으며 표별 content_hash 를 기록한다.
# 서버 전용(경로가 ~/quant-ledger 고정). 배포: scp 로 서버 scripts/ 에 두고 실행.
# 사용: equity_rebuild_all.sh <pass-label> [--basis evening|morning]   (예: pass1 / pass2)
#   --basis 는 각 표의 `equity build` 에 그대로 넘어간다 — 저녁 잠정판 e_ / 아침 확정판 m_
#   (플랜 v2 §4 B.2). 생략하면 기본 판(manual, build_id 접두 b_).
#   표별 보관 판 수는 QL_EQUITY_KEEP(기본 10 = 5거래일×2판, 판당 ≈1.2 GB). 30일 추적은 equity 판이 아니라
#   `_pinned/` 의 stage 입력 판 보호(build_chain.sh gc_step, 인계 이력 기준)로 한다 — 같은 입력이면 재빌드가 같다(EG5a).
# 재현성 검사 = 두 pass 의 logs/equity/rebuild_<pass>/summary.tsv content_hash 열 비교
#   (EG5a 는 전량 재빌드에서 inputs_changed 로 빠진다 — EQUITY_HANDOFF.md §7 ⑦)
set -uo pipefail
export LC_ALL=C
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
cd "$QL_HOME"
# 2026-09-09 (플랜 P0 Task 0.2): 락은 여기서 한 번만 잡고 자식 run_equity.sh 는 QL_BUILD_LOCK_HELD=1 로 생략한다.
LOCK=/tmp/quant_ledger_build.lock
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  flock -n 9 || { echo "another build is running — lock $LOCK"; exit 3; }
  export QL_BUILD_LOCK_HELD=1
fi
PASS="${1:?pass label required}"; shift || true
BASIS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --basis) BASIS="${2:?--basis needs a value}"; shift 2 ;;
    --basis=*) BASIS="${1#--basis=}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
BASIS_ARGS=""
if [ -n "$BASIS" ]; then BASIS_ARGS="--basis $BASIS"; fi
OUT="logs/equity/rebuild_${PASS}"
mkdir -p "$OUT"
SUM="$OUT/summary.tsv"
: > "$SUM"
: > "$OUT/STATUS"

ORDER=$(grep -vE '^\s*(#|$)' "$QL_HOME/scripts/equity_order.txt" | tr '\n' ' ')
# 정본은 scripts/equity_order.txt 하나다 — 빌드와 재판정이 같은 29표를 돈다(DEFECT-C09).
[ -n "${ORDER// /}" ] || { echo "!!! scripts/equity_order.txt 가 비었거나 없다" >&2; exit 2; }

T_ALL0=$(date +%s)
ANY_FAIL=""
for t in $ORDER; do
  T0=$(date +%s)
  echo "=== [$PASS] build $t  basis=${BASIS:-manual}  $(date -u +%FT%TZ)"
  # shellcheck disable=SC2086  # reason: BASIS_ARGS 는 비었거나 "--basis <값>" 두 낱말이다
  scripts/run_equity.sh "$t" --threads 3 --memory-limit 8GB --keep "${QL_EQUITY_KEEP:-10}" $BASIS_ARGS > "$OUT/$t.log" 2>&1
  RC=$?
  T1=$(date +%s)
  H="-"
  [ "$RC" -eq 0 ] && H=$(.venv/bin/python scripts/equity_manifest_row.py "$t")   # 실패한 표는 MANIFEST 가 없을 수 있다
  printf '%s\t%s\t%s\t%s\n' "$t" "$RC" "$((T1-T0))" "$H" >> "$SUM"
  echo "    rc=$RC  $((T1-T0))s  $H"
  if [ "$RC" -ne 0 ]; then
    echo "FAILED $t" >> "$OUT/STATUS"
    if [ -n "${QL_EQUITY_CONTINUE:-}" ]; then
      # 진단 모드(2026-09-13): 첫 실패에서 멈추지 않고 끝까지 돌아 실패 표를 한 번에 모은다. 하류 표는
      # 상류 실패 판(이전 판)을 입력으로 쓰므로 결과 판은 운영에 쓰지 않는다 — summary.tsv 만 읽는다.
      echo "!!! FAILED $t (rc=$RC) — QL_EQUITY_CONTINUE 라 계속"; ANY_FAIL=1; continue
    fi
    echo "!!! FAILED $t (rc=$RC) — 중단"; exit "$RC"
  fi
done
T_ALL1=$(date +%s)
echo "TOTAL $((T_ALL1-T_ALL0))s" | tee -a "$OUT/STATUS"
# 마지막 명령이 `[ -n ] && {…}` 이면 실패 표가 없을 때 `[` 의 rc 1 이 스크립트 종료 코드가 된다 —
# 09-17 00:33 진단 실측: 29표 전부 rc 0 인데 종료 rc 1(09-13 7a885f4 이후 성공 경로가 한 번도 안 돌았다).
if [ -n "$ANY_FAIL" ]; then
  echo "!!! 진단 모드 — 실패 표: $(grep FAILED "$OUT/STATUS" | tr '\n' ' ')"
  exit 1
fi
exit 0
