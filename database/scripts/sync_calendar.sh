#!/usr/bin/env bash
# v3 의 KIS 휴장일 캐시(~/kael-system-v3/data/.kis_holidays.json)를 quant-ledger 로 복사 + 검증.
# 플랜 P0 Task 0.3 / 결정 R7. 검증 실패면 이전 복사본을 그대로 두고 warn 알림, exit 1.
#   형식(실측 09-09): {"year": 2026, "fetched_at", "last_reviewed_at", "holidays": ["20260101", ...121건], "review_history"}
#   검증 규칙은 v3 holiday.py 와 같다 — 100건 이상 · 전건 해당 연도 · 주말(토·일) 90건 이상.
#   원천은 단일 연도라 이듬해 판으로 교체되면 그해 휴장일이 사라진다(DEFECT-A07) — 그래서 응답의 year 로
#   `kis_holidays_<year>.json` 에 쌓고 `kis_holidays.json` 은 옛 경로 호환용으로 계속 갱신한다.
#   daily.calendar.load() 는 디렉터리의 `kis_holidays*.json` 을 합쳐 읽는다.
set -uo pipefail
cd "${QL_HOME:-/home/kael/quant-ledger}"
SRC="${1:-/home/kael/kael-system-v3/data/.kis_holidays.json}"
DST=data/calendar/kis_holidays.json
mkdir -p data/calendar
# 같은 파일시스템에 만들어야 마지막 mv 가 rename(2) 원자 교체가 된다(리뷰 REC-8)
TMP=$(mktemp data/calendar/.sync.XXXXXX)
trap 'rm -f "$TMP"' EXIT
if ! cp "$SRC" "$TMP" 2>/dev/null; then
  scripts/notify.sh warn "캘린더 동기화 실패" "$SRC 를 읽을 수 없다 — 이전 복사본($DST) 유지"
  exit 1
fi
if OUT=$(.venv/bin/python - "$TMP" <<'PY'
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
)
then
  echo "$OUT"
  YEAR=$(printf '%s\n' "$OUT" | sed -n 's/^calendar ok: year=\([0-9]\{4\}\).*/\1/p')
  if [ -z "$YEAR" ]; then
    scripts/notify.sh warn "캘린더 검증 실패" "$SRC 에서 연도를 읽지 못했다 — 이전 복사본($DST) 유지"
    exit 1
  fi
  # cp 는 truncate 뒤 재기록이라 중간에 죽으면 절단된 JSON 이 남고 calendar.load() 가 weekend_only 로
  # 조용히 폴백한다 — 임시 파일에 쓰고 rename 으로 교체한다. 실패는 rc 1 + warn(리뷰 REC-8).
  YEAR_TMP=$(mktemp data/calendar/.sync_year.XXXXXX)
  if ! { cp "$TMP" "$YEAR_TMP" && chmod 600 "$YEAR_TMP" && mv -f "$YEAR_TMP" "data/calendar/kis_holidays_${YEAR}.json"; }; then
    rm -f "$YEAR_TMP"
    scripts/notify.sh warn "캘린더 저장 실패" "data/calendar/kis_holidays_${YEAR}.json 을 쓰지 못했다 — 이전 파일 유지"
    exit 1
  fi
  chmod 600 "$TMP"
  mv -f "$TMP" "$DST"
  trap - EXIT
else
  scripts/notify.sh warn "캘린더 검증 실패" "$SRC 형식·건수 이상 — 이전 복사본($DST) 유지"
  exit 1
fi
