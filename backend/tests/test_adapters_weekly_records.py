"""S07 실행 기록 파일. 임시 폴더에 직접 만든 기록만 쓴다."""

from __future__ import annotations

import json
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from strategy_workbench.adapters.outbound.rebalance_files.facade.plans import JsonExecutionLog
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    DayOrder,
    DayRecord,
    ExecutionEvent,
)

KST = timezone(timedelta(hours=9))
DAY = date(2026, 10, 26)


def _record(equity: int) -> DayRecord:
    order = DayOrder(
        "p-A-buy",
        "00000A",
        "이름A",
        "buy",
        130,
        50,
        501_000,
        1_750,
        "partial",
        10_000,
        10_100,
        20,
        8_000,
    )
    return DayRecord(
        DAY,
        datetime(2026, 10, 26, 15, 40, tzinfo=KST),
        "p",
        True,
        "ok",
        1_000,
        (("00000A", 50, 10_100),),
        equity,
        501_000,
        (order,),
    )


def test_events_append_and_day_records_replace(tmp_path: Path) -> None:
    log = JsonExecutionLog(tmp_path)
    assert log.events() == () and log.days() == () and log.day(DAY) is None
    event = ExecutionEvent(
        datetime(2026, 10, 26, 9, 10, tzinfo=KST),
        "p",
        "open",
        "submit",
        "00000A",
        "p-A-buy",
        "buy",
        130,
        10_020,
        10_000,
        (10_010, 10_020),
        (10_000,),
        "sent",
        "",
    )
    log.append(event)
    log.append(event)
    assert log.events() == (event, event)
    log.save_day(_record(506_000))
    log.save_day(_record(507_000))  # 다시 대사하면 갈아 끼운다
    assert log.days() == (_record(507_000),) and log.day(DAY) == _record(507_000)
    log.save_raw(DAY, {"kt00007": [{"ord_no": "0000001"}]})
    raw = tmp_path / "records" / "raw-2026-10-26.json"
    assert json.loads(raw.read_text())["kt00007"] == [{"ord_no": "0000001"}]
    for path in (tmp_path / "records").iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    assert sorted(p.name for p in (tmp_path / "records").iterdir()) == [
        "day-2026-10-26.json",
        "events.jsonl",
        "raw-2026-10-26.json",
    ]
