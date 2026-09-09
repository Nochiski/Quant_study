#!/usr/bin/env bash
# v3 의 KIS 휴장일 캐시(~/kael-system-v3/data/.kis_holidays.json)를 quant-ledger 로 복사 + 검증.
# 플랜 P0 Task 0.3 / 결정 R7. 검증 실패면 이전 복사본을 그대로 두고 warn 알림, exit 1.
#   형식(실측 09-09): {"year": 2026, "fetched_at", "last_reviewed_at", "holidays": ["20260101", ...121건], "review_history"}
#   검증 규칙은 v3 holiday.py 와 같다 — 100건 이상 · 전건 해당 연도 · 주말(토·일) 90건 이상.
set -uo pipefail
cd "${QL_HOME:-$HOME/quant-ledger}"
SRC="${1:-$HOME/kael-system-v3/data/.kis_holidays.json}"
DST=data/calendar/kis_holidays.json
mkdir -p data/calendar
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
if ! cp "$SRC" "$TMP" 2>/dev/null; then
  scripts/notify.sh warn "캘린더 동기화 실패" "$SRC 를 읽을 수 없다 — 이전 복사본($DST) 유지"
  exit 1
fi
if .venv/bin/python - "$TMP" <<'PY'
import datetime as dt
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
year = int(d["year"])
hol = [str(x) for x in d["holidays"]]
assert len(hol) >= 100, f"too few holidays: {len(hol)}"
assert all(len(x) == 8 and x[:4] == str(year) for x in hol), "holiday outside declared year"
weekend = sum(1 for x in hol if dt.date(int(x[:4]), int(x[4:6]), int(x[6:8])).weekday() >= 5)
assert weekend >= 90, f"weekend entries {weekend} < 90"
print(f"calendar ok: year={year} holidays={len(hol)} weekend={weekend} fetched_at={d.get('fetched_at')}")
PY
then
  mv "$TMP" "$DST"
  trap - EXIT
else
  scripts/notify.sh warn "캘린더 검증 실패" "$SRC 형식·건수 이상 — 이전 복사본($DST) 유지"
  exit 1
fi
