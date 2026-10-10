#!/usr/bin/env bash
# quant-ledger 코드 배포 — 저장소 database/{src,scripts} 를 서버 ~/quant-ledger/{src,scripts} 로
# 밀어 넣는다. **정본은 저장소**이고 서버는 사본이다(P0 Task 0.9 판정).
#
#   database/scripts/deploy.sh                                   # dry-run (기본, 아무것도 안 바꾼다)
#   database/scripts/deploy.sh --apply                           # 실제 배포
#   database/scripts/deploy.sh --apply --allow-branch <브랜치>    # main 미머지 브랜치를 알고 민다
#   database/scripts/deploy.sh --apply --skip-tests              # 테스트 생략(사유는 DEPLOYED.json 에 남는다)
#   database/scripts/deploy.sh --apply --allow-rollback <서버 rev> # 서버 판이 HEAD 에 없는 것을 알고 되돌린다
#
# --delete 를 쓰는 이유: 서버에만 남은 작업 사본(equity_s23/ 같은 것)이 다음 사람에게
# "둘 중 어느 쪽이 정본인가" 를 다시 묻게 만든다. 저장소에 없으면 서버에도 없어야 한다.
#
# --apply 전 검사(DEFECT-B05·C01·D05: 배포 한 번이 서버의 운영 시간표·핫픽스를 조용히 되돌린 사고):
#   ① 작업 트리가 깨끗해야 한다 — 어느 커밋을 밀었는지 서버에 적을 수 없으면 드리프트를 추적할 수 없다
#   ② HEAD 가 origin/main 을 포함해야 한다. 미머지 브랜치는 `--allow-branch <그 브랜치 이름>` 으로만 허용
#   ③ database/tests 전량 통과(`--skip-tests` 로 생략 가능 — 생략 사실이 서버에 기록된다)
#   ④ 배포 결과를 서버 ~/quant-ledger/DEPLOYED.json 에 남긴다(rev·branch·at_utc·by·tests[·rollback_from])
#   ⑤ 서버 빌드 락(/tmp/quant_ledger_build.lock — build_chain·model_daily·gc 등이 쓰는 것)을 비차단으로 잡고
#      rsync 와 DEPLOYED.json 쓰기를 마칠 때까지 쥔다. 못 잡으면 다른 빌드·배포가 실행 중이라 거부한다 — 기다리거나
#      다시 시도하지 않는다(P9). 체인이 빌드하는 도중에 코드가 바뀌면 한 판 안에 옛 코드·새 코드가 섞인다.
#   ⑦ 서버 DEPLOYED.json 의 rev 가 HEAD 의 조상이어야 한다(K1-1e). main 을 역병합한 브랜치는 ② 를 늘
#      통과하므로, 다른 브랜치에서 먼저 민 핫픽스를 모르고 덮는 일은 ② 로 못 막는다. 의도한 되돌림은
#      `--allow-rollback <서버 rev>` 로만 허용하고 DEPLOYED.json 에 rollback_from 으로 남긴다.
#   ⑧ 서버 비밀 파일 ~/quant-ledger/.env(RG-C7-4) — 체인 스크립트가 QL_ENV 를 그 경로로 고정하므로 rsync 전에
#      일반 파일(심볼릭 링크 아님)·권한 600·배포 계정 소유인지 본다. 존재·권한만 보고 내용은 읽지 않는다.
#      아니면 거부 — 절차는 README '운영 (P6) → 비밀 파일'(파일을 먼저 만든 뒤 배포).
#   dry-run 은 ⑤·⑦·⑧ 을 똑같이 판정해 결과만 출력한다(락은 잡았다가 바로 놓는다).
# 서버에는 pytest·ruff 가 없다(맨 pip venv) — 검사는 이 저장소 쪽에서만 돈다.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # …/database
WORKTREE="$(cd "$REPO/.." && pwd)"                           # 저장소 루트(backend/ 가 있는 곳)
REMOTE="${QL_REMOTE:-kael-server}"
ROOT="${QL_REMOTE_ROOT:-quant-ledger}"                       # 원격 홈 기준 상대경로

APPLY=0
SKIP_TESTS=0
ALLOW_BRANCH=""
ALLOW_ROLLBACK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --allow-branch) ALLOW_BRANCH="${2:?--allow-branch 뒤에 브랜치 이름이 필요하다}"; shift 2 ;;
    --allow-rollback) ALLOW_ROLLBACK="${2:?--allow-rollback 뒤에 서버 DEPLOYED.json 의 rev 가 필요하다}"; shift 2 ;;
    *) echo "usage: deploy.sh [--apply|--dry-run] [--allow-branch <name>] [--allow-rollback <server rev>] [--skip-tests]" >&2; exit 2 ;;
  esac
done
DRY="--dry-run"
[ "$APPLY" -eq 1 ] && DRY=""

# 서버에만 있어야 하는 것 — 지우면 안 된다.
#   토큰 2종: 런타임이 굽는 캐시. 저장소에 올릴 수 없다.
#   sync_v3_wise.py: v3 컨센서스 미러. daily_wise 체인에서 2026-09-02 에 빠졌지만
#     rules_s17 이 "재개하면 소급 복구, 단 운영 변경이라 사람 승인 필요" 로 근거에 박아 두었다.
#     승인 전까지는 지우지 않는다.
#   rebuild_share.py: 정본이 저장소 backend/ops/ 쪽이다(md5 동일). 아래 별도 라인으로 민다.
COMMON=(--exclude '__pycache__/' --exclude '.ruff_cache/' --exclude '.venv/'
        --exclude '*.pyc' --exclude '.DS_Store')
SRC_KEEP=(--exclude '.kw_token.json' --exclude '.kis_token.json'
          --exclude 'sync_v3_wise.py' --exclude 'rebuild_share.py')

# equity contract(EGC) 가 부르는 워크벤치 사본(#372 — 전에는 커널 `_engine/backtest_engine` 을
# 불렀다). 사본은 2026-09-05 판에서 멈춰 있었고 갱신 경로가 어디에도 없었다(DEFECT-C05) — 배포가
# 같이 민다. 계약이 읽는 facade 는 제3자 패키지 없이 import 되고 어댑터는 duckdb 만 쓴다.
ENGINE_SRC="$WORKTREE/backend/src/strategy_workbench"
[ -d "$ENGINE_SRC" ] || { echo "거부: $ENGINE_SRC 가 없다 — 엔진 사본을 밀 수 없다." >&2; exit 2; }
# 비었거나 sparse-checkout 인 소스를 --delete 로 밀면 서버 _engine 이 전멸하고 contract 는 기록형이라 묻힌다(리뷰 REC-9)
[ -f "$ENGINE_SRC/__init__.py" ] || { echo "거부: $ENGINE_SRC/__init__.py 가 없다 — 빈 소스로 서버 _engine 을 지울 수 없다." >&2; exit 2; }

BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
REV="$(git -C "$REPO" rev-parse HEAD)"
TESTS="ok"

PLAN=""; LOCK_PID=""; LOCK_DIR=""
cleanup() {   # 끝날 때(정상·거부·중단 모두) 계획 파일을 지우고 쥐고 있던 서버 빌드 락을 놓는다
  [ -z "$PLAN" ] || rm -f "$PLAN"
  if [ -n "$LOCK_PID" ]; then
    exec 8>&- 7<&-      # FIFO 의 쓰는 쪽을 닫으면 원격 cat 이 끝나고 락이 풀린다
    wait "$LOCK_PID" || true
  fi
  [ -z "$LOCK_DIR" ] || rm -rf "$LOCK_DIR"
}
trap cleanup EXIT

if [ "$APPLY" -eq 1 ]; then
  # ① 더러운 트리 — 밀린 코드가 어느 커밋인지 서버에 적을 수 없다
  if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
    echo "거부: 작업 트리가 깨끗하지 않다 — 무엇을 밀었는지 서버에 기록할 수 없다." >&2
    git -C "$REPO" status --porcelain >&2
    exit 2
  fi
  # ② 리비전 — 미머지 브랜치를 모르고 밀면 서버의 운영 시간표·핫픽스가 되돌아간다
  # 낡은 remote-tracking ref 로 판정하면 통과 도장이 찍힌 채 서버의 최신 운영 변경을 덮는다 — 먼저 받아온다
  git -C "$REPO" fetch --quiet origin main || { echo "거부: origin/main 을 받아오지 못했다 — 리비전을 판정할 수 없다." >&2; exit 2; }
  if [ -n "$ALLOW_BRANCH" ] && [ "$ALLOW_BRANCH" = "$BRANCH" ]; then
    echo "== 브랜치 면제: HEAD=$BRANCH ($REV) — --allow-branch 로 main 포함 조건을 건너뛴다"
  elif git -C "$REPO" merge-base --is-ancestor origin/main HEAD; then
    echo "== 리비전 확인: HEAD=$BRANCH ($REV) 가 origin/main 을 포함한다"
  else
    echo "거부: HEAD=$BRANCH ($REV) 가 origin/main 을 포함하지 않는다." >&2
    echo "      그대로 밀면 main 에만 있는 판이 서버의 최신 운영 변경을 --delete 로 덮는다." >&2
    echo "      의도한 배포라면: deploy.sh --apply --allow-branch $BRANCH" >&2
    exit 2
  fi
  # ③ 테스트 — 서버에는 pytest 가 없으므로 여기서 돌리지 않으면 아무 데서도 안 돈다
  if [ "$SKIP_TESTS" -eq 1 ]; then
    TESTS="skipped"
    echo "== 테스트 생략(--skip-tests) — DEPLOYED.json 에 tests=skipped 로 남는다"
  else
    echo "== 테스트: uv run --project backend pytest database/tests -q"
    ( cd "$WORKTREE" && uv run --project backend pytest database/tests -q ) \
      || { echo "거부: database/tests 실패 — 서버에 밀지 않는다." >&2; exit 2; }
  fi
fi

# ⑤·⑦·⑧ 은 두 모드 모두 판정한다. --apply 면 실패 시 거부(rc 2 — 락은 cleanup 이 놓는다), dry-run 이면
# 결과만 출력하고 계속한다. 테스트(③) 뒤에 두는 이유: 락을 쥔 채 몇 분짜리 테스트를 돌리면 그동안 시작하는
# 체인이 락 실패로 그 회차를 건너뛴다.
verdict_fail() {   # 첫 인자는 판정, 나머지는 안내 줄
  local msg="$1"; shift
  if [ "$APPLY" -eq 1 ]; then
    printf '거부: %s\n' "$msg" >&2
    [ $# -eq 0 ] || printf '      %s\n' "$@" >&2
    exit 2
  fi
  printf '== (dry-run) --apply 라면 거부: %s\n' "$msg"
  [ $# -eq 0 ] || printf '      %s\n' "$@"
}

# ⑧ 비밀 파일 — 존재·종류·소유자·권한만 본다(cat·grep 등으로 내용을 읽지 않는다). 락보다 먼저 판정해
# 거부될 배포가 락을 쥐지 않게 한다. stat 형식은 GNU(-c, 서버)·BSD(-f, 맥의 가짜 원격) 둘 다 받는다.
SECRET="$ROOT/.env"
SECRET_CMD='f='"$SECRET"'; if [ -L "$f" ]; then echo SYMLINK; elif [ ! -e "$f" ]; then echo MISSING;'
SECRET_CMD+=' elif [ ! -f "$f" ]; then echo NOTFILE; elif [ ! -O "$f" ]; then echo NOTOWNER;'
SECRET_CMD+=' else echo "MODE $(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f" 2>/dev/null)"; fi'
SECRET_STATE="$(ssh "$REMOTE" "$SECRET_CMD" </dev/null || true)"
SECRET_HELP="README '운영 (P6) → 비밀 파일' 절차로 서버에 파일을 먼저 만든 뒤(권한 600·배포 계정 소유) 다시 실행한다."
case "$SECRET_STATE" in
  "MODE 600")
    echo "== 비밀 파일: $REMOTE:~/$SECRET 있음 · 권한 600 · 배포 계정 소유" ;;
  MISSING)
    verdict_fail "서버 비밀 파일 ~/$SECRET 가 없다 — 체인 스크립트가 QL_ENV 를 그 경로로 고정해 배포 뒤 체인이 비밀을 읽지 못한다." \
                 "$SECRET_HELP" ;;
  SYMLINK|NOTFILE)
    verdict_fail "서버 비밀 파일 ~/$SECRET 가 일반 파일이 아니다($SECRET_STATE) — 링크로 다른 시스템의 비밀 파일을 가리키지 않는다." \
                 "$SECRET_HELP" ;;
  NOTOWNER)
    verdict_fail "서버 비밀 파일 ~/$SECRET 의 소유자가 배포 계정이 아니다." "$SECRET_HELP" ;;
  MODE\ [0-7]*)
    verdict_fail "서버 비밀 파일 ~/$SECRET 의 권한이 ${SECRET_STATE#MODE } 이다 — 600(소유자만 읽기·쓰기)이어야 한다." \
                 "chmod 600 ~/$SECRET 뒤 다시 실행한다 — 절차는 README '운영 (P6) → 비밀 파일'." ;;
  *)   # ssh 실패·stat 실패(빈 MODE)
    verdict_fail "서버 비밀 파일 ~/$SECRET 를 확인하지 못했다(ssh 실패, 응답='$SECRET_STATE') — 비밀 파일이 준비됐는지 판정할 수 없다." \
                 "$SECRET_HELP" ;;
esac

# ⑤ 빌드 락 — 원격에서 비차단으로 잡고 LOCKED 를 찍은 뒤 stdin 이 닫힐 때까지 쥔다. 못 잡으면 BUSY.
BUILD_LOCK="${QL_BUILD_LOCK_FILE:-/tmp/quant_ledger_build.lock}"   # QL_BUILD_LOCK_FILE 은 테스트 전용(model_daily.sh 와 같다)
LOCK_CMD="exec 9>$BUILD_LOCK || exit 4; if flock -n 9; then echo LOCKED; cat >/dev/null; else echo BUSY; fi"
LOCK_STATE=""
if [ "$APPLY" -eq 1 ]; then
  # ssh 하나가 락을 쥔 채 FIFO 로 받은 stdin 을 기다린다. 이 스크립트가 어떻게 끝나든 FIFO 의 쓰는 쪽
  # (fd 8)이 닫히면 원격 cat 이 끝나 락이 풀린다 — 배포가 중간에 죽어도 서버에 락이 남지 않는다.
  LOCK_DIR="$(mktemp -d)"
  mkfifo "$LOCK_DIR/in" "$LOCK_DIR/out"
  ssh "$REMOTE" "$LOCK_CMD" <"$LOCK_DIR/in" >"$LOCK_DIR/out" &
  LOCK_PID=$!
  exec 8>"$LOCK_DIR/in" 7<"$LOCK_DIR/out"
  read -r LOCK_STATE <&7 || true
else
  LOCK_STATE="$(ssh "$REMOTE" "$LOCK_CMD" </dev/null || true)"   # stdin 이 비어 잡자마자 놓는다
fi
case "$LOCK_STATE" in
  LOCKED)
    if [ "$APPLY" -eq 1 ]; then
      echo "== 빌드 락: $REMOTE:$BUILD_LOCK 비어 있음 — rsync·DEPLOYED.json 기록을 마칠 때까지 쥔다"
    else
      echo "== 빌드 락: $REMOTE:$BUILD_LOCK 비어 있음"
    fi ;;
  BUSY)
    verdict_fail "서버 빌드 락($REMOTE:$BUILD_LOCK)을 다른 작업이 쥐고 있다 — 다른 빌드·배포가 실행 중이다." \
                 "기다리지 않는다(P9). 그 빌드·배포가 끝난 뒤 다시 실행한다." ;;
  *)
    verdict_fail "서버 빌드 락($REMOTE:$BUILD_LOCK)을 확인하지 못했다(ssh 실패 또는 락 파일 열기 실패, 응답='$LOCK_STATE') — 체인과 겹치는지 판정할 수 없다." ;;
esac

# ⑦ 서버 rev — 서버 DEPLOYED.json 의 rev 가 HEAD 의 조상이 아니면 그 판에만 있는 커밋(핫픽스)이 되돌아간다
SERVER_REV=""; ROLLBACK_FROM=""
if ! SERVER_META="$(ssh "$REMOTE" "cat $ROOT/DEPLOYED.json")"; then
  verdict_fail "서버 ~/$ROOT/DEPLOYED.json 을 읽지 못했다 — 서버 판이 HEAD=$BRANCH ($REV) 에 들어 있는지 판정할 수 없다." \
               "서버 ~/$ROOT/DEPLOYED.json 을 마지막 배포 rev 로 되살린 뒤 다시 실행한다."
else
  SERVER_REV="$(printf '%s\n' "$SERVER_META" \
    | sed -n 's/.*"rev"[[:space:]]*:[[:space:]]*"\([0-9a-f]\{7,40\}\)".*/\1/p')"
  if [ -z "$SERVER_REV" ]; then
    verdict_fail "서버 ~/$ROOT/DEPLOYED.json 에서 rev(16진 7~40자)를 읽지 못했다 — 서버 판이 HEAD=$BRANCH ($REV) 에 들어 있는지 판정할 수 없다." \
                 "서버 ~/$ROOT/DEPLOYED.json 을 마지막 배포 rev 로 되살린 뒤 다시 실행한다." "내용: $SERVER_META"
  elif git -C "$REPO" merge-base --is-ancestor "$SERVER_REV" HEAD 2>/dev/null; then
    echo "== 서버 rev 확인: 서버 $SERVER_REV 가 HEAD=$BRANCH ($REV) 의 조상이다 — 되돌리는 커밋 없음"
  elif [ "$ALLOW_ROLLBACK" = "$SERVER_REV" ]; then
    ROLLBACK_FROM="$SERVER_REV"
    echo "== 되돌림 허용: 서버 $SERVER_REV 가 HEAD=$BRANCH ($REV) 에 없지만 --allow-rollback 으로 덮는다 — DEPLOYED.json 에 rollback_from 으로 남긴다"
  else
    WHY=""
    git -C "$REPO" cat-file -e "$SERVER_REV^{commit}" 2>/dev/null \
      || WHY=" (이 저장소에 그 커밋이 없다 — 다른 브랜치·다른 작업 사본에서 민 판일 수 있다)"
    [ -z "$ALLOW_ROLLBACK" ] || WHY="$WHY (--allow-rollback $ALLOW_ROLLBACK 은 서버 rev 와 다르다)"
    verdict_fail "서버 rev $SERVER_REV 가 HEAD=$BRANCH ($REV) 에 들어 있지 않다$WHY — 그대로 밀면 서버에만 있는 커밋(핫픽스)이 --delete 로 되돌아간다." \
                 "먼저 그 rev 를 이 브랜치에 병합한다. 의도한 되돌림이라면: deploy.sh --apply --allow-rollback $SERVER_REV"
  fi
fi

# ⑥ 무엇이 바뀌는지 먼저 센다. rsync 를 한 번 더 부르는 값으로, apply 모드에서도 계획을 남긴다.
# `-c`(체크섬)로 재는 이유: 워크트리 체크아웃은 mtime 이 전부 다르다 — 크기·시각 비교로 세면
# "바뀐다" 가 214개로 나와 진짜 되돌아가는 파일이 묻힌다.
PLAN="$(mktemp)"
plan_of() {   # 항목별 dry-run 계획을 $PLAN 에 모으고 변경 파일 수를 반환한다
  local label="$1"; shift
  local out; out="$(rsync -ainc "$@" 2>/dev/null || true)"
  printf '── %s\n%s\n' "$label" "$out" >> "$PLAN"
  # 첫 글자가 `.` 인 줄은 내용이 같고 속성만 손보는 것이라 세지 않는다.
  printf '%s\n' "$out" | grep -cE '^([<>ch][fdLDS]|\*deleting)' || true
}
N_SRC=$(plan_of "src/" "${COMMON[@]}" "${SRC_KEEP[@]}" --delete "$REPO/src/" "$REMOTE:$ROOT/src/")
N_SCRIPTS=$(plan_of "scripts/" "${COMMON[@]}" --delete "$REPO/scripts/" "$REMOTE:$ROOT/scripts/")
# 모델 레지스트리(config/models/*.toml, M2) — 코드와 같은 rev 로 나가야 판의 spec_id 가 코드와 맞는다
N_CONFIG=$(plan_of "config/" "${COMMON[@]}" --delete "$REPO/config/" "$REMOTE:$ROOT/config/")
N_ENGINE=$(plan_of "_engine/strategy_workbench/" "${COMMON[@]}" --delete "$ENGINE_SRC/" "$REMOTE:$ROOT/_engine/strategy_workbench/")
N_SHARE=$(plan_of "src/rebuild_share.py" "$WORKTREE/backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py")
echo "════ 내용이 바뀔 파일 $((N_SRC + N_SCRIPTS + N_CONFIG + N_ENGINE + N_SHARE))개 (src $N_SRC · scripts $N_SCRIPTS · config $N_CONFIG · _engine $N_ENGINE · rebuild_share $N_SHARE) — $BRANCH $REV tests=$TESTS ════"
cat "$PLAN"

echo "== src/  ($REPO/src/ → $REMOTE:~/$ROOT/src/)"
rsync -avz $DRY --delete "${COMMON[@]}" "${SRC_KEEP[@]}" \
      "$REPO/src/" "$REMOTE:$ROOT/src/"

echo "== scripts/  ($REPO/scripts/ → $REMOTE:~/$ROOT/scripts/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$REPO/scripts/" "$REMOTE:$ROOT/scripts/"

echo "== config/  ($REPO/config/ → $REMOTE:~/$ROOT/config/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$REPO/config/" "$REMOTE:$ROOT/config/"

# equity contract 대조 대상. 서버 경로는 `equity contract --engine-src ~/quant-ledger/_engine` 이 읽는다.
# 옛 `_engine/backtest_engine/` 은 이제 아무도 읽지 않는다 — 이 배포는 그 디렉터리를 건드리지 않는다.
echo "== _engine/strategy_workbench/  (backend/src/strategy_workbench/ → $REMOTE:~/$ROOT/_engine/strategy_workbench/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$ENGINE_SRC/" "$REMOTE:$ROOT/_engine/strategy_workbench/"

# 워크벤치 공유 산출물 재생성기. 저장소 정본은 backend/ops/ 이고 서버는 src/ 에 두고 쓴다.
echo "== backend/ops/rebuild_share.py → src/rebuild_share.py"
rsync -avz $DRY "$WORKTREE/backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py"

if [ "$APPLY" -eq 1 ]; then
  # ④ 서버에 무엇을 언제 누가 밀었는지 남긴다 — 드리프트 조사의 출발점(D05). 알고 되돌렸으면(⑦) 덮인 rev 도.
  ROLLBACK_JSON=""
  [ -z "$ROLLBACK_FROM" ] || ROLLBACK_JSON=",\"rollback_from\":\"$ROLLBACK_FROM\""
  printf '{"rev":"%s","branch":"%s","at_utc":"%s","by":"%s","tests":"%s"%s}\n' \
    "$REV" "$BRANCH" "$(date -u +%FT%TZ)" "${USER:-unknown}@$(hostname -s 2>/dev/null || echo unknown)" "$TESTS" \
    "$ROLLBACK_JSON" \
    | ssh "$REMOTE" "cat > $ROOT/DEPLOYED.json"
  echo "== DEPLOYED.json 기록: rev=$REV branch=$BRANCH tests=$TESTS${ROLLBACK_FROM:+ rollback_from=$ROLLBACK_FROM}"
else
  echo
  echo "(dry-run 이었다. 실제로 밀려면 --apply)"
fi
