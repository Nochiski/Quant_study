#!/usr/bin/env bash
# DART 회사 정보 공백 메우기 — 유니버스(dart_corp_map)에는 있는데 dart_company 가 한 번도 수집되지 않은
# corp_code 를 찾아 `backfill_dart.py --only company` 로 받는다. 2026-09-12 확정 빌드에서 신규 상장사 5곳
# (기도산업·스카이랩스·니어스랩·해치텍·한화머시너리앤서비스홀딩스)이 이 공백으로 equity `corp` EG3 를 깨뜨렸다.
#   사용: dart_company_gap.sh [--dry-run]     (06:00 daily_ledger.sh 의 DART 단계 뒤에서 부른다)
#   비용: 공백 corp 당 DART 1콜. 평소 0~수 건.
set -uo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
export QL_HOME="${QL_HOME:-/home/kael/quant-ledger}" PYTHONPATH="${PYTHONPATH:-/home/kael/quant-ledger/src}"
PY=.venv/bin/python
DRY=""; [ "${1:-}" = "--dry-run" ] && DRY=1
MISSING=$(sqlite3 "file:data/raw/dart.db?mode=ro" \
  "SELECT group_concat(corp_code) FROM (SELECT DISTINCT m.corp_code FROM dart_corp_map m
     LEFT JOIN dart_company c ON c.corp_code = m.corp_code WHERE c.corp_code IS NULL
     AND nullif(trim(m.corp_code), '') IS NOT NULL ORDER BY m.corp_code)")
if [ -z "$MISSING" ]; then
  echo "[dart_company_gap] 공백 0건 — dart_corp_map 의 모든 corp 에 dart_company 가 있다"
  exit 0
fi
N=$(echo "$MISSING" | tr ',' '\n' | wc -l | tr -d ' ')
echo "[dart_company_gap] dart_company 공백 ${N}건: $MISSING"
if [ -n "$DRY" ]; then echo "[dart_company_gap] dry-run — 호출 생략"; exit 0; fi
$PY src/backfill_dart.py --corps "$MISSING" --only company --quota-window midnight
rc=$?
LEFT=$(sqlite3 "file:data/raw/dart.db?mode=ro" \
  "SELECT count(*) FROM (SELECT DISTINCT m.corp_code FROM dart_corp_map m
     LEFT JOIN dart_company c ON c.corp_code = m.corp_code WHERE c.corp_code IS NULL
     AND nullif(trim(m.corp_code), '') IS NOT NULL)")
echo "[dart_company_gap] backfill rc=$rc · 남은 공백 ${LEFT}건"
[ "$rc" -eq 0 ] && [ "$LEFT" -eq 0 ] && exit 0
exit 1
