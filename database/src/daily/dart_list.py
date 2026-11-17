"""공시 목록(list.json) 스윕의 대조 기록 읽기 — "그 접수일의 공시 목록을 DART 가 가진 만큼 다 받았는가".

쓰는 곳은 둘이고 같은 판정을 쓴다:
  · 일일 DART 완료 판정 `daily.dart_daily` 의 `filings` 관문(18:05 저녁 · 06:00 새벽 런)
  · 08:10 원장 건전성 `daily.ledger_health` 의 `dart.disclosure.rows`

근거는 스윕(`sweep_disclosure.py`)이 창(표준 분기)마다 `ingest_log`(name='disclosure') 에 남기는 상태다.
  ok · open   page 1..total_page 를 다 받았고 수신 합계 = DART `total_count`, 적재 고유 건수 ≥ `total_count`
              (ok = 끝난 창, open = 진행 중인 창)
  no_data     013 — 그 창에 공시가 없다(DART 의 확정 답)
  partial     페이지 순회 중이거나 도중에 멈췄다(키 소진 · 콜 상한 · 응답 오류)
  mismatch    대조가 어긋났다(페이징 누락 · 적재 유실)
스윕은 partial · mismatch 로 끝나도 종료 코드가 0 이라 이 기록 말고는 알 방법이 없다.

건수의 많고 적음은 보지 않는다(2026-11-17). 옛 판정은 '접수일 D 공시 400건 이상 · 상장사 290건 이상'이었다.
  · 분기보고서 기한 다음 거래일(11-17)의 334건이 걸려 저녁 판이 그날 공시 없이 나갔다 — 스윕은 정상이었다.
    2019 년 이후 평일 1,940일 중 61일이 400건 미만이다.
  · 반대로 바쁜 날 목록을 도중까지만 받아도 400건만 넘으면 통과했다.
건수 판정은 열린 분기 창이 늘 mismatch 로 남던 때(DEFECT-B01)의 우회였고, 그 결함은 2026-09-10 에 고쳐졌다
(원장 고유 건수가 `total_count` 보다 많은 것은 DART 측 삭제로 보고 통과).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass

SWEEP_NAME = "disclosure"                       # `sweep_disclosure.NAME` — 드리프트는 테스트가 잡는다
RECONCILED: tuple[str, ...] = ("ok", "open", "no_data")   # 대조를 통과하고 끝난 상태
_TS = "%Y-%m-%dT%H:%M:%S"                       # `ingest_log.ts` — Z 없는 UTC
_KST = dt.timezone(dt.timedelta(hours=9))


@dataclass(frozen=True)
class ListSweep:
    """접수일 D 가 든 표준 분기 창의 마지막 스윕 기록과 판정."""

    ok: bool
    window: tuple[str, str]          # (창 시작, 창 끝) YYYYMMDD
    since_ts: str                    # 기록 시각이 이 시각 이후여야 한다(Z 없는 UTC)
    status: str | None = None        # 기록이 없으면 None
    ts: str | None = None            # 기록 시각(Z 없는 UTC)
    total_count: int | None = None   # DART 가 알려준 창 전체 건수

    def describe(self) -> str:
        return (f"list sweep window={self.window[0]}-{self.window[1]} status={self.status or 'none'} "
                f"ts={self.ts or '-'} (need one of {'/'.join(RECONCILED)} at or after {self.since_ts}) "
                f"total_count={'-' if self.total_count is None else self.total_count}")


def quarter_window(date_yyyymmdd: str) -> tuple[str, str]:
    """D 가 든 표준 분기 창 — 스윕의 창 id(`ingest_log.bsns_year` · `reprt_code`)와 같다."""
    y, m = int(date_yyyymmdd[:4]), int(date_yyyymmdd[4:6])
    first = 3 * ((m - 1) // 3) + 1
    end = dt.date(y + (first + 2) // 12, (first + 2) % 12 + 1, 1) - dt.timedelta(days=1)
    return f"{y}{first:02d}01", end.strftime("%Y%m%d")


def day_start_utc(date_yyyymmdd: str) -> str:
    """D 00:00 KST 를 `ingest_log.ts` 와 같은 형식(Z 없는 UTC)으로."""
    d = dt.datetime(int(date_yyyymmdd[:4]), int(date_yyyymmdd[4:6]), int(date_yyyymmdd[6:8]),
                    tzinfo=_KST)
    return d.astimezone(dt.UTC).strftime(_TS)


def list_sweep(con: sqlite3.Connection, date_yyyymmdd: str, *,
               since_ts: str | None = None) -> ListSweep:
    """D 가 든 분기 창의 마지막 스윕이 대조를 통과했고(`RECONCILED`) 그 기록이 `since_ts` 이후인가.

    `since_ts` 는 "언제 이후의 스윕이어야 하는가"다. 이번 런이 스윕을 돌렸으면 그 스윕의 시작 시각을 준다 —
    앞선 런의 기록으로 통과하지 않는다. 주지 않으면 D 00:00 KST — D 당일이나 그 뒤에 대조를 통과한 스윕이
    있으면 된다(스윕 없는 재실행 · 다음 날 08:10 원장 건전성).
    """
    window = quarter_window(date_yyyymmdd)
    since = since_ts or day_start_utc(date_yyyymmdd)
    has_log = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingest_log'").fetchone()
    row = None if has_log is None else con.execute(
        "SELECT status, ts, note FROM ingest_log WHERE name=? AND corp_code='' "
        "AND bsns_year=? AND reprt_code=? AND fs_div=''", (SWEEP_NAME, *window)).fetchone()
    if row is None:
        return ListSweep(False, window, since)
    status, ts, note = str(row[0]), str(row[1]), row[2]
    try:
        total = json.loads(note).get("total_count") if note else None
    except (ValueError, AttributeError):
        total = None
    return ListSweep(status in RECONCILED and ts >= since, window, since, status, ts,
                     int(total) if isinstance(total, int) else None)
