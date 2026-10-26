"""실행 기록 파일(S07). 계좌 사실이 담겨 600으로 만든다.

- `<폴더>/records/events.jsonl`: 실행 사건을 한 줄씩 덧붙인다.
- `<폴더>/records/day-YYYY-MM-DD.json`: 그날 장 마감 기록. 다시 대사하면 임시 파일로 다 쓴 뒤
  갈아 끼운다.
- `<폴더>/records/raw-YYYY-MM-DD.json`: 그날 장 마감 때 브로커 조회 원본(감사·복기용).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    DayOrder,
    DayRecord,
    ExecutionEvent,
)

DAY_SCHEMA = "weekly-rebalance-day/1"


class JsonExecutionLog:
    def __init__(self, folder: Path) -> None:
        self._folder = folder / "records"

    def append(self, event: ExecutionEvent) -> None:
        self._folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = asdict(event)
        value["at"] = event.at.isoformat()
        line = json.dumps(value, ensure_ascii=False) + "\n"
        fd = os.open(self._folder / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)

    def events(self) -> tuple[ExecutionEvent, ...]:
        path = self._folder / "events.jsonl"
        if not path.exists():
            return ()
        events: list[ExecutionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            value["at"] = datetime.fromisoformat(value["at"])
            value["asks"], value["bids"] = tuple(value["asks"]), tuple(value["bids"])
            events.append(ExecutionEvent(**value))
        return tuple(events)

    def save_day(self, record: DayRecord) -> None:
        value = asdict(record)
        value.update(
            schema=DAY_SCHEMA,
            day=record.day.isoformat(),
            recorded_at=record.recorded_at.isoformat(),
        )
        replace_file(self._path(record.day), value)

    def save_raw(self, day: date, payload: Mapping[str, object]) -> None:
        replace_file(self._folder / f"raw-{day.isoformat()}.json", dict(payload))

    def day(self, day: date) -> DayRecord | None:
        path = self._path(day)
        return _decode(path) if path.exists() else None

    def days(self) -> tuple[DayRecord, ...]:
        if not self._folder.exists():
            return ()
        return tuple(_decode(path) for path in sorted(self._folder.glob("day-*.json")))

    def _path(self, day: date) -> Path:
        return self._folder / f"day-{day.isoformat()}.json"


def replace_file(path: Path, value: dict[str, object]) -> None:
    """임시 파일에 다 쓴 뒤 제자리에 갈아 끼운다(600)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".record.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=1)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def _decode(path: Path) -> DayRecord:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != DAY_SCHEMA:
        raise ValueError(f"unexpected day record schema — file={path.name}")
    return DayRecord(
        date.fromisoformat(value["day"]),
        datetime.fromisoformat(value["recorded_at"]),
        str(value["plan_id"]),
        bool(value["matched"]),
        str(value["detail"]),
        int(value["cash_krw"]),
        tuple((str(s), int(n), int(p)) for s, n, p in value["holdings"]),
        int(value["equity_krw"]),
        int(value["traded_krw"]),
        tuple(DayOrder(**order) for order in value["orders"]),
    )
