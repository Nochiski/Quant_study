#!/usr/bin/env bash
# quant-ledger 코드 배포 — 저장소 database/{src,scripts} 를 서버 ~/quant-ledger/{src,scripts} 로
# 밀어 넣는다. **정본은 저장소**이고 서버는 사본이다(P0 Task 0.9 판정).
#
#   database/scripts/deploy.sh            # dry-run (기본, 아무것도 안 바꾼다)
#   database/scripts/deploy.sh --apply    # 실제 배포
#
# --delete 를 쓰는 이유: 서버에만 남은 작업 사본(equity_s23/ 같은 것)이 다음 사람에게
# "둘 중 어느 쪽이 정본인가" 를 다시 묻게 만든다. 저장소에 없으면 서버에도 없어야 한다.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # …/database
REMOTE="${QL_REMOTE:-kael-server}"
ROOT="${QL_REMOTE_ROOT:-quant-ledger}"                       # 원격 홈 기준 상대경로

DRY="--dry-run"
[ "${1:-}" = "--apply" ] && DRY=""

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

echo "== src/  ($REPO/src/ → $REMOTE:~/$ROOT/src/)"
rsync -avz $DRY --delete "${COMMON[@]}" "${SRC_KEEP[@]}" \
      "$REPO/src/" "$REMOTE:$ROOT/src/"

echo "== scripts/  ($REPO/scripts/ → $REMOTE:~/$ROOT/scripts/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$REPO/scripts/" "$REMOTE:$ROOT/scripts/"

# 워크벤치 공유 산출물 재생성기. 저장소 정본은 backend/ops/ 이고 서버는 src/ 에 두고 쓴다.
echo "== backend/ops/rebuild_share.py → src/rebuild_share.py"
rsync -avz $DRY "$REPO/../backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py"

[ -n "$DRY" ] && echo && echo "(dry-run 이었다. 실제로 밀려면 --apply)"
