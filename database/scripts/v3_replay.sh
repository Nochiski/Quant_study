#!/usr/bin/env bash
# v3 소비자 재생 실행기 — 컷오버 트랙 QL-G(정본 `docs/plans/2026-10-10-cutover-track.md` §3 P1 QL-G · P5 'v3 소비자
#   재생 — 등록 범주 밖 0, 점수 Spearman 분포', `docs/COMPAT_LAYER.md` §7). 서버 전용.
#   거래일 D 마다(판정 달력으로 센다):
#   ① v3 날짜별 사본 `quant_<D>.db`(v3 가 그날 쓴 결과, 읽기 전용 보존본)를 --out 아래로 **복사**한다 — 대조 기준
#      `v3.db` 와 반영 대상 `compat.db` 두 벌, 다음 거래일 사본이 있으면 `v3_next.db`(§7 daily_prices 6 판정)
#   ② `compat.db` 에 compat 제자리 반영 — `scripts/v3_post.sh` ①~④ 와 같은 compat 하위 명령을 같은 순서로 부른다:
#      stage → v3-tables → export --in-place → apply. 그날 판으로 고정한다(`--builds-from
#      <연구 루트>/data/deliver/history/<D>_morning.json`, `--basis morning`, 점수 포함 — 사본에 compat 기록이 없으면 9표).
#      재생이라 `--allow-older`(T-35 순서 가드)를 넘긴다
#   ③ 대조기 `python -m compat.v3_replay compare` → `<out>/<D>/v3_replay.json`(판정 범위는 T-45 — 매일 도는 소비자가
#      읽는 열만 rc 를 정하고, 리서치센터만 읽는 열·읽는 곳 없는 열은 수만 기록한다)
#   끝에 `<out>/summary.tsv`(날짜·상태·rc·갈래별 미설명 수·점수 Spearman·범주·미설명 사유)와 `<out>/hashes.tsv`(원본
#   사본 sha256 전·후)를 쓴다.
#
#   v3_post.sh 를 그대로 부르지 않는 이유: 그 셸은 운영 홈의 `logs/v3_post/`·`logs/notify.log` 에 쓰고(그림자도 info
#   한 줄 — notify.log 는 X-2 연속 창 판정이 읽는 경보 장부다), 제자리 모드는 v3 락(`/tmp/kael_v3_daily_all.lock`)을
#   쥐어 v3 크론(flock -n)을 건너뛰게 한다. 반영 규칙(창·게이트·표 범위)은 compat 하위 명령이 정본이라 여기서는
#   순서와 인자만 같게 둔다 — v3_post.sh 의 단계가 바뀌면 이 셸도 같이 고친다.
#
# 격리(X-1 불변식 — 운영 루트·원본 사본에 쓰기 0, `scripts/replay.sh` 와 같은 검사):
#   · --out 을 실제 경로로 풀어(아직 없는 부분의 . · .. · 매달린 링크 거부) 연구 루트·그 data·사본 폴더 안이거나 그
#     조상이면 거부한다. --out 은 없거나 빈 폴더여야 한다(지난 결과와 섞지 않는다). 출력 루트에 링크를 만들지 않는다
#   · 원본 사본은 `cp` 로 읽기만 하고, 쓴 날짜마다 sha256 을 전·후로 대조한다 — 다르면 rc 2(X-1 위반)
#   · sqlite 는 원본을 직접 열지 않는다(WAL 사본을 읽기 전용으로 열어도 옆에 -shm 을 만들 수 있다). 원본 옆에
#     비어 있지 않은 `-wal` 이 있으면 정적 사본이 아니므로 그날은 대조하지 않는다(copy_wal)
#   · python 은 연구 루트 venv, 코드는 이 셸의 저장소(`<scripts/..>/src`) — 바이트코드를 쓰지 않는다
#     (PYTHONDONTWRITEBYTECODE). QL_HOME·TMPDIR 은 --out 아래다(기본 경로 쓰기가 운영으로 가지 않게)
#   · 경로 인자를 실제 경로로 푼 뒤 `<out>/tmp` 로 cd 한다 — DuckDB 는 TMPDIR 이 아니라 cwd 의 `.tmp` 를 임시 폴더로
#     쓰므로(리뷰 MINOR-1 실험), 연구 루트에서 실행해도 그 아래에 아무것도 생기지 않게
#   · 락은 잡지 않는다 — 운영 빌드 체인과 겹치지 않는 때(10:30~15:10)에 돌린다(메모리 경합)
#
# 사용:
#   v3_replay.sh --v3-copies DIR --dates YYYYMMDD[-YYYYMMDD] --out DIR --root DIR
#                [--full] [--allow-current-builds] [--min-spearman X] [--keep-db]
#   --v3-copies    v3 날짜별 사본 폴더(`quant_<YYYYMMDD>.db`)
#   --dates        A-B — 그 사이 거래일(판정 달력 `<root>/data/calendar`). 하루면 A 만
#   --out          격리 출력 폴더
#   --root         연구 루트(운영 홈 — `data/{equity,stage,model,calendar,deliver/history}`·`.venv`). 읽기만 한다
#   --full         compat `--full`(730일 창 — 손 복구 도구). 없으면 매일과 같은 증분 창(마지막 거래일과 앞 10거래일,
#                  K1-9d — T-46 뒤 첫 반영 V3-C 도 이 창이다)
#   --allow-current-builds  인계 이력이 가리킨 equity·stage 판이 보관 판 밖이면(GC) 현판으로 대체한다(T-45 — 옵트인).
#                  compat `--builds-from-missing current` 를 넘기고, 대체한 표는 compat 기록을 거쳐 JSON
#                  `inputs.builds_fallback` 에 남는다(그 표가 원천인 v3 표·열의 차이는 대조기 `builds_fallback` 갈래 —
#                  수만 기록). 기본은 그날 판이 없으면 반영 실패(reflect_failed:export). 옛 날짜는 대체 판 조합에
#                  따라 export 가 실패할 수 있다(판 스키마 차이 — 예: 현판과 옛 판이 섞여 security 열이 없다) — 그날은 대조 불가
#   --min-spearman 대조기 점수 Spearman 하한(기본 0 = 기록형 — P5 분포를 잰다)
#   --keep-db      날짜 폴더의 db(v3·compat·v3_next)를 남긴다. 기본은 대조 뒤 지운다(날마다 사본 크기 × 3)
# 날짜 폴더 `<out>/<D>/`: v3_replay.json(대조 보고, 대조 못 한 날은 상태 기록) · run.log(단계별 출력)
# 상태: ok(매일 소비자 열 미설명 0) · other(그런 미설명 있음 — 리서치센터만·소비자 없음 갈래는 수만 기록, T-45) ·
#       no_copy(사본 없음) · copy_wal · reflect_failed:<단계> · compare_error
# rc: 0 고른 거래일 전부 ok · 1 ok 아닌 날 있음 · 2 인자·격리 위반·원본 사본 변경·달력 오류
set -uo pipefail

die() { echo "v3_replay: $*" >&2; exit 2; }

COPIES=""; DATES=""; OUT=""; ROOT=""; FULL=""; MINSP="0"; KEEP=""; MISSING=""
while [ $# -gt 0 ]; do
  case "$1" in
    --v3-copies|--dates|--out|--root|--min-spearman)
      [ $# -ge 2 ] && [ -n "$2" ] || die "$1 에 값이 없다"
      case "$1" in
        --v3-copies) COPIES="$2" ;;
        --dates) DATES="$2" ;;
        --out) OUT="$2" ;;
        --root) ROOT="$2" ;;
        --min-spearman) MINSP="$2" ;;
      esac
      shift 2 ;;
    --full) FULL="--full"; shift ;;
    --keep-db) KEEP=1; shift ;;
    --allow-current-builds) MISSING=current; shift ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done
[ -n "$COPIES" ] || die "--v3-copies 가 필요하다"
[ -n "$OUT" ] || die "--out 이 필요하다"
[ -n "$ROOT" ] || die "--root 가 필요하다"
case "$DATES" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) FROM="$DATES"; TO="$DATES" ;;
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
    FROM="${DATES%-*}"; TO="${DATES#*-}" ;;
  *) die "--dates 는 YYYYMMDD 또는 YYYYMMDD-YYYYMMDD: '$DATES'" ;;
esac
[[ "$MINSP" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--min-spearman 은 0 이상의 수: $MINSP"
[ -d "$COPIES" ] || die "사본 폴더가 없다: $COPIES"
[ -d "$ROOT/data" ] || die "연구 루트에 data/ 가 없다: $ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || die "연구 루트 venv python 이 없다: $PY"
CODE="$(cd "$(dirname "$0")/.." && pwd -P)"
[ -d "$CODE/src/compat" ] || die "코드 루트에 src/compat 이 없다: $CODE"

# 없는 경로도 가장 가까운 있는 조상을 실제 경로(pwd -P)로 풀어 붙인다(scripts/replay.sh 와 같다)
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
R=$(abspath "$OUT") || die "--out 을 실제 경로로 풀 수 없다(아직 없는 부분에 . · .. · 매달린 링크): $OUT"
R="${R%/}"
[ -n "$R" ] || die "--out 이 / 다"
ROOT_ABS=""; COPIES_ABS=""
for g in "$ROOT" "$ROOT/data" "$COPIES"; do
  G=$(abspath "$g") || die "경로를 풀 수 없다: $g"
  [ "$g" = "$ROOT" ] && ROOT_ABS="$G"
  [ "$g" = "$COPIES" ] && COPIES_ABS="$G"
  case "$R/" in "$G/"*) die "--out 이 보호 경로 안이다: $R (보호 $G) — 연구 루트·원본 사본 폴더에는 쓰지 않는다" ;; esac
  case "$G/" in "$R/"*) die "--out 이 보호 경로의 조상이다: $R (보호 $G)" ;; esac
done
if [ -e "$R" ]; then
  [ -d "$R" ] && [ ! -L "$R" ] || die "--out 이 폴더가 아니다(또는 링크다): $R"
  [ -z "$(ls -A "$R")" ] || die "--out 이 비어 있지 않다: $R — 지난 결과와 섞지 않게 새 폴더를 쓴다"
fi
mkdir -p "$R/tmp" || die "출력 루트를 만들 수 없다: $R"
# 여기부터 경로는 전부 실제 경로다 — cwd 를 출력 루트 아래로 옮긴다(위 '격리' — DuckDB `.tmp`)
ROOT="$ROOT_ABS"; COPIES="$COPIES_ABS"; PY="$ROOT/.venv/bin/python"
cd "$R/tmp" || die "출력 루트로 이동 실패: $R/tmp"

export QL_HOME="$R" TMPDIR="$R/tmp" PYTHONPATH="$CODE/src" PYTHONDONTWRITEBYTECODE=1
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
stub() {  # stub <날짜 폴더> <D> <상태> — 대조하지 못한 날의 기록(요약 tsv 가 읽는다)
  printf '{"date": "%s", "status": "%s", "rc": 1}\n' "$2" "$3" > "$1/v3_replay.json"
}
step() {  # step <이름> <명령…> — 로그 머리줄을 남기고 STEP 에 지금 단계를 둔다(실패하면 그 이름이 남는다)
  STEP="$1"; shift
  echo "──── [$(kst)] $STEP"
  "$@"
}
plan_tables() {  # 반영 표(T-34) — v3_post.sh 와 같은 판정
  TABLES=$("$PY" -m compat v3-tables --v3-db "$DIR/compat.db" --date "$D" --basis morning) || return 1
  echo "반영 표: $TABLES"
}
{
  echo "시작 $(kst)"
  echo "v3_copies=$COPIES dates=$FROM-$TO out=$R root=$ROOT code=$CODE full=${FULL:-no}" \
       "allow_current_builds=${MISSING:-no} min_spearman=$MINSP keep_db=${KEEP:-no}"
} > "$R/run.txt"

CAL="$ROOT/data/calendar"
DAYS=$("$PY" -m compat.v3_replay dates --from "$FROM" --to "$TO" --calendar-dir "$CAL" 2>"$R/tmp/dates.err") ||
  die "거래일을 셀 수 없다(판정 달력 $CAL): $(cat "$R/tmp/dates.err")"
[ -n "$DAYS" ] || die "구간 $FROM-$TO 에 거래일이 없다"
printf 'file\tsha256_before\tsha256_after\n' > "$R/hashes.tsv"
BAD=""; CHANGED=""
echo "════ v3_replay $(kst) — $FROM-$TO out=$R full=${FULL:-no} ════"
while IFS=$'\t' read -r D NEXT <&3; do      # 날짜 목록은 fd 3 — 자식이 표준 입력을 읽어도 목록이 줄지 않게
  DIR="$R/$D"
  mkdir "$DIR" || die "날짜 폴더를 만들 수 없다: $DIR"
  LOG="$DIR/run.log"
  SRC="$COPIES/quant_$D.db"
  if [ ! -f "$SRC" ]; then
    stub "$DIR" "$D" no_copy; BAD=1; echo "  $D 사본 없음($SRC)"; continue
  fi
  if [ -s "$SRC-wal" ]; then
    stub "$DIR" "$D" copy_wal; BAD=1; echo "  $D 원본 옆 -wal 이 비어 있지 않다 — 정적 사본이 아니라 건너뜀"; continue
  fi
  NSRC=""
  if [ "$NEXT" != "-" ] && [ -f "$COPIES/quant_$NEXT.db" ] && [ ! -s "$COPIES/quant_$NEXT.db-wal" ]; then
    NSRC="$COPIES/quant_$NEXT.db"
  fi
  B_SRC=$(sha "$SRC"); B_NSRC=""
  [ -z "$NSRC" ] || B_NSRC=$(sha "$NSRC")
  cp "$SRC" "$DIR/v3.db" && cp "$SRC" "$DIR/compat.db" && chmod u+w "$DIR/v3.db" "$DIR/compat.db" ||
    die "사본 복사 실패: $SRC → $DIR"
  NEXT_ARG=()
  if [ -n "$NSRC" ]; then
    cp "$NSRC" "$DIR/v3_next.db" && chmod u+w "$DIR/v3_next.db" || die "다음 거래일 사본 복사 실패: $NSRC"
    NEXT_ARG=(--next-v3-db "$DIR/v3_next.db")
  fi
  STEP=""
  {                                   # 묶음 명령(서브셸 아님) — STEP 이 이 셸에 남는다
    echo "════ [$(kst)] D=$D 다음 거래일=$NEXT 그 사본=${NSRC:-없음}"
    step stage "$PY" -m compat stage --v3-db "$DIR/compat.db" --out "$DIR/staging.db" &&
    step v3-tables plan_tables &&
    # shellcheck disable=SC2086  # reason: $FULL 은 있거나 없는 단일 플래그다
    step export "$PY" -m compat export --date "$D" --basis morning --equity-root "$ROOT/data/equity" \
      --stage-root "$ROOT/data/stage" --model-root "$ROOT/data/model" --target "$DIR/staging.db" \
      --in-place --tables "$TABLES" $FULL --builds-from "$ROOT/data/deliver/history/${D}_morning.json" \
      ${MISSING:+--builds-from-missing "$MISSING"} --allow-older --calendar-dir "$CAL" &&
    step apply "$PY" -m compat apply --staging "$DIR/staging.db" --v3-db "$DIR/compat.db" --date "$D" \
      --basis morning --allow-older &&
    STEP=""
  } >> "$LOG" 2>&1
  rm -f "$DIR/staging.db" "$DIR/staging.db-wal" "$DIR/staging.db-shm"
  if [ -n "$STEP" ]; then
    stub "$DIR" "$D" "reflect_failed:$STEP"; BAD=1; echo "  $D 반영 실패 — 단계 $STEP(로그 $LOG)"
  else
    "$PY" -m compat.v3_replay compare --compat-db "$DIR/compat.db" --v3-db "$DIR/v3.db" --date "$D" \
      --equity-root "$ROOT/data/equity" ${NEXT_ARG[@]+"${NEXT_ARG[@]}"} --min-spearman "$MINSP" \
      --json "$DIR/v3_replay.json" >> "$LOG" 2>&1
    crc=$?
    if [ "$crc" -eq 2 ] || [ ! -f "$DIR/v3_replay.json" ]; then
      stub "$DIR" "$D" compare_error; BAD=1; echo "  $D 대조 오류 rc=$crc(로그 $LOG)"
    else
      [ "$crc" -eq 0 ] || BAD=1
      echo "  $D 대조 rc=$crc — $(grep -m1 '^v3_replay D=' "$LOG")"
    fi
  fi
  [ -n "$KEEP" ] || rm -f "$DIR"/v3.db* "$DIR"/compat.db* "$DIR"/v3_next.db*
  for pair in "$SRC|$B_SRC" ${NSRC:+"$NSRC|$B_NSRC"}; do
    f="${pair%|*}"; before="${pair##*|}"; after=$(sha "$f")
    printf '%s\t%s\t%s\n' "$f" "$before" "$after" >> "$R/hashes.tsv"
    [ "$after" = "$before" ] || { CHANGED=1; echo "  !! 원본 사본이 바뀌었다: $f" >&2; }
  done
done 3<<< "$DAYS"

"$PY" -m compat.v3_replay tsv --out "$R/summary.tsv" "$R"/*/v3_replay.json > /dev/null ||
  die "요약 tsv 를 쓰지 못했다: $R/summary.tsv"
echo "요약 $R/summary.tsv · 원본 해시 $R/hashes.tsv"
echo "끝 $(kst)" >> "$R/run.txt"
[ -z "$CHANGED" ] || die "X-1 위반 — 원본 사본 sha256 이 바뀌었다($R/hashes.tsv)"
[ -z "$BAD" ] || exit 1
exit 0
