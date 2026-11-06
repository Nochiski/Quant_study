"""개별종목차트 선 저장(`ChartDrawingStore`) — 연구 DB의 `chart_drawing` 표(판 12).

행 ↔ 값 타입 변환만 한다. 어휘(종류·출처·주기·판정·방향 출처·구간 출처)는 domain 열거형으로 읽어
모르는 값이면 `ResearchStorageError`로 멈춘다 — 다른 판의 서버가 쓴 행을 조용히 다르게 읽지 않는다.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TypeVar

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.stock_chart.facade.ports import (
    AutoRecord,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    InstrumentKind,
    InstrumentRef,
    LineAnchor,
    ParamValue,
    RegimeRecord,
)
from strategy_workbench.domain.chart_analysis.facade.bars import Timeframe
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeVerdict, Structure

from ._errors import ResearchStorageError
from ._schema import migrate_schema

_E = TypeVar("_E", bound=Enum)

_COLUMNS = """
    line_id, instrument_kind, instrument_code, timeframe, kind, source, anchors_json, levels_json,
    extend_right, memo, locked, visible, algorithm_version, params_json, regime_window_start,
    regime_window_end, regime_verdict, regime_strength, regime_structure, chosen_by, window_source,
    created_at, updated_at
"""


class SQLiteChartDrawingStore:
    """선 저장 SQLite adapter. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB다."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._database = SqliteDatabase(
            path, label="research database", error=ResearchStorageError, prepare=migrate_schema
        )

    @property
    def database_path(self) -> Path | None:
        return self._database.database_path

    def close(self) -> None:
        self._database.close()

    def list_lines(self, instrument: InstrumentRef) -> tuple[ChartLine, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM chart_drawing "
                "WHERE instrument_kind = ? AND instrument_code = ? ORDER BY drawing_order",
                (instrument.kind.value, instrument.code),
            ).fetchall()
        return tuple(_line(row) for row in rows)

    def get_line(self, line_id: str) -> ChartLine | None:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM chart_drawing WHERE line_id = ?", (line_id,)
            ).fetchone()
        return None if row is None else _line(row)

    def add_line(self, line: ChartLine) -> None:
        auto = line.auto
        regime = None if auto is None else auto.regime
        window = None if auto is None else auto.regime_window
        with self._database.transaction(write=True) as connection:
            connection.execute(
                f"INSERT INTO chart_drawing ({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    line.line_id,
                    line.instrument.kind.value,
                    line.instrument.code,
                    line.timeframe.value,
                    line.kind.value,
                    line.source.value,
                    *_content_values(line.content),
                    None if auto is None else auto.algorithm_version,
                    None if auto is None else _json(dict(auto.params)),
                    None if window is None else window[0].isoformat(),
                    None if window is None else window[1].isoformat(),
                    None if regime is None else regime.verdict.value,
                    None if regime is None else regime.strength,
                    None if regime is None else regime.structure.value,
                    None if auto is None or auto.chosen_by is None else auto.chosen_by.value,
                    None
                    if auto is None or auto.window_source is None
                    else auto.window_source.value,
                    _time(line.created_at, "created_at", line.line_id),
                    _time(line.updated_at, "updated_at", line.line_id),
                ),
            )

    def update_content(
        self, line_id: str, content: ChartLineContent, updated_at: datetime
    ) -> ChartLine:
        with self._database.transaction(write=True) as connection:
            changed = connection.execute(
                "UPDATE chart_drawing SET anchors_json = ?, levels_json = ?, extend_right = ?, "
                "memo = ?, locked = ?, visible = ?, updated_at = ? WHERE line_id = ?",
                (
                    *_content_values(content),
                    _time(updated_at, "updated_at", line_id),
                    line_id,
                ),
            ).rowcount
            if changed == 0:
                raise ChartLineNotFoundError(line_id)
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM chart_drawing WHERE line_id = ?", (line_id,)
            ).fetchone()
        return _line(row)

    def delete_line(self, line_id: str) -> None:
        with self._database.transaction(write=True) as connection:
            deleted = connection.execute(
                "DELETE FROM chart_drawing WHERE line_id = ?", (line_id,)
            ).rowcount
            if deleted == 0:
                raise ChartLineNotFoundError(line_id)


def _json(value: object) -> str:
    # NaN·inf 는 표준 JSON 이 아니다 — 저장 전에 막는다(application 이 이미 걸러야 한다).
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _time(value: datetime, field: str, line_id: str) -> str:
    return datetime_text(value, field=f"chart line {field} (line_id={line_id})")


def _content_values(content: ChartLineContent) -> tuple[object, ...]:
    return (
        _json([[anchor.bar_date.isoformat(), anchor.price] for anchor in content.anchors]),
        None if content.levels is None else _json(list(content.levels)),
        int(content.extend_right),
        content.memo,
        int(content.locked),
        int(content.visible),
    )


def _vocab(enum: type[_E], value: object, field: str, line_id: str) -> _E:
    try:
        return enum(value)
    except ValueError as error:
        raise ResearchStorageError(
            f"unknown {field} in chart_drawing — line_id={line_id} value={value!r} "
            f"known={[item.value for item in enum]}"
        ) from error


def _line(row: sqlite3.Row | tuple[object, ...]) -> ChartLine:
    (
        line_id,
        instrument_kind,
        instrument_code,
        timeframe,
        kind,
        source,
        anchors_json,
        levels_json,
        extend_right,
        memo,
        locked,
        visible,
        algorithm_version,
        params_json,
        window_start,
        window_end,
        verdict,
        strength,
        structure,
        chosen_by,
        window_source,
        created_at,
        updated_at,
    ) = tuple(row)
    label = str(line_id)
    try:
        anchors = tuple(
            LineAnchor(date.fromisoformat(day), float(price))
            for day, price in json.loads(str(anchors_json))
        )
        levels = (
            None if levels_json is None else tuple(float(v) for v in json.loads(str(levels_json)))
        )
        params: Mapping[str, ParamValue] = (
            {} if params_json is None else json.loads(str(params_json))
        )
    except (TypeError, ValueError) as error:
        raise ResearchStorageError(
            f"chart_drawing JSON column could not be read — line_id={label} error={error!r}"
        ) from error
    if not all(math.isfinite(anchor.price) for anchor in anchors):
        raise ResearchStorageError(f"chart_drawing anchor price is not finite — line_id={label}")
    auto = None
    if algorithm_version is not None:
        auto = AutoRecord(
            algorithm_version=str(algorithm_version),
            params=params,
            regime_window=None
            if window_start is None or window_end is None
            else (date.fromisoformat(str(window_start)), date.fromisoformat(str(window_end))),
            regime=None
            if verdict is None or structure is None
            else RegimeRecord(
                _vocab(RegimeVerdict, verdict, "regime_verdict", label),
                None if strength is None else float(str(strength)),
                _vocab(Structure, structure, "regime_structure", label),
            ),
            chosen_by=None
            if chosen_by is None
            else _vocab(ChosenBy, chosen_by, "chosen_by", label),
            window_source=None
            if window_source is None
            else _vocab(WindowSource, window_source, "window_source", label),
        )
    return ChartLine(
        line_id=label,
        instrument=InstrumentRef(
            _vocab(InstrumentKind, instrument_kind, "instrument_kind", label), str(instrument_code)
        ),
        timeframe=_vocab(Timeframe, timeframe, "timeframe", label),
        kind=_vocab(LineKind, kind, "kind", label),
        source=_vocab(LineSource, source, "source", label),
        content=ChartLineContent(
            anchors=anchors,
            levels=levels,
            extend_right=bool(extend_right),
            memo=str(memo),
            locked=bool(locked),
            visible=bool(visible),
        ),
        auto=auto,
        created_at=datetime.fromisoformat(str(created_at)),
        updated_at=datetime.fromisoformat(str(updated_at)),
    )
