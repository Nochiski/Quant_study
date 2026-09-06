#!/usr/bin/env bash
# 서버 equity 층을 로컬로 내려받아 워크벤치·백테스트가 읽을 수 있게 만든다.
#
#   database/scripts/fetch_equity_local.sh <로컬 경로> [minimal|full]
#
# minimal(기본) = 가격·조정가·유니버스·조정계수·식별 표만 (약 2.0GB)
# full          = 어댑터가 읽는 19개 표 전부 (약 3.3GB)
#
# `_pinned/`(재빌드용 원장 고정본)·`_asof/`·`_tmp/`·`_failed/` 는 읽기에 불필요하므로 받지 않는다.
# 카탈로그(`equity.duckdb`)의 매크로는 **절대경로**를 굽고 있어(DESIGN §10 P1c) 경로가 바뀌면
# 재무·컨센서스 필드가 통째로 unavailable 이 된다 → 내려받은 뒤 반드시 다시 만든다.
# (조정 종가는 S23 부터 표 `price_adj_daily` 를 직접 읽으므로 카탈로그와 무관하다.)
set -euo pipefail

DEST="${1:?사용법: fetch_equity_local.sh <로컬 경로> [minimal|full]}"
MODE="${2:-minimal}"
REMOTE="${EQUITY_REMOTE:-kael-server}"
REMOTE_ROOT="${EQUITY_REMOTE_ROOT:-~/quant-ledger/data/equity}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MINIMAL=(price_daily price_adj_daily universe_daily universe_policy adj_factor corp_event
         security security_span corp_ticker trading_calendar)
EXTRA=(fin_std disclosure_version consensus_daily opinion_daily
       flow_daily short_daily credit_daily dividend_event holder_daily)

case "$MODE" in
  minimal) TABLES=("${MINIMAL[@]}") ;;
  full)    TABLES=("${MINIMAL[@]}" "${EXTRA[@]}") ;;
  *) echo "모드는 minimal 또는 full: $MODE" >&2; exit 2 ;;
esac

mkdir -p "$DEST"
echo "== 내려받기 ${#TABLES[@]}개 표 → $DEST (모드 $MODE)"
for t in "${TABLES[@]}"; do
  printf '  %-22s' "$t"
  rsync -a --info=none "$REMOTE:$REMOTE_ROOT/$t/" "$DEST/$t/"
  du -sh "$DEST/$t" | cut -f1
done

echo "== baseline·카탈로그 메타"
rsync -a "$REMOTE:$REMOTE_ROOT/baseline.json" "$DEST/baseline.json"

echo "== 카탈로그 재생성 (매크로 경로가 절대경로라 필수)"
PYTHONPATH="$REPO_ROOT/database/src" python -m equity \
  --root "$DEST" --stage-root "$DEST" --baseline "$DEST/baseline.json" catalog

echo
echo "완료: $(du -sh "$DEST" | cut -f1)"
echo "워크벤치 기동:"
echo "  export STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb"
echo "  export STRATEGY_WORKBENCH_EQUITY_ROOT=$DEST"
echo "  cd backend && uv run --extra parquet --extra equity server"
