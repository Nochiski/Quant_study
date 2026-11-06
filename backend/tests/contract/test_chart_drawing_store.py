"""연구 DB 판본 12 — 개별종목차트 선 저장(`chart_drawing`, 설계 문서 4.7)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite._schema import V11_SCHEMA_OBJECTS
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    ResearchStorageError,
    SQLiteBacktestRunRepository,
    SQLiteChartDrawingStore,
)
from strategy_workbench.application.stock_chart.facade.ports import (
    AutoRecord,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    InstrumentKind,
    InstrumentRef,
    LineAnchor,
    RegimeRecord,
)
from strategy_workbench.domain.chart_analysis.facade.bars import Timeframe
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeVerdict, Structure

_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
STOCK = InstrumentRef(InstrumentKind.STOCK, "005930")
INDEX = InstrumentRef(InstrumentKind.INDEX, "KOSPI:코스피")

MANUAL = ChartLine(
    line_id="line-1",
    instrument=STOCK,
    timeframe=Timeframe.DAY,
    kind=LineKind.HLINE,
    source=LineSource.MANUAL,
    content=ChartLineContent((LineAnchor(date(2025, 9, 3), 1234.5),), memo="지지"),
    auto=None,
    created_at=_AT,
    updated_at=_AT,
)
AUTO = ChartLine(
    line_id="line-2",
    instrument=STOCK,
    timeframe=Timeframe.WEEK,
    kind=LineKind.FIB_CHANNEL,
    source=LineSource.AUTO,
    content=ChartLineContent(
        (
            LineAnchor(date(2025, 3, 3), 100.0),
            LineAnchor(date(2025, 6, 2), 120.0),
            LineAnchor(date(2025, 4, 7), 130.0),
        ),
        levels=(0.0, 0.5, 1.0),
        extend_right=False,
        locked=True,
        visible=False,
    ),
    auto=AutoRecord(
        algorithm_version=ALGORITHM_VERSION,
        params={"direction": "up", "first_anchor": None, "candidate_sensitivity": 1.0},
        regime_window=(date(2024, 10, 28), date(2025, 10, 20)),
        regime=RegimeRecord(RegimeVerdict.UP, 2.64, Structure.UP),
        chosen_by=ChosenBy.USER_OVERRIDE,
        window_source=WindowSource.USER,
    ),
    created_at=_AT,
    updated_at=_AT,
)


def test_lines_round_trip_per_instrument_in_creation_order(tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    store = SQLiteChartDrawingStore(path)
    store.add_line(AUTO)
    store.add_line(MANUAL)
    store.add_line(replace(MANUAL, line_id="line-3", instrument=INDEX))

    reopened = SQLiteChartDrawingStore(path)
    assert reopened.list_lines(STOCK) == (AUTO, MANUAL)
    assert [line.line_id for line in reopened.list_lines(INDEX)] == ["line-3"]
    assert reopened.get_line("line-2") == AUTO
    assert reopened.get_line("missing") is None


def test_update_changes_only_the_editable_fields_and_delete_removes_the_line() -> None:
    store = SQLiteChartDrawingStore()
    store.add_line(AUTO)
    later = datetime(2026, 11, 7, tzinfo=UTC)
    content = ChartLineContent((LineAnchor(date(2025, 3, 3), 99.0),) * 3, memo="옮김")

    updated = store.update_content("line-2", content, later)

    assert (updated.content, updated.updated_at, updated.created_at) == (content, later, _AT)
    assert (updated.auto, updated.kind, updated.timeframe) == (AUTO.auto, AUTO.kind, AUTO.timeframe)
    store.delete_line("line-2")
    assert store.list_lines(STOCK) == ()
    with pytest.raises(ChartLineNotFoundError):
        store.delete_line("line-2")
    with pytest.raises(ChartLineNotFoundError):
        store.update_content("line-2", content, later)


def test_a_version_11_file_gains_the_drawing_table_and_keeps_its_runs(tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        for _object_type, _name, statement in V11_SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO backtest_runs (run_id, status, progress, stage, message, created_at, "
            "updated_at, strategy_kind, spec_hash, schema_version, request_json) VALUES "
            f"('run-1', 'queued', 0.0, 'queued', 'm', 't', 't', 'inline_draft', '{'a' * 64}', "
            "'1.2', '{}')"
        )
        connection.execute("PRAGMA application_id = 1398231635")
        connection.execute("PRAGMA user_version = 11")
        connection.commit()

    SQLiteChartDrawingStore(path).add_line(MANUAL)

    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (12,)
        assert connection.execute("SELECT run_id FROM backtest_runs").fetchall() == [("run-1",)]
    # 같은 파일을 여는 다른 저장소도 새 판을 그대로 받아들인다
    SQLiteBacktestRunRepository(path).close()
    assert SQLiteChartDrawingStore(path).list_lines(STOCK) == (MANUAL,)


def test_auto_columns_must_match_the_source(tmp_path: Path) -> None:
    store = SQLiteChartDrawingStore(tmp_path / "research.sqlite3")
    with pytest.raises(ResearchStorageError):
        store.add_line(replace(MANUAL, auto=AUTO.auto))


def test_an_unknown_vocabulary_value_stops_the_read(tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    SQLiteChartDrawingStore(path).add_line(MANUAL)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE chart_drawing SET kind = 'trendline'")
        connection.commit()

    with pytest.raises(ResearchStorageError, match="kind.*line_id=line-1"):
        SQLiteChartDrawingStore(path).list_lines(STOCK)
