"""키움 실시간 장운영(`0s`) 기록을 오늘 정규장 상태로 읽는다(S07, 2026-11-05 사용자 결정).

수능일(개장 10:00·마감 16:30)·새해 첫 거래일(개장 10:00)처럼 장 시각이 바뀌는 날을 시각 표 없이
알아차리려는 것이다. 기록은 체결 기록기(`_stream.record_fills`)가 같은 웹소켓에서 받아
`records/session-<날짜>.jsonl`에 항목마다 한 줄씩 남긴다. 장운영구분(215) 가운데 KRX 주식 정규장
코드만 본다 — 선물옵션(s·e·f·o)·NXT(P~V) 코드는 무시한다.

- 0 장시작전 알림: 개장 전 1분마다 온다. 214(장시작예상잔여시간 HHmmss)로 개장 시각을 잰다. 남은
  시간을 읽지 못하면 그 알림 뒤 `_PRE_OPEN_EXPIRY`가 지나면 열린 것으로 본다(알림이 끊겼다).
- 3 장시작 · 2 장마감전 알림: 열렸다.
- 4 장마감 · 8 정규장마감: 정규장이 끝났다. 시간외 종가(a·b)·애프터마켓(g·h)·전체장마감(9)은 보지
  않는다 — 장 전 시간외 종가(수능일 09:30~09:40)도 같은 코드로 올 수 있고, 접속 직후 전날 마지막
  상태가 올 수도 있어 정규장 전에 '끝났다'로 읽을 위험이 있다.

장시작 알림을 놓쳐도(끊김) 알린 개장 시각이 지나면 열린 것으로 본다 — 오전 주문이 내내 막히지 않게.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import MarketPhase

SESSION_TYPE = "0s"
PHASE_FIELD = "215"
REMAINING_FIELD = "214"
PRE_OPEN = "0"
OPENED = frozenset({"3", "2"})
CLOSED = frozenset({"4", "8"})
_PRE_OPEN_EXPIRY = timedelta(minutes=2)


def session_log_path(folder: Path, day: date) -> Path:
    """그날 장운영 기록 파일. 기록기와 읽는 쪽이 같은 이름을 쓴다."""
    return folder / f"session-{day.isoformat()}.jsonl"


def phase_code(entry: Mapping[str, object]) -> str | None:
    """`0s` 항목의 장운영구분. 다른 실시간 항목이면 None."""
    values = entry.get("values")
    if entry.get("type") != SESSION_TYPE or not isinstance(values, dict):
        return None
    code = values.get(PHASE_FIELD)
    return code if isinstance(code, str) else None


class KiwoomSessionLog:
    """`MarketSession` 구현. 기록 폴더(`<작업 폴더>/records`)의 그날 파일을 읽는다."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def phase(self, now: datetime) -> MarketPhase:
        path = session_log_path(self._folder, now.date())
        if not path.exists():
            return MarketPhase.UNKNOWN
        opened = closed = False
        pre_open_until: datetime | None = None
        # 기록기가 쓰는 중인 마지막 줄(줄바꿈 전)은 다음에 읽는다
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n")[:-1], 1):
            if not line.strip():
                continue
            at, entry = _row(path, number, line)
            code = phase_code(entry)
            if code in CLOSED:
                closed = True
            elif code in OPENED:
                opened = True
            elif code == PRE_OPEN:
                remaining = _remaining(entry)
                pre_open_until = at + (_PRE_OPEN_EXPIRY if remaining is None else remaining)
        if closed:
            return MarketPhase.CLOSED
        if opened:
            return MarketPhase.OPEN
        if pre_open_until is None:
            return MarketPhase.UNKNOWN
        return MarketPhase.BEFORE_OPEN if now < pre_open_until else MarketPhase.OPEN


def _row(path: Path, number: int, line: str) -> tuple[datetime, Mapping[str, object]]:
    try:
        row = json.loads(line)
        at = datetime.fromisoformat(row["at"])
        entry = row["msg"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            f"session record unreadable — file={path.name} line={number} error={error!r}"
        ) from error
    if not isinstance(entry, dict) or at.tzinfo is None:
        raise ValueError(
            f"session record has no entry or timezone — file={path.name} line={number}"
        )
    return at, entry


def _remaining(entry: Mapping[str, object]) -> timedelta | None:
    values = entry.get("values")
    text = values.get(REMAINING_FIELD) if isinstance(values, dict) else None
    if not isinstance(text, str) or len(text) != 6 or not text.isdigit():
        return None
    return timedelta(hours=int(text[:2]), minutes=int(text[2:4]), seconds=int(text[4:]))
