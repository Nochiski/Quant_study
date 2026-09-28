#!/usr/bin/env bash
# quant-ledger 코드 배포 — 저장소 database/{src,scripts} 를 서버 ~/quant-ledger/{src,scripts} 로
# 밀어 넣는다. **정본은 저장소**이고 서버는 사본이다(P0 Task 0.9 판정).
#
#   database/scripts/deploy.sh                                   # dry-run (기본, 아무것도 안 바꾼다)
#   database/scripts/deploy.sh --apply                           # 실제 배포
#   database/scripts/deploy.sh --apply --allow-branch <브랜치>    # main 미머지 브랜치를 알고 민다
#   database/scripts/deploy.sh --apply --skip-tests              # 테스트 생략(사유는 DEPLOYED.json 에 남는다)
#
# --delete 를 쓰는 이유: 서버에만 남은 작업 사본(equity_s23/ 같은 것)이 다음 사람에게
# "둘 중 어느 쪽이 정본인가" 를 다시 묻게 만든다. 저장소에 없으면 서버에도 없어야 한다.
#
# --apply 전 검사(DEFECT-B05·C01·D05: 배포 한 번이 서버의 운영 시간표·핫픽스를 조용히 되돌린 사고):
#   ① 작업 트리가 깨끗해야 한다 — 어느 커밋을 밀었는지 서버에 적을 수 없으면 드리프트를 추적할 수 없다
#   ② HEAD 가 origin/main 을 포함해야 한다. 미머지 브랜치는 `--allow-branch <그 브랜치 이름>` 으로만 허용
#   ③ database/tests 전량 통과(`--skip-tests` 로 생략 가능 — 생략 사실이 서버에 기록된다)
#   ④ 배포 결과를 서버 ~/quant-ledger/DEPLOYED.json 에 남긴다(rev·branch·at_utc·by·tests)
# 서버에는 pytest·ruff 가 없다(맨 pip venv) — 검사는 이 저장소 쪽에서만 돈다.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # …/database
WORKTREE="$(cd "$REPO/.." && pwd)"                           # 저장소 루트(backend/ 가 있는 곳)
REMOTE="${QL_REMOTE:-kael-server}"
ROOT="${QL_REMOTE_ROOT:-quant-ledger}"                       # 원격 홈 기준 상대경로

APPLY=0
SKIP_TESTS=0
ALLOW_BRANCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --allow-branch) ALLOW_BRANCH="${2:?--allow-branch 뒤에 브랜치 이름이 필요하다}"; shift 2 ;;
    *) echo "usage: deploy.sh [--apply|--dry-run] [--allow-branch <name>] [--skip-tests]" >&2; exit 2 ;;
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

# equity contract(EGC) 가 대조하는 엔진 사본. 서버 `_engine/backtest_engine` 은 2026-09-05 판에서
# 멈춰 있었고 갱신 경로가 어디에도 없었다(DEFECT-C05) — 배포가 같이 민다.
ENGINE_SRC="$WORKTREE/backend/src/backtest_engine"
[ -d "$ENGINE_SRC" ] || { echo "거부: $ENGINE_SRC 가 없다 — 엔진 사본을 밀 수 없다." >&2; exit 2; }
# 비었거나 sparse-checkout 인 소스를 --delete 로 밀면 서버 _engine 이 전멸하고 contract 는 기록형이라 묻힌다(리뷰 REC-9)
[ -f "$ENGINE_SRC/__init__.py" ] || { echo "거부: $ENGINE_SRC/__init__.py 가 없다 — 빈 소스로 서버 _engine 을 지울 수 없다." >&2; exit 2; }

BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
REV="$(git -C "$REPO" rev-parse HEAD)"
TESTS="ok"

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

# ⑥ 무엇이 바뀌는지 먼저 센다. rsync 를 한 번 더 부르는 값으로, apply 모드에서도 계획을 남긴다.
# `-c`(체크섬)로 재는 이유: 워크트리 체크아웃은 mtime 이 전부 다르다 — 크기·시각 비교로 세면
# "바뀐다" 가 214개로 나와 진짜 되돌아가는 파일이 묻힌다.
PLAN="$(mktemp)"
trap 'rm -f "$PLAN"' EXIT
plan_of() {   # 항목별 dry-run 계획을 $PLAN 에 모으고 변경 파일 수를 반환한다
  local label="$1"; shift
  local out; out="$(rsync -ainc "$@" 2>/dev/null || true)"
  printf '── %s\n%s\n' "$label" "$out" >> "$PLAN"
  # 첫 글자가 `.` 인 줄은 내용이 같고 속성만 손보는 것이라 세지 않는다.
  printf '%s\n' "$out" | grep -cE '^([<>ch][fdLDS]|\*deleting)' || true
}
N_SRC=$(plan_of "src/" "${COMMON[@]}" "${SRC_KEEP[@]}" --delete "$REPO/src/" "$REMOTE:$ROOT/src/")
N_SCRIPTS=$(plan_of "scripts/" "${COMMON[@]}" --delete "$REPO/scripts/" "$REMOTE:$ROOT/scripts/")
N_ENGINE=$(plan_of "_engine/backtest_engine/" "${COMMON[@]}" --delete "$ENGINE_SRC/" "$REMOTE:$ROOT/_engine/backtest_engine/")
N_SHARE=$(plan_of "src/rebuild_share.py" "$WORKTREE/backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py")
echo "════ 내용이 바뀔 파일 $((N_SRC + N_SCRIPTS + N_ENGINE + N_SHARE))개 (src $N_SRC · scripts $N_SCRIPTS · _engine $N_ENGINE · rebuild_share $N_SHARE) — $BRANCH $REV tests=$TESTS ════"
cat "$PLAN"

echo "== src/  ($REPO/src/ → $REMOTE:~/$ROOT/src/)"
rsync -avz $DRY --delete "${COMMON[@]}" "${SRC_KEEP[@]}" \
      "$REPO/src/" "$REMOTE:$ROOT/src/"

echo "== scripts/  ($REPO/scripts/ → $REMOTE:~/$ROOT/scripts/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$REPO/scripts/" "$REMOTE:$ROOT/scripts/"

# equity contract 대조 대상. 서버 경로는 `equity contract --engine-src ~/quant-ledger/_engine` 이 읽는다.
echo "== _engine/backtest_engine/  (backend/src/backtest_engine/ → $REMOTE:~/$ROOT/_engine/backtest_engine/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$ENGINE_SRC/" "$REMOTE:$ROOT/_engine/backtest_engine/"

# 워크벤치 공유 산출물 재생성기. 저장소 정본은 backend/ops/ 이고 서버는 src/ 에 두고 쓴다.
echo "== backend/ops/rebuild_share.py → src/rebuild_share.py"
rsync -avz $DRY "$WORKTREE/backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py"

if [ "$APPLY" -eq 1 ]; then
  # ④ 서버에 무엇을 언제 누가 밀었는지 남긴다 — 드리프트 조사의 출발점(D05).
  printf '{"rev":"%s","branch":"%s","at_utc":"%s","by":"%s","tests":"%s"}\n' \
    "$REV" "$BRANCH" "$(date -u +%FT%TZ)" "${USER:-unknown}@$(hostname -s 2>/dev/null || echo unknown)" "$TESTS" \
    | ssh "$REMOTE" "cat > $ROOT/DEPLOYED.json"
  echo "== DEPLOYED.json 기록: rev=$REV branch=$BRANCH tests=$TESTS"
else
  echo
  echo "(dry-run 이었다. 실제로 밀려면 --apply)"
fi
