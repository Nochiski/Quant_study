#!/usr/bin/env bash
# 격리 재생 실행기 — 서버 전용(컷오버 트랙 X-1). 운영 루트(~/quant-ledger)에는 쓰지 않는다.
#   읽기: 운영 stage(현판, 또는 --stage-at D 의 판) · 운영 equity 의 게이트 기준 파일(첫 패스에만 사본)
#   쓰기: --out 아래만 — data/{equity,factor_inputs,model} · stage_at/passN · logs/passN · config·scripts 링크
#   원형: 10-09 Q-4 병합 격리 확인 merge_check.sh(운영 해시 재현 equity 30/30 · fi 8/8)를 일반화했다.
#
# 사용:
#   replay.sh --out DIR --date YYYYMMDD [--basis morning|evening] [--code DIR] [--engine DIR]
#             [--steps equity,catalog,contract,fi,model] [--stage-at YYYYMMDD]
#   replay.sh --out DIR --compare OTHER [--date YYYYMMDD] [--basis morning|evening]
#
#   --out      출력 루트. 운영 홈 안이거나 그 조상이면 거부한다(실제 경로로 판정). 운영 data 와 같은
#              파일시스템이어야 한다 — equity 가 stage 입력을 `_pinned/` 에 하드링크로 고정한다(inputs.pin).
#              그래서 출력 루트는 운영 stage 파일의 링크를 쥔다 — 다 쓰면 지워 디스크를 돌려준다.
#   --code     src·scripts 가 있는 코드 루트(기본 운영 배포본). PYTHONPATH=<code>/src, 표 순서는
#              <code>/scripts/equity_order.txt. python 은 운영 venv 다(원형과 같다).
#   --date     판 기준일 D — fi·model 의 --date(그 단계가 있으면 필수)
#   --basis    morning(기본)|evening — equity·fi·model 의 --basis(build_chain 과 같다 — equity
#              dataset_profile 의 basis 열이 해시에 들어간다)와 --stage-at 이 읽는 인계 이력
#   --steps    고를 단계(쉼표). 적은 순서와 무관하게 equity → catalog → contract → fi → model 로 돈다.
#              빼면 그 단계는 앞 패스가 출력 루트에 남긴 판을 쓴다
#   --engine   contract 의 --engine-src(기본 <code>/_engine — 운영 체인과 같은 자리)
#   --stage-at 과거 D 의 stage 판으로 재생한다. 인계 이력 data/deliver/history/<D>_<basis>.json 의
#              stage_builds 를 운영 stage(keep 3판) 또는 equity `_pinned/`(이력 30일 보호)에서 찾아
#              <out>/stage_at/passN/ 에 판 디렉터리 심볼릭 링크 + 1판 MANIFEST 로 세운다(scripts/
#              replay_tool.py stage-at). 어디에도 없는 판은 MANIFEST 만 서서 그 표를 읽는 단계가 실패한다.
#   --compare  빌드 없이 --out 과 OTHER(다른 출력 루트 또는 운영 홈)의 표별 content_hash 를 대조한다
#              (읽기 전용). 운영 쪽 equity 는 인계 이력 D 가 가리키는 판, fi·model 은 `_runs/<D>_<basis>.json`.
#
# 기록: <out>/logs/passN/ — summary.tsv(step·table·rc·sec·build_id·content_hash·n_rows), summary.txt
#       (한 줄 요약, 표준 출력에도), run.txt(인자), 단계별 로그. 같은 --out 으로 다시 돌리면 N 이 늘고
#       같은 루트에 새 판을 짓는다(재현성 EG5a — 두 패스 summary.tsv 의 content_hash 열 비교).
# 실패: equity 표 하나가 실패하면 남은 표와 뒤 단계를 돌지 않는다(혼합 판 위에 짓지 않는다).
#       catalog 실패는 contract 만, fi 실패는 model 만 막는다. contract 는 기록형(build_chain step_soft).
# rc: 0 고른 단계 전부 성공(compare: 전부 같음) · 1 실패 단계 있음(compare: 다름·없음) · 2 인자·입력 오류
# 빌드 락은 잡지 않는다 — 잡으면 운영 체인(flock -n)이 그 회차를 건너뛴다. 운영 빌드 체인과 겹치지 않는
# 때에 돌린다(겹치면 메모리 경합, 기본 모드는 stage 현판이 도중에 바뀔 수 있다).
set -uo pipefail
OPS="$HOME/quant-ledger"
PY="$OPS/.venv/bin/python"
TOOL="$(cd "$(dirname "$0")" && pwd -P)/replay_tool.py"
ALL_STEPS="equity catalog contract fi model"

die() { echo "replay: $*" >&2; exit 2; }

OUT=""; CODE="$OPS"; D=""; BASIS="morning"; STEPS="equity,catalog,contract,fi,model"
ENGINE=""; STAGE_AT=""; COMPARE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out|--code|--date|--basis|--steps|--engine|--stage-at|--compare)
      [ $# -ge 2 ] || die "$1 에 값이 없다"
      case "$1" in
        --out) OUT="$2" ;;
        --code) CODE="$2" ;;
        --date) D="$2" ;;
        --basis) BASIS="$2" ;;
        --steps) STEPS="$2" ;;
        --engine) ENGINE="$2" ;;
        --stage-at) STAGE_AT="$2" ;;
        --compare) COMPARE="$2" ;;
      esac
      shift 2 ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done

# 없는 경로도 가장 가까운 있는 조상을 실제 경로(pwd -P)로 풀어 붙인다 — 심볼릭 링크로 운영 홈에 돌아
# 들어가는 것을 막는다. 아직 없는 부분에 . 이나 .. 가 있으면 풀 수 없으므로 실패한다.
abspath() {
  local p="$1" rest=""
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do
    rest="/$(basename "$p")$rest"
    p="$(dirname "$p")"
  done
  case "$rest/" in */./*|*/../*) return 1 ;; esac
  echo "$(cd "$p" && pwd -P)$rest"
}

[ -n "$OUT" ] || die "--out 이 필요하다"
case "$BASIS" in morning|evening) ;; *) die "--basis 는 morning|evening: $BASIS" ;; esac
for v in "$D" "$STAGE_AT"; do
  [ -z "$v" ] || [[ "$v" =~ ^[0-9]{8}$ ]] || die "날짜는 YYYYMMDD: $v"
done
R=$(abspath "$OUT") || die "--out 의 아직 없는 부분에 . 이나 .. 를 쓰지 않는다: $OUT"
OPS_ABS=$(abspath "$OPS") || die "운영 홈 경로를 풀 수 없다: $OPS"
case "$R/" in "$OPS_ABS/"*) die "--out 이 운영 홈 안이다: $R — 운영 루트에는 쓰지 않는다" ;; esac
case "$OPS_ABS/" in "$R/"*) die "--out 이 운영 홈의 조상이다: $R — 운영 루트를 품는 곳에는 쓰지 않는다" ;; esac
[ -x "$PY" ] || die "운영 venv python 이 없다: $PY"

if [ -n "$COMPARE" ]; then
  [ -d "$R/data" ] || die "대조할 출력 루트에 data/ 가 없다: $R"
  [ -d "$COMPARE/data" ] || die "대조 상대에 data/ 가 없다: $COMPARE"
  # shellcheck disable=SC2086  # reason: ${D:+…} 는 비었거나 "--date <D>" 두 낱말이다
  exec "$PY" "$TOOL" compare "$R" "$COMPARE" ${D:+--date "$D"} --basis "$BASIS"
fi

SEL=" "
# shellcheck disable=SC2086  # reason: 쉼표를 공백으로 바꾼 단계 목록이라 단어 분리가 의도다
for s in $(echo "$STEPS" | tr ',' ' '); do
  case " $ALL_STEPS " in
    *" $s "*) SEL="$SEL$s " ;;
    *) die "알 수 없는 단계: $s (고를 수 있는 것: $ALL_STEPS)" ;;
  esac
done
[ "$SEL" != " " ] || die "--steps 가 비었다"
has() { case "$SEL" in *" $1 "*) return 0 ;; esac; return 1; }
# shellcheck disable=SC2086  # reason: 앞뒤 공백을 걷어 사람이 읽는 목록으로 만든다
STEP_LIST=$(echo $SEL)

if { has fi || has model; } && [ -z "$D" ]; then die "fi·model 단계에는 --date 가 필요하다"; fi
[ -d "$CODE/src" ] || die "코드 루트에 src/ 가 없다: $CODE"
CODE=$(cd "$CODE" && pwd -P)
if has equity && [ ! -f "$CODE/scripts/equity_order.txt" ]; then
  die "표 순서 파일이 없다: $CODE/scripts/equity_order.txt"
fi
ENGINE="${ENGINE:-$CODE/_engine}"
if has contract && [ ! -d "$ENGINE" ]; then
  die "엔진 경로가 없다: $ENGINE — --engine 을 주거나 --steps 에서 contract 를 뺀다"
fi
[ -d "$OPS/data/stage" ] || die "운영 stage 가 없다: $OPS/data/stage"
HIST="$OPS/data/deliver/history/${STAGE_AT}_${BASIS}.json"
if [ -n "$STAGE_AT" ] && [ ! -f "$HIST" ]; then die "인계 이력이 없다: $HIST"; fi

mkdir -p "$R/data/equity" "$R/logs" || die "출력 루트를 만들 수 없다: $R"
PASS=$(( $(ls -d "$R"/logs/pass* 2>/dev/null | wc -l) + 1 ))
L="$R/logs/pass$PASS"
mkdir -p "$L" || die "로그 폴더를 만들 수 없다: $L"
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
T0=$(date +%s)
{
  echo "시작 $(kst)"
  echo "out=$R code=$CODE date=${D:--} basis=$BASIS steps=$STEP_LIST engine=$ENGINE stage_at=${STAGE_AT:--}"
} > "$L/run.txt"
echo "════ replay pass$PASS $(kst) — out=$R code=$CODE D=${D:--} basis=$BASIS steps=$STEP_LIST ════"

# 게이트 기준 파일은 첫 패스에만 운영에서 복사한다 — 표 폴더·_pinned·equity.duckdb 는 복사하지 않는다(원형과 같다).
# 다음 패스는 앞 패스가 남긴 기준으로 판정한다(운영이 그 사이 기준을 바꿔도 같은 루트 안 비교가 흔들리지 않게).
if [ "$PASS" -eq 1 ]; then
  EQ_OPS="$OPS/data/equity"
  for p in "$EQ_OPS/baseline.json" "$EQ_OPS"/baseline_seed_*.json "$EQ_OPS"/_seed_s0*.json \
           "$EQ_OPS/_catalog_meta.json" "$EQ_OPS/_contract_meta.json" "$EQ_OPS/_asof" "$EQ_OPS/fixtures"; do
    [ -e "$p" ] || continue
    cp -Rp "$p" "$R/data/equity/" || die "기준 파일 사본 실패: $p"
    echo "기준 파일 사본 ${p##*/}" >> "$L/run.txt"
  done
fi
# 코드 루트와 같은 모양으로 둔다(원형과 같다). 패스마다 그 패스의 --code 를 가리킨다.
ln -sfn "$CODE/config" "$R/config"
ln -sfn "$CODE/scripts" "$R/scripts"

STAGE="$OPS/data/stage"
STAGE_DESC="current"
if [ -n "$STAGE_AT" ]; then
  STAGE="$R/stage_at/pass$PASS"
  STAGE_DESC="at:$STAGE_AT"
  msg=$("$PY" "$TOOL" stage-at --ops-data "$OPS/data" --date "$STAGE_AT" --basis "$BASIS" \
          --dest "$STAGE" 2>&1 > "$L/stage_at.tsv")
  rc=$?
  echo "$msg" | tee -a "$L/run.txt"
  [ "$rc" -eq 0 ] || die "임시 stage 루트를 세우지 못했다(rc=$rc) — $L/stage_at.tsv"
fi

export QL_HOME="$R" PYTHONPATH="$CODE/src" PYTHONDONTWRITEBYTECODE=1
cd "$R" || die "출력 루트로 이동 실패: $R"

SUM="$L/summary.tsv"
printf 'step\ttable\trc\tsec\tbuild_id\tcontent_hash\tn_rows\n' > "$SUM"
FAILED=""
BRIEF=""
step() {
  # step <이름> <로그> <명령…> — 한 줄을 summary.tsv 에 남기고 rc 를 돌려준다
  local name="$1" log="$2" t1 rc sec; shift 2
  t1=$(date +%s)
  nice -n 10 "$@" > "$log" 2>&1
  rc=$?
  sec=$(( $(date +%s) - t1 ))
  printf '%s\t-\t%s\t%s\t-\t-\t-\n' "$name" "$rc" "$sec" >> "$SUM"
  echo "  $name rc=$rc ${sec}s"
  if [ "$rc" -eq 0 ]; then BRIEF="$BRIEF · $name ok"; else
    FAILED="$FAILED $name(rc=$rc)"; BRIEF="$BRIEF · $name rc=$rc"; fi
  return "$rc"
}
skip() {
  printf '%s\t-\tskip\t-\t-\t-\t-\n' "$1" >> "$SUM"
  echo "  $1 건너뜀 — $2"
  BRIEF="$BRIEF · $1 skip"
}
layer_rows() {
  # 표별 해시를 summary.tsv 에 붙인다 — layer_rows <단계> <층> [rows 인자…]
  local name="$1" layer="$2"; shift 2
  "$PY" "$TOOL" rows --data "$R/data" --layer "$layer" "$@" \
    | awk -F'\t' -v s="$name" 'BEGIN { OFS = "\t" } { print s, $1, 0, "-", $2, $3, $4 }' >> "$SUM"
}

EQ_OK=1      # equity 층을 읽어도 되는가(이번 패스 equity 가 실패하면 혼합 판이다)
if has equity; then
  ORDER=$(grep -vE '^\s*(#|$)' "$CODE/scripts/equity_order.txt")
  n_all=0; n_ok=0
  for t in $ORDER; do
    n_all=$((n_all + 1))
    if [ "$EQ_OK" -ne 1 ]; then
      printf 'equity\t%s\tskip\t-\t-\t-\t-\n' "$t" >> "$SUM"
      continue
    fi
    t1=$(date +%s)
    nice -n 10 "$PY" -m equity --root "$R/data/equity" --stage-root "$STAGE" build "$t" \
      --basis "$BASIS" --threads 3 --memory-limit 8GB --keep 3 > "$L/eq_$t.log" 2>&1
    rc=$?
    sec=$(( $(date +%s) - t1 ))
    if [ "$rc" -eq 0 ]; then
      row=$("$PY" "$TOOL" rows --data "$R/data" --layer equity --table "$t" | cut -f2-)
      n_ok=$((n_ok + 1))
    else
      row=$(printf -- '-\t-\t-')
      EQ_OK=0
      FAILED="$FAILED equity:$t(rc=$rc)"
    fi
    printf 'equity\t%s\t%s\t%s\t%s\n' "$t" "$rc" "$sec" "$row" >> "$SUM"
    echo "  equity $t rc=$rc ${sec}s $row"
  done
  BRIEF="$BRIEF · equity $n_ok/$n_all"
fi

CAT_OK=1
if has catalog; then
  if [ "$EQ_OK" -eq 1 ]; then
    step catalog "$L/catalog.log" "$PY" -m equity --root "$R/data/equity" --stage-root "$STAGE" \
      catalog || CAT_OK=0
  else
    skip catalog "equity 실패"; CAT_OK=0
  fi
fi
if has contract; then
  if [ "$EQ_OK" -eq 1 ] && [ "$CAT_OK" -eq 1 ]; then
    step contract "$L/contract.log" "$PY" -m equity --root "$R/data/equity" --stage-root "$STAGE" \
      contract --engine-src "$ENGINE"
  else
    skip contract "equity 또는 catalog 실패"
  fi
fi
FI_OK=1
if has fi; then
  if [ "$EQ_OK" -eq 1 ]; then
    if step fi "$L/fi.log" "$PY" -m factor_inputs build --date "$D" --basis "$BASIS" \
         --root "$R/data/factor_inputs" --stage-root "$STAGE" --equity-root "$R/data/equity"; then
      layer_rows fi fi --date "$D" --basis "$BASIS"
    else
      FI_OK=0
    fi
  else
    skip fi "equity 실패"; FI_OK=0
  fi
fi
if has model; then
  # model 은 --fi-build latest 다 — fi 가 실패한 패스에서 돌면 앞 패스의 같은 D fi 판을 조용히 읽는다
  if [ "$EQ_OK" -eq 1 ] && [ "$FI_OK" -eq 1 ]; then
    if step model "$L/model.log" "$PY" -m model build --date "$D" --basis "$BASIS" \
         --fi-build latest --specs all --root "$R/data/model" --fi-root "$R/data/factor_inputs"; then
      layer_rows model model --date "$D" --basis "$BASIS"
    fi
  else
    skip model "equity 또는 fi 실패"
  fi
fi

LINE="replay pass$PASS D=${D:--} basis=$BASIS stage=$STAGE_DESC code=$CODE —${BRIEF#" ·"}"
LINE="$LINE · 실패 ${FAILED:-없음} · $(( $(date +%s) - T0 ))s · 크기 $(du -sh "$R" 2>/dev/null | cut -f1)(stage 하드링크 포함)"
echo "$LINE" > "$L/summary.txt"
echo "$LINE"
echo "기록 $L/summary.tsv"
[ -z "$FAILED" ] || exit 1
exit 0
