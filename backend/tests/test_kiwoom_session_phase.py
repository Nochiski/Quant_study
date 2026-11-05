"""키움 실시간 장운영(`0s`) 기록 → 오늘 정규장 상태(S07, 2026-11-05).

기록 한 줄은 체결 기록기가 받은 `0s` 항목 하나다. 수능일(개장 10:00·마감 16:30)과 새해 첫 거래일
(개장 10:00)처럼 장 시각이 바뀌는 날을 시각 표 없이 알아차리는 근거다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.session import (
    KiwoomSessionLog,
    session_log_path,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import MarketPhase

KST = timezone(timedelta(hours=9))
DAY = date(2026, 11, 19)


def _at(hhmm: str) -> datetime:
    return datetime.combine(DAY, datetime.strptime(hhmm, "%H:%M").time(), KST)


def _row(hhmm: str, code: str, remaining: str | None = None) -> str:
    values = {"215": code, "20": hhmm.replace(":", "") + "00"}
    if remaining is not None:
        values["214"] = remaining
    entry = {"type": "0s", "name": "장시작시간", "item": "", "values": values}
    return json.dumps({"at": _at(hhmm).isoformat(), "kind": "0s", "msg": entry}) + "\n"


def _phase(tmp_path: Path, rows: str, now: str) -> MarketPhase:
    session_log_path(tmp_path, DAY).write_text(rows)
    return KiwoomSessionLog(tmp_path).phase(_at(now))


def test_no_record_for_the_day_is_unknown(tmp_path: Path) -> None:
    # 기록기가 돌지 않았거나 모의 서버가 장운영을 보내지 않는 날 — 예약 실행은 정해진 시각에 돈다
    session_log_path(tmp_path, DAY - timedelta(days=1)).write_text(_row("15:30", "8"))
    assert KiwoomSessionLog(tmp_path).phase(_at("09:05")) is MarketPhase.UNKNOWN


def test_a_late_opening_is_before_open_until_the_announced_start(tmp_path: Path) -> None:
    # 수능일: 08:55 장시작전 알림이 개장까지 1시간 5분 남았다고 알린다 — 09:05 주문은 장 시작 전이다
    rows = _row("08:55", "0", "010500") + _row("08:56", "0", "010400")
    assert _phase(tmp_path, rows, "09:05") is MarketPhase.BEFORE_OPEN
    assert _phase(tmp_path, rows, "09:59") is MarketPhase.BEFORE_OPEN
    # 장시작 알림을 놓쳐도(끊김) 알린 개장 시각이 지나면 열린 것으로 본다 — 오전 내내 막히지 않게
    assert _phase(tmp_path, rows, "10:00") is MarketPhase.OPEN


def test_a_pre_open_notice_without_remaining_time_expires_after_two_minutes(
    tmp_path: Path,
) -> None:
    # 장시작전 알림은 1분마다 온다. 남은 시간을 못 읽으면 알림이 끊긴 지 2분 뒤 열린 것으로 본다
    rows = _row("08:58", "0", "")
    assert _phase(tmp_path, rows, "08:59") is MarketPhase.BEFORE_OPEN
    assert _phase(tmp_path, rows, "09:00") is MarketPhase.OPEN


@pytest.mark.parametrize(("hhmm", "code"), [("09:00", "3"), ("15:20", "2")])
def test_start_or_closing_auction_notices_mean_open(tmp_path: Path, hhmm: str, code: str) -> None:
    # 장시작전 알림 없이 그 신호만 있어도 열린 것이다(기록기가 장중에 다시 붙은 날)
    assert _phase(tmp_path, _row(hhmm, code), hhmm) is MarketPhase.OPEN


@pytest.mark.parametrize("code", ["4", "8", "a"])
def test_close_notices_mean_the_regular_session_ended(tmp_path: Path, code: str) -> None:
    rows = _row("10:00", "3") + _row("16:20", "2") + _row("16:30", code)
    assert _phase(tmp_path, rows, "16:31") is MarketPhase.CLOSED


def test_futures_and_nxt_notices_are_ignored(tmp_path: Path) -> None:
    # 선옵 장시작(o)·NXT 메인마켓 종료(S)는 KRX 주식 정규장과 무관하다
    rows = _row("08:45", "o") + _row("15:20", "S")
    assert _phase(tmp_path, rows, "15:40") is MarketPhase.UNKNOWN
    rows = _row("10:00", "3") + _row("15:20", "S")
    assert _phase(tmp_path, rows, "15:40") is MarketPhase.OPEN


def test_a_half_written_last_line_is_left_for_the_next_read(tmp_path: Path) -> None:
    rows = _row("10:00", "3") + _row("16:30", "8")[:20]
    assert _phase(tmp_path, rows, "16:30") is MarketPhase.OPEN
    with pytest.raises(ValueError, match="line=1"):
        _phase(tmp_path, "{broken\n" + _row("10:00", "3"), "10:01")
