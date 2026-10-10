#!/usr/bin/env bash
# 격리 재생 실행기 — 서버 전용(컷오버 트랙 X-1). 운영 루트(~/quant-ledger)에는 쓰지 않는다.
#   읽기: 운영 stage(현판, 또는 --stage-at D 의 판) · 운영 equity 의 게이트 기준 파일(첫 패스에만 사본)
#         · 장 마감 판 재생은 판정 달력 data/calendar · 21:05 키움 원장 data/raw/kiwoom.db(읽기 전용 URI)도
#   쓰기: --out 아래만 — data/{equity,factor_inputs,model} · stage_at/passN · logs/passN
#         · 장 마감 판 재생은 data/{model_db,deliver/history,postclose_replay/passN} · stage_pin/passN 도.
#         출력 루트에는 심볼릭 링크를 만들지 않는다(불변식). 그래서 링크가 있으면 손으로 둔 것으로 보고 거부한다.
#   원형: 10-09 Q-4 병합 격리 확인 merge_check.sh(운영 해시 재현 equity 30/30 · fi 8/8)를 일반화했다.
#
# 사용:
#   replay.sh --out DIR --date YYYYMMDD [--basis morning] [--code DIR] [--engine DIR]
#             [--steps equity,catalog,contract,fi,model] [--stage-at YYYYMMDD]
#   replay.sh --out DIR --basis evening (--date T | --dates T1-T2) [--code DIR] [--steps equity,board]
#             [--spearman-min X]
#   replay.sh --out DIR --compare OTHER [--date YYYYMMDD] [--basis morning|evening]
#
#   --out      출력 루트. 운영 홈이나 운영 data 의 실제 경로 안이거나 그 조상이면 거부한다. 아직 없는
#              부분에 . · .. · 매달린 링크가 있어도 거부한다. data·logs·stage_at·stage_pin 아래에 링크가 있거나
#              운영 data 와 같은 디렉터리(-ef)여도 거부한다. 운영 data 와 같은 파일시스템이어야 한다 —
#              equity 가 stage 입력을 `_pinned/` 에 하드링크로 고정한다(inputs.pin). 그래서 출력 루트는
#              운영 stage 파일의 하드링크를 쥔다. 끝에 출력하는 `rm -rf -- "<out>"` 로 지워 디스크를
#              돌려준다(링크가 없으니 운영 파일에는 닿지 않는다).
#   --code     src·scripts 가 있는 코드 루트(기본 운영 배포본). PYTHONPATH=<code>/src, 표 순서는
#              <code>/scripts/equity_order.txt. python 은 운영 venv 다(원형과 같다).
#   --date     판 기준일 D — fi·model 의 --date(그 단계가 있으면 필수)
#   --basis    morning(기본) — equity·fi·model 의 --basis(build_chain 과 같다 — equity dataset_profile 의
#              basis 열이 해시에 들어간다)와 --stage-at 이 읽는 인계 이력. evening 은 아래 '장 마감 판 재생'
#              (옛 21:20 연구 저녁 판 재생이 아니다 — 그 판은 그림자 시작 때 크론에서 빠진다, N-42 Q4)
#   --steps    고를 단계(쉼표). 적은 순서와 무관하게 equity → catalog → contract → fi → model 로 돈다.
#              빼면 그 단계는 앞 패스가 출력 루트에 남긴 판을 쓴다
#   --engine   contract 의 --engine-src(기본 <code>/_engine — 운영 체인과 같은 자리)
#   --stage-at 과거 D 의 stage 판으로 재생한다. 인계 이력 data/deliver/history/<D>_<basis>.json 의
#              stage_builds 를 운영 stage(keep 3판) 또는 equity `_pinned/`(이력 30일 보호)에서 찾아
#              <out>/stage_at/passN/ 에 파티션 파일 하드링크 + 1판 MANIFEST 로 세운다(scripts/
#              replay_tool.py stage-at). 어디에도 없는 판은 MANIFEST 만 서서 그 표를 읽는 단계가 실패한다.
#              한계: fi 가 stage 에서 직접 읽는 4표(stg_consensus_annual·stg_consensus_matrix·stg_fin_wise·
#              stg_fin_wise_q)는 equity 가 고정하지 않아 `_pinned/` 에 없다. D 가 운영 stage keep(표당
#              3판 ≈ 1.5거래일) 밖이면 이 4표는 '없음'이고 fi 는 실패한다. equity 만 다시 짓는 재생은 30일
#              안이면 된다. 과거 D 의 fi·model 재생(P5)은 이 기능이 아니라 '현판 + D 시점 자르기(PIT)'로
#              한다 — 장 마감 판은 PR-4 가 D' 시점으로 자르고, 연구 fi 는 D6-7 처럼 현 equity 로 과거 D 를
#              짓는다. 그래서 이 한계를 우회하는 기능은 두지 않는다.
#   --compare  빌드 없이 --out 과 OTHER(다른 출력 루트 또는 운영 홈)의 표별 content_hash 를 대조한다
#              (읽기 전용). 운영 쪽(data/deliver 가 있는 루트) equity 는 인계 이력 D 가 가리키는 판이고,
#              그 이력이 없으면 rc 2. fi·model 은 `_runs/<D>_<basis>.json` 이고, 없거나 ok 가 아니면 '없음'.
#              model 은 scores 와 indicators 해시를 둘 다 대조한다. 운영 equity keep(10판) 밖으로 밀린
#              D 의 판은 '없음'으로 나온다.
#
# 장 마감 판 재생(--basis evening, 컷오버 PR-8b — P5 '장 마감 판 60거래일 재생 대 연구 판'의 실행기):
#   과거 T 의 장 마감 판과 다음 날 연구 판 T 를 다시 지어 두 판 대조(daily.board_compare --replay)까지 돈다.
#   그날 판(stage keep 3 · equity keep 10)은 GC 로 사라졌으므로 위 원칙대로 '현판 + D 시점 자르기'다:
#   equity 는 운영 stage 현판으로 한 번 짓고(--basis morning — fi 는 m_·b_ equity 판만 읽는다), fi 는 재생 전용
#   --replay 로 equity 세션 축 표를 asof(연구 판 D · 장 마감 판 D')에서 잘라 그날 판 모양으로 짓는다(MD-SEAM 이
#   선다). fi 의 queries SQL·게이트·진입 조건, 모델, 대조는 운영 체인(postclose_chain.sh)과 같은 명령·같은 코드다.
#   --dates T1-T2  [T1, T2] 의 거래일 T 마다(판정 달력 운영 data/calendar) 차례로 — --date T 는 T 하루
#   --steps        equity(출력 루트에 equity 현판을 짓는다) · board(날짜별 ①~⑥). 기본 equity,board. board 만
#                  주면 앞 패스가 지은 equity 판을 쓴다(그 패스 equity 표가 전부 rc 0 이어야 한다 — 아니면 rc 2)
#   --spearman-min 대조의 Spearman 하한(기본 board_compare 기본값). P5 기록형 측정은 0(T-36)
#   한 패스: equity → 고정 stage(fi 가 stage 에서 직접 읽는 표의 운영 현판을 <out>/stage_pin/passN 에 하드링크,
#     replay_tool.py stage-pin) → 날짜마다 합성 인계 이력 <out>/data/deliver/history/<D>_morning.json(이 패스
#     equity 현판 + 고정 stage 판, health ok, 재생 표시 replay — replay_tool.py handoff) → 연구 판 D'(fi 아침판)
#     → T 마다 ② 재생 T 행 원천: 21:05 키움 원장(운영 data/raw/kiwoom.db ka10060 dt=T, 읽기 전용)에서 수집기 대상
#     (① 연구 판 D' 후보 → ② v3 유니버스 나머지)만 <out>/data/postclose_replay/passN/<T>/postclose.db 에 수집기와
#     같은 함수로(price_valid='1', 재생 표시 replay_source 표 — scripts/replay_evening.py) → stage 단독 빌드
#     (<out>/data/model_db/{stage,snapshots}) ③ fi 장 마감 판(--builds-from <D'>_morning.json --replay,
#     --postclose-stage-root·--candidates-root 출력 루트) ④ 모델 장 마감 판 ⑤ 연구 판 T(fi 아침판 --replay + 모델)
#     ⑥ 두 판 대조(daily.board_compare --replay → <out>/data/model_db/compare/<T>.json). 앞 단계가 실패하면 그 T
#     의 뒤 단계는 건너뛰되 ⑤ 는 돈다(다음 T 의 연구 판 D'). 연구 판 D' 가 없는 T 는 ②~④·⑥ 을 건너뛴다.
#   집계: <out>/logs/passN/board.tsv — 날짜별 verdict · rc · 미설명 수 · spec 별 Spearman, 끝 줄 합계(replay_tool.py
#     board-summary). 재생 표시는 postclose.db replay_source · 인계 이력 replay · fi 판 manifest replay(자른 날) ·
#     대조 결과 replay: true 에 남는다.
#   재생으로 못 보는 것: 도착 시각·락·크론·16:00 컷오프(대상인데 21:05 원장에 없는 종목만 T 가격 없음)·정규장
#     수급 정의(재생 T 수급 = 하루 전체라 연구 판과 같다), 그날 마스터(security·corp)·equity 재계산 차이(두 판이
#     같은 현판을 읽어 상쇄된다) — 그림자 3거래일 몫(§4). 체인의 stage 단독 건전성(C1~C6 — 커밋 시각·행수 단조·
#     신선도, 재생 원장은 T 하루치라 맞지 않는다)·스냅샷 GC·런 로그·알림·엑셀·v3 반영은 돌지 않는다.
#
# 기록: <out>/logs/passN/ — summary.tsv(step·table·rc·sec·build_id·content_hash·n_rows), summary.txt
#       (한 줄 요약, 표준 출력에도), run.txt(인자), 단계별 로그. 같은 --out 으로 다시 돌리면 N 이 늘고
#       같은 루트에 새 판을 짓는다(재현성 EG5a — 두 패스 summary.tsv 의 content_hash 열 비교).
# 실패: equity 표 하나가 실패하면 남은 표와 뒤 단계를 돌지 않는다(혼합 판 위에 짓지 않는다).
#       catalog 실패는 contract 만, fi 실패는 model 만 막는다. contract 는 기록형(build_chain step_soft).
# rc: 0 고른 단계 전부 성공(compare: 전부 같음) · 1 실패 단계 있음(compare: 다름·없음 · 장 마감 판 재생:
#     어느 T 의 단계 실패·대조 rc≠0) · 2 인자·입력 오류
# 빌드 락은 잡지 않는다 — 잡으면 운영 체인(flock -n)이 그 회차를 건너뛴다. 운영 빌드 체인과 겹치지 않는
# 때에 돌린다(겹치면 메모리 경합, 기본 모드는 stage 현판이 도중에 바뀔 수 있다).
set -uo pipefail
OPS="$HOME/quant-ledger"
PY="$OPS/.venv/bin/python"
TOOL="$(cd "$(dirname "$0")" && pwd -P)/replay_tool.py"
EVT="$(dirname "$TOOL")/replay_evening.py"     # 장 마감 판 재생 보조 — 검증 대상 코드를 import 한다
ALL_STEPS="equity catalog contract fi model"

die() { echo "replay: $*" >&2; exit 2; }

OUT=""; CODE="$OPS"; D=""; BASIS="morning"; STEPS=""; STEPS_GIVEN=""
ENGINE=""; STAGE_AT=""; COMPARE=""; DATES=""; SPMIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out|--code|--date|--basis|--steps|--engine|--stage-at|--compare|--dates|--spearman-min)
      [ $# -ge 2 ] || die "$1 에 값이 없다"
      case "$1" in
        --out) OUT="$2" ;;
        --code) CODE="$2" ;;
        --date) D="$2" ;;
        --basis) BASIS="$2" ;;
        --steps) STEPS="$2"; STEPS_GIVEN=1 ;;
        --engine) ENGINE="$2" ;;
        --stage-at) STAGE_AT="$2" ;;
        --compare) COMPARE="$2" ;;
        --dates) DATES="$2" ;;
        --spearman-min) SPMIN="$2" ;;
      esac
      shift 2 ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done

# 없는 경로도 가장 가까운 있는 조상을 실제 경로(pwd -P)로 풀어 붙인다 — 심볼릭 링크로 운영 홈에 돌아
# 들어가는 것을 막는다. 아직 없는 부분에 . · .. · 매달린 링크가 있으면 풀 수 없으므로 실패한다.
abspath() {
  local p="$1" rest=""
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do
    [ -L "$p" ] && return 1          # 매달린 링크 — mkdir -p 가 그 대상(운영일 수 있다)에 만든다
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
[ -z "$DATES" ] || [[ "$DATES" =~ ^[0-9]{8}-[0-9]{8}$ ]] || die "--dates 는 YYYYMMDD-YYYYMMDD: $DATES"
[ -z "$SPMIN" ] || [[ "$SPMIN" =~ ^[0-9]*\.?[0-9]+$ ]] || die "--spearman-min 은 0 이상 수: $SPMIN"
if [ "$BASIS" = evening ]; then
  ALL_STEPS="equity board"
  [ -z "$STAGE_AT" ] || die "--stage-at 은 --basis evening 과 함께 쓰지 않는다(장 마감 판 재생은 현판 + 세션 자르기)"
  [ -z "$D" ] || [ -z "$DATES" ] || die "--date 와 --dates 중 하나만 준다"
else
  [ -z "$DATES" ] && [ -z "$SPMIN" ] || die "--dates·--spearman-min 은 --basis evening(장 마감 판 재생)에서만 쓴다"
fi
[ -n "$STEPS_GIVEN" ] || STEPS=$(echo "$ALL_STEPS" | tr ' ' ',')
R=$(abspath "$OUT") || die "--out 을 실제 경로로 풀 수 없다(아직 없는 부분에 . · .. · 매달린 링크): $OUT"
R="${R%/}"     # 끝 슬래시 정규화 — '/' 는 빈 문자열이 되어 아래에서 모든 경로의 조상으로 걸린다
OPS_ABS=$(abspath "$OPS") || die "운영 홈 경로를 풀 수 없다: $OPS"
OPS_DATA_ABS=$(abspath "$OPS/data") || die "운영 data 경로를 풀 수 없다: $OPS/data"
# 운영 data 가 다른 디스크로 가는 링크일 수 있다 — 홈과 data 의 실제 경로를 둘 다 본다
for g in "$OPS_ABS" "$OPS_DATA_ABS"; do
  case "$R/" in "$g/"*) die "--out 이 운영 루트 안이다: ${R:-/} (운영 $g) — 운영 루트에는 쓰지 않는다" ;; esac
  case "$g/" in "$R/"*) die "--out 이 운영 루트의 조상이다: ${R:-/} (운영 $g) — 운영 루트를 품는 곳에는 쓰지 않는다" ;; esac
done
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
if has board && [ -z "$D$DATES" ]; then die "board 단계에는 --date 또는 --dates 가 필요하다"; fi
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
CAL="$OPS/data/calendar"
KW_DB="$OPS/data/raw/kiwoom.db"
DAYS=""
if has board; then
  [ -d "$CAL" ] || die "판정 달력이 없다: $CAL"
  [ -f "$KW_DB" ] || die "21:05 키움 원장이 없다: $KW_DB"
  RANGE="${DATES:-$D-$D}"
  DAYS=$(PYTHONPATH="$CODE/src" PYTHONDONTWRITEBYTECODE=1 "$PY" "$EVT" days --calendar-dir "$CAL" \
           --from "${RANGE%-*}" --to "${RANGE#*-}") || die "재생 날짜를 정하지 못했다(판정 달력 $CAL): ${DATES:-$D}"
  if ! has equity; then
    eqp=$("$PY" "$TOOL" equity-pass --logs "$R/logs" 2>&1) \
      || die "출력 루트의 equity 판을 쓸 수 없다 — $eqp (--steps equity,board 로 다시 짓는다)"
  fi
fi

# 불변식: 이 스크립트는 출력 루트에 링크를 만들지 않는다. 링크가 있으면 손으로 둔 것이고, 그 링크를 따라
# 운영에 쓸 수 있다(옛 원형 루트의 data 링크 — 리뷰 BLOCKER-1). 아무것도 쓰기 전에 멈춘다.
lnk=$(find "$R/data" "$R/logs" "$R/stage_at" "$R/stage_pin" -type l -print -quit 2>/dev/null)
[ -z "$lnk" ] || die "출력 루트 안에 링크가 있다: $lnk — 운영에 쓸 수 있어 멈춘다(링크 없는 --out 을 쓴다)"
mkdir -p "$R/data/equity" "$R/logs" || die "출력 루트를 만들 수 없다: $R"
for sub in data data/equity data/factor_inputs data/model data/stage data/model_db data/deliver; do
  if [ -e "$R/$sub" ] && [ "$R/$sub" -ef "$OPS/$sub" ]; then
    die "출력 루트의 $sub 가 운영 $sub 와 같은 디렉터리다 — 운영 루트에는 쓰지 않는다"
  fi
done
PASS=$(( $(ls -d "$R"/logs/pass* 2>/dev/null | wc -l) + 1 ))
L="$R/logs/pass$PASS"
# 패스 선점 — -p 없이 만든다. 같은 --out 으로 다른 실행이 같은 번호를 잡았으면 멈춘다
mkdir "$L" 2>/dev/null || die "패스 폴더를 선점하지 못했다: $L — 같은 --out 으로 다른 실행이 돌고 있나"
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
T0=$(date +%s)
{
  echo "시작 $(kst)"
  echo "out=$R code=$CODE date=${D:--} basis=$BASIS steps=$STEP_LIST engine=$ENGINE stage_at=${STAGE_AT:--}"
  [ -z "$DAYS" ] || echo "dates=${DATES:-$D} D'=$(echo "$DAYS" | head -1) T=$(echo "$DAYS" | tail -n +2 | paste -sd, -) spearman_min=${SPMIN:-기본}"
} > "$L/run.txt"
echo "════ replay pass$PASS $(kst) — out=$R code=$CODE D=${D:--} basis=$BASIS steps=$STEP_LIST ════"

# 게이트 기준 파일은 출력 루트에 baseline.json 이 없을 때(첫 패스)만 운영에서 복사한다 — 표 폴더·_pinned·
# equity.duckdb 는 복사하지 않는다(원형과 같다). 다음 패스는 앞 패스가 남긴 기준으로 판정한다(운영이 그
# 사이 기준을 바꿔도 같은 루트 안 비교가 흔들리지 않게).
if [ ! -e "$R/data/equity/baseline.json" ]; then
  EQ_OPS="$OPS/data/equity"
  for p in "$EQ_OPS/baseline.json" "$EQ_OPS"/baseline_seed_*.json "$EQ_OPS"/_seed_s0*.json \
           "$EQ_OPS/_catalog_meta.json" "$EQ_OPS/_contract_meta.json" "$EQ_OPS/_asof" "$EQ_OPS/fixtures"; do
    [ -e "$p" ] || continue
    cp -Rp "$p" "$R/data/equity/" || die "기준 파일 사본 실패: $p"
    echo "기준 파일 사본 ${p##*/}" >> "$L/run.txt"
  done
fi
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
  # 표별 해시를 summary.tsv 에 붙인다 — layer_rows <단계> <층> [rows 인자…]. 층 루트의 부모는 ROWS_DATA
  # (기본 출력 루트 data — 장 마감 판은 data/model_db)
  local name="$1" layer="$2"; shift 2
  "$PY" "$TOOL" rows --data "${ROWS_DATA:-$R/data}" --layer "$layer" "$@" \
    | awk -F'\t' -v s="$name" 'BEGIN { OFS = "\t" } { print s, $1, 0, "-", $2, $3, $4 }' >> "$SUM" \
    && return 0
  FAILED="$FAILED $name(해시 읽기 실패)"
  return 1
}

EQ_OK=1      # equity 층을 읽어도 되는가(이번 패스 equity 가 실패하면 혼합 판이다)
equity_tables() {
  # equity_tables <basis> — 표 순서대로 짓고 표별 해시를 남긴다. 표 하나가 실패하면 남은 표는 skip
  local eq_basis="$1" t t1 rc sec row n_all=0 n_ok=0 ORDER
  ORDER=$(grep -vE '^\s*(#|$)' "$CODE/scripts/equity_order.txt")
  for t in $ORDER; do
    n_all=$((n_all + 1))
    if [ "$EQ_OK" -ne 1 ]; then
      printf 'equity\t%s\tskip\t-\t-\t-\t-\n' "$t" >> "$SUM"
      continue
    fi
    t1=$(date +%s)
    nice -n 10 "$PY" -m equity --root "$R/data/equity" --stage-root "$STAGE" build "$t" \
      --basis "$eq_basis" --threads 3 --memory-limit 8GB --keep 3 > "$L/eq_$t.log" 2>&1
    rc=$?
    sec=$(( $(date +%s) - t1 ))
    if [ "$rc" -eq 0 ]; then
      # 해시를 못 읽으면(MANIFEST 가 깨졌거나 없다) 그 표 상태를 모른다 — 실패로 세고 뒤를 막는다(P1)
      if row=$("$PY" "$TOOL" rows --data "$R/data" --layer equity --table "$t" | cut -f2-) \
           && [ "$(printf '%s' "$row" | cut -f2)" != "-" ]; then
        n_ok=$((n_ok + 1))
      else
        row=$(printf -- '-\t-\t-')
        EQ_OK=0
        FAILED="$FAILED equity:$t(해시 읽기 실패)"
      fi
    else
      row=$(printf -- '-\t-\t-')
      EQ_OK=0
      FAILED="$FAILED equity:$t(rc=$rc)"
    fi
    printf 'equity\t%s\t%s\t%s\t%s\n' "$t" "$rc" "$sec" "$row" >> "$SUM"
    echo "  equity $t rc=$rc ${sec}s $row"
  done
  BRIEF="$BRIEF · equity $n_ok/$n_all"
}

# ── 장 마감 판 재생(--basis evening, 컷오버 PR-8b) — 머리 주석 '장 마감 판 재생' ─────────────────────────
MDB="$R/data/model_db"                         # 장 마감 판 루트(운영 data/model_db 자리 — T-3·T-29)
PIN="$R/stage_pin/pass$PASS"                   # fi 가 stage 에서 직접 읽는 표의 고정 판
PCR="$R/data/postclose_replay/pass$PASS"       # 재생 T 행 원장(T 마다 postclose.db 하나)
PREV_OK=0                                      # 직전 연구 판(D') fi 가 이번 패스에 섰나
research_fi() {
  # 연구 판 X — fi 아침판(운영 model_daily 와 같은 명령 + 출력 루트 + --replay: 세션 축을 X 에서 자른다)
  step "fi_r@$1" "$L/fi_r_$1.log" "$PY" -m factor_inputs build --date "$1" --basis morning \
    --root "$R/data/factor_inputs" --stage-root "$PIN" --equity-root "$R/data/equity" --replay \
    && layer_rows "fi_r@$1" fi --date "$1" --basis morning
}
board_day() {
  # board_day <D'> <T> — ② T 행 원천 ③ fi 장 마감 판 ④ 모델 장 마감 판 ⑤ 연구 판 T ⑥ 두 판 대조.
  # 장 마감 쪽(②~④)은 postclose_chain.sh close 와 같은 명령에 출력 루트·재생 인자만 더했다. ⑤ 는 늘 돈다
  local dp="$1" t="$2" ev=0 rs=0
  if [ "$PREV_OK" -eq 1 ]; then
    ev=1
    step "postclose@$t" "$L/postclose_$t.log" "$PY" "$EVT" postclose --kiwoom-db "$KW_DB" --base "$R" \
      --date "$t" --d-prime "$dp" --out "$PCR/$t/postclose.db" || ev=0
  else
    skip "postclose@$t" "연구 판 D'=$dp 가 이번 패스에 없다"
  fi
  if [ "$ev" -eq 1 ]; then
    step "pc_stage@$t" "$L/pc_stage_$t.log" "$PY" -m stage --table stg_flow_postclose_kiwoom \
      --basis evening --raw-dir "$PCR/$t" --stage-root "$MDB/stage" --snapshot-root "$MDB/snapshots" \
      || ev=0
  else
    skip "pc_stage@$t" "앞 단계 실패"
  fi
  if [ "$ev" -eq 1 ]; then
    if step "fi_e@$t" "$L/fi_e_$t.log" "$PY" -m factor_inputs build --date "$t" --basis evening \
         --root "$MDB/factor_inputs" --builds-from "$R/data/deliver/history/${dp}_morning.json" \
         --calendar-dir "$CAL" --stage-root "$PIN" --equity-root "$R/data/equity" \
         --postclose-stage-root "$MDB/stage" --candidates-root "$R/data/factor_inputs" --replay; then
      ROWS_DATA="$MDB" layer_rows "fi_e@$t" fi --date "$t" --basis evening || ev=0
    else
      ev=0
    fi
  else
    skip "fi_e@$t" "앞 단계 실패"
  fi
  if [ "$ev" -eq 1 ]; then
    if step "model_e@$t" "$L/model_e_$t.log" "$PY" -m model build --date "$t" --basis evening \
         --root "$MDB/model" --fi-root "$MDB/factor_inputs"; then
      ROWS_DATA="$MDB" layer_rows "model_e@$t" model --date "$t" --basis evening || ev=0
    else
      ev=0
    fi
  else
    skip "model_e@$t" "앞 단계 실패"
  fi
  # ⑤ 연구 판 T — 다음 T 의 D' 이기도 하다
  PREV_OK=0
  if research_fi "$t"; then
    PREV_OK=1
    if step "model_r@$t" "$L/model_r_$t.log" "$PY" -m model build --date "$t" --basis morning \
         --root "$R/data/model" --fi-root "$R/data/factor_inputs"; then
      layer_rows "model_r@$t" model --date "$t" --basis morning && rs=1
    fi
  else
    skip "model_r@$t" "연구 판 fi 실패"
  fi
  if [ "$ev" -eq 1 ] && [ "$rs" -eq 1 ]; then
    # shellcheck disable=SC2086  # reason: ${SPMIN:+…} 는 비었거나 "--spearman-min <X>" 두 낱말이다
    step "compare@$t" "$L/compare_$t.log" "$PY" -m daily.board_compare --date "$t" \
      --evening-root "$MDB" --research-root "$R/data" --calendar-dir "$CAL" --replay \
      ${SPMIN:+--spearman-min "$SPMIN"}
  else
    skip "compare@$t" "장 마감 판 또는 연구 판 T 가 없다"
  fi
}
evening_main() {
  local dp0 ts t dp srcs msg rc x ready=1 board brief
  dp0=$(echo "$DAYS" | head -1)
  ts=$(echo "$DAYS" | tail -n +2)
  # equity — 연구(아침 확정) 판 규약. 장 마감 판이 고정하는 것도 연구 판이고 fi 는 m_·b_ equity 판만 읽는다
  if has equity; then
    equity_tables morning
  else
    BRIEF="$BRIEF · equity 앞 패스 판($eqp)"
  fi
  [ "$EQ_OK" -eq 1 ] || ready=0
  has board || ready=-1                         # equity 만 고른 패스 — 날짜별 단계 없음
  if [ "$ready" -eq 1 ]; then
    # fi 가 stage 에서 직접 읽는 표 — 목록의 정본은 검증 대상 코드(factor_inputs.build)
    if srcs=$("$PY" -c 'from factor_inputs.build import OPTIONAL_STAGE_SOURCES as o, STAGE_SOURCES as s
print(",".join(t for t in s if t not in o))
print(",".join(o))' 2>> "$L/run.txt"); then
      msg=$("$PY" "$TOOL" stage-pin --ops-data "$OPS/data" --tables "$(echo "$srcs" | sed -n 1p)" \
              --optional "$(echo "$srcs" | sed -n 2p)" --dest "$PIN" 2>&1 > "$L/stage_pin.tsv")
      rc=$?
      echo "$msg" | tee -a "$L/run.txt"
      [ "$rc" -eq 0 ] || { FAILED="$FAILED stage-pin(rc=$rc)"; ready=0; }
    else
      FAILED="$FAILED stage-pin(fi 원천 목록을 읽지 못함)"; ready=0
    fi
  fi
  if [ "$ready" -eq 1 ]; then
    for x in $DAYS; do
      "$PY" "$TOOL" handoff --data "$R/data" --stage-root "$PIN" --date "$x" \
        --note "replay.sh --basis evening pass$PASS" >> "$L/run.txt" 2>&1 \
        || { FAILED="$FAILED handoff:$x"; ready=0; break; }
    done
  fi
  if [ "$ready" -eq 1 ]; then
    research_fi "$dp0" && PREV_OK=1           # 첫 T 의 연구 판 D' — fi 만(후보·3자 대조가 읽는다)
    dp="$dp0"
    for t in $ts; do
      echo "── T=$t (D'=$dp) $(kst)"
      board_day "$dp" "$t"
      dp="$t"
    done
  elif [ "$ready" -eq 0 ]; then
    for t in $ts; do skip "board@$t" "equity·고정 stage·인계 이력 준비 실패"; done
  fi
  brief="${BRIEF#" ·"}"
  brief="${brief%%" · fi_r@"*}"                 # 단계별 ok 목록은 summary.tsv 에 — 요약 줄에는 equity 만
  if has board; then
    board=$("$PY" "$TOOL" board-summary --compare-dir "$MDB/compare" \
              --dates "$(echo "$ts" | paste -sd, -)" --since "$T0")
    printf '%s\n' "$board" > "$L/board.tsv"
    printf '%s\n' "$board"
    LINE="replay pass$PASS basis=evening D'=$dp0 T=$(echo "$ts" | head -1)..$(echo "$ts" | tail -1)"
    LINE="$LINE($(echo "$ts" | wc -l | tr -d ' ')일) stage=current(고정 stage_pin) code=$CODE —$brief"
    LINE="$LINE · $(echo "$board" | tail -1)"
  else
    LINE="replay pass$PASS basis=evening(equity 만) stage=current code=$CODE —$brief"
  fi
  LINE="$LINE · 실패 ${FAILED:-없음} · $(( $(date +%s) - T0 ))s"
  LINE="$LINE · 크기 $(du -sh "$R" 2>/dev/null | cut -f1)(stage 하드링크 포함)"
  echo "$LINE" > "$L/summary.txt"
  echo "$LINE"
  echo "기록 $L/summary.tsv$(has board && echo " · 날짜별 대조 $L/board.tsv · 대조 결과 $MDB/compare/<T>.json")"
  echo "정리(다 쓴 뒤 — 운영 stage 파일의 하드링크를 쥐고 있다): rm -rf -- \"$R\""
  [ -z "$FAILED" ] || exit 1
  exit 0
}

if [ "$BASIS" = evening ]; then
  evening_main      # 끝에서 exit 한다 — 아래 아침판 단계로 내려가지 않는다
fi
if has equity; then
  equity_tables "$BASIS"
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
      layer_rows fi fi --date "$D" --basis "$BASIS" || FI_OK=0
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
echo "정리(다 쓴 뒤 — 운영 stage 파일의 하드링크를 쥐고 있다): rm -rf -- \"$R\""
[ -z "$FAILED" ] || exit 1
exit 0
