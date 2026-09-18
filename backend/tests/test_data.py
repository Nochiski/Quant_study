"""CSV 로더와 DataFeed 테스트 (테스트가 직접 만든 임시 파일 사용)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest

from backtest_engine.adapters.csv_bars import load_bars_csv
from backtest_engine.data import feed as feed_module
from backtest_engine.data.feed import DataFeed
from backtest_engine.errors import TimeReversalError
from backtest_engine.ports.market_data import LoadStatus, OhlcPolicy
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from tests.conftest import day, make_bar, make_instrument

INSTRUMENT = make_instrument()


def write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join(["date,open,high,low,close,volume", *rows]) + "\n")
    return path


class TestCsvLoader:
    def test_valid_file_loads_in_order(self, tmp_path: Path) -> None:
        path = write_csv(
            tmp_path / "bars.csv",
            ["2026-08-03,100,110,95,105,1000", "2026-08-04,105,120,100,115,2000"],
        )
        result = load_bars_csv(path, INSTRUMENT)
        assert result.ok
        assert len(result.bars) == 2
        assert result.bars[0].close == 105.0
        assert result.bars[1].ts == day(4)

    def test_missing_file_is_no_data(self, tmp_path: Path) -> None:
        result = load_bars_csv(tmp_path / "absent.csv", INSTRUMENT)
        assert result.status is LoadStatus.NO_DATA
        assert result.detail is not None and "absent.csv" in result.detail

    def test_header_only_is_no_data(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "empty.csv", [])
        result = load_bars_csv(path, INSTRUMENT)
        assert result.status is LoadStatus.NO_DATA

    def test_wrong_header_is_format_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_header.csv"
        path.write_text("timestamp,o,h,l,c,v\n2026-08-03,1,1,1,1,1\n")
        result = load_bars_csv(path, INSTRUMENT)
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "unexpected header" in result.detail

    def test_ohlc_violation_reports_line(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "bad_ohlc.csv", ["2026-08-03,100,90,95,105,1000"])
        result = load_bars_csv(path, INSTRUMENT)
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "line=2" in result.detail

    def test_clamp_policy_drops_halt_rows(self, tmp_path: Path) -> None:
        # 삼성전자 액면분할 정지 구간에서 관찰된 패턴: open/high/low=0, close만 존재
        path = write_csv(
            tmp_path / "halt.csv",
            [
                "2018-04-27,53380,53639,52440,53000,606216",
                "2018-04-30,0,0,0,53000,0",
                "2018-05-04,53000,53900,51800,51900,39565391",
            ],
        )
        result = load_bars_csv(path, INSTRUMENT, ohlc_policy=OhlcPolicy.CLAMP)
        assert result.ok
        assert result.dropped_rows == 1
        assert [bar.ts.day for bar in result.bars] == [27, 4]

    def test_strict_policy_rejects_halt_rows(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path / "halt_strict.csv", ["2018-04-30,0,0,0,53000,0"])
        result = load_bars_csv(path, INSTRUMENT)
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "line=2" in result.detail

    def test_clamp_policy_repairs_and_counts(self, tmp_path: Path) -> None:
        # close(28000) > high(27999): 실제 PyKRX 수정주가에서 관찰되는 패턴
        path = write_csv(
            tmp_path / "clamp.csv",
            ["2026-08-03,27500,27999,27480,28000,1000", "2026-08-04,28000,28500,27900,28100,1000"],
        )
        result = load_bars_csv(path, INSTRUMENT, ohlc_policy=OhlcPolicy.CLAMP)
        assert result.ok
        assert result.repaired_rows == 1
        assert result.bars[0].high == 28000.0  # 몸통을 포함하도록 넓힘
        assert result.bars[1].high == 28500.0  # 정상 행은 그대로

    def test_time_reversal_rejected(self, tmp_path: Path) -> None:
        path = write_csv(
            tmp_path / "reversed.csv",
            ["2026-08-04,100,110,95,105,1000", "2026-08-03,105,120,100,115,2000"],
        )
        result = load_bars_csv(path, INSTRUMENT)
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "strictly increasing" in result.detail


class TestDataFeed:
    def test_groups_same_ts_into_one_snapshot(self) -> None:
        other = make_instrument("000660")
        feed = DataFeed(
            [
                make_bar(day(1), INSTRUMENT, 100, 100),
                make_bar(day(1), other, 50, 50),
                make_bar(day(2), INSTRUMENT, 101, 101),
            ]
        )
        snapshots = list(feed.snapshots())
        assert len(snapshots) == 2
        assert {bar.instrument.symbol for bar in snapshots[0].bars} == {"005930", "000660"}
        assert feed.sessions == (day(1), day(2))

    def test_per_instrument_time_reversal_rejected(self) -> None:
        with pytest.raises(TimeReversalError, match="005930"):
            DataFeed(
                [
                    make_bar(day(2), INSTRUMENT, 100, 100),
                    make_bar(day(1), INSTRUMENT, 99, 99),
                ]
            )


@dataclass(frozen=True)
class _Columns:
    """`DataFeed.from_columns` 인자 한 벌. 테스트가 한 열만 바꿔 넣기 쉽게 묶어 둔다."""

    sessions: list[datetime]
    instruments: list[InstrumentId]
    offsets: list[int]
    instrument_ids: list[int]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[int]

    def feed(self) -> DataFeed:
        return DataFeed.from_columns(
            sessions=self.sessions,
            instruments=self.instruments,
            offsets=self.offsets,
            instrument_ids=self.instrument_ids,
            opens=self.opens,
            highs=self.highs,
            lows=self.lows,
            closes=self.closes,
            volumes=self.volumes,
        )


def _columns_of(bars: list[Bar]) -> _Columns:
    """Bar 목록을 세션순·입력순으로 편 열 묶음 (DataFeed(bars)와 같은 순서)."""
    grouped: dict[datetime, list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(bar.ts, []).append(bar)
    sessions = sorted(grouped)
    instruments: list[InstrumentId] = []
    index_of: dict[InstrumentId, int] = {}
    columns = _Columns(sessions, instruments, [0], [], [], [], [], [], [])
    for ts in sessions:
        for bar in grouped[ts]:
            index = index_of.get(bar.instrument)
            if index is None:
                index = len(instruments)
                index_of[bar.instrument] = index
                instruments.append(bar.instrument)
            columns.instrument_ids.append(index)
            columns.opens.append(bar.open)
            columns.highs.append(bar.high)
            columns.lows.append(bar.low)
            columns.closes.append(bar.close)
            columns.volumes.append(bar.volume)
        columns.offsets.append(len(columns.instrument_ids))
    return columns


def _sample_bars() -> list[Bar]:
    other = make_instrument("000660")
    return [
        make_bar(day(1), INSTRUMENT, 100, 105),
        make_bar(day(1), other, 50, 52),
        make_bar(day(2), INSTRUMENT, 105, 101),
        make_bar(day(2), other, 52, 49),
        make_bar(day(3), other, 49, 51),
    ]


class TestDataFeedFromColumns:
    """열 단위 생성자가 `DataFeed(bars)`와 같은 feed를 만드는지."""

    def test_matches_bar_constructor(self) -> None:
        bars = _sample_bars()
        from_bars = DataFeed(bars)
        from_columns = _columns_of(bars).feed()
        assert list(from_columns.snapshots()) == list(from_bars.snapshots())
        assert from_columns.sessions == from_bars.sessions
        assert len(from_columns) == len(from_bars)

    def test_columns_round_trip_from_bar_constructor(self) -> None:
        bars = _sample_bars()
        expected = _columns_of(bars)
        columns = DataFeed(bars).columns()
        assert list(columns.instruments) == expected.instruments
        assert list(columns.offsets) == expected.offsets
        assert list(columns.instrument_ids) == expected.instrument_ids
        assert list(columns.opens) == expected.opens
        assert list(columns.highs) == expected.highs
        assert list(columns.lows) == expected.lows
        assert list(columns.closes) == expected.closes
        assert list(columns.volumes) == expected.volumes

    def test_columns_survive_repeated_calls(self) -> None:
        feed = DataFeed(_sample_bars())
        assert feed.columns() is feed.columns()

    def test_snapshot_at_is_lazy_and_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        columns = _columns_of(_sample_bars())
        built: list[datetime] = []
        real_snapshot = feed_module.MarketSnapshot

        def counting(*, ts: datetime, bars: tuple[Bar, ...]) -> MarketSnapshot:
            built.append(ts)
            return real_snapshot(ts=ts, bars=bars)

        monkeypatch.setattr(feed_module, "MarketSnapshot", counting)
        feed = columns.feed()
        assert built == []
        assert feed.sessions == (day(1), day(2), day(3))
        assert len(feed) == 3
        assert built == []

        second = feed.snapshot_at(1)
        assert built == [day(2)]
        assert feed.snapshot_at(1) is second
        assert built == [day(2)]

        assert len(list(feed.snapshots())) == 3
        assert sorted(built) == [day(1), day(2), day(3)]

    def test_non_increasing_sessions_rejected(self) -> None:
        columns = replace(_columns_of(_sample_bars()), sessions=[day(2), day(1), day(3)])
        with pytest.raises(TimeReversalError, match="strictly increasing"):
            columns.feed()

    def test_duplicate_instrument_in_session_rejected(self) -> None:
        other = make_instrument("000660")
        columns = _columns_of(
            [make_bar(day(1), INSTRUMENT, 100, 105), make_bar(day(1), other, 50, 52)]
        )
        with pytest.raises(ValueError, match="duplicate instrument in snapshot"):
            replace(columns, instrument_ids=[0, 0]).feed()

    def test_price_violation_message_matches_bar(self) -> None:
        columns = replace(_columns_of([make_bar(day(1), INSTRUMENT, 100.0, 105.0)]), highs=[90.0])
        with pytest.raises(ValueError) as columnar:
            columns.feed()
        with pytest.raises(ValueError) as from_bar:
            Bar(
                ts=day(1),
                instrument=INSTRUMENT,
                open=100.0,
                high=90.0,
                low=100.0,
                close=105.0,
                volume=1_000,
            )
        assert str(columnar.value) == str(from_bar.value)

    def test_zero_price_message_matches_bar(self) -> None:
        columns = replace(_columns_of([make_bar(day(1), INSTRUMENT, 100.0, 105.0)]), lows=[0.0])
        with pytest.raises(ValueError) as columnar:
            columns.feed()
        with pytest.raises(ValueError) as from_bar:
            Bar(
                ts=day(1),
                instrument=INSTRUMENT,
                open=100.0,
                high=105.0,
                low=0.0,
                close=105.0,
                volume=1_000,
            )
        assert str(columnar.value) == str(from_bar.value)

    def test_negative_volume_message_matches_bar(self) -> None:
        columns = replace(_columns_of([make_bar(day(1), INSTRUMENT, 100.0, 105.0)]), volumes=[-1])
        with pytest.raises(ValueError) as columnar:
            columns.feed()
        with pytest.raises(ValueError) as from_bar:
            Bar(
                ts=day(1),
                instrument=INSTRUMENT,
                open=100.0,
                high=105.0,
                low=100.0,
                close=105.0,
                volume=-1,
            )
        assert str(columnar.value) == str(from_bar.value)

    def test_offsets_must_hold_one_boundary_per_session(self) -> None:
        columns = replace(_columns_of(_sample_bars()), offsets=[0, 2, 4])
        with pytest.raises(ValueError, match="one boundary per session"):
            columns.feed()

    def test_offsets_must_end_at_row_count(self) -> None:
        columns = replace(_columns_of(_sample_bars()), offsets=[0, 2, 4, 4])
        with pytest.raises(ValueError, match="must start at 0 and end at the row count"):
            columns.feed()

    def test_column_length_mismatch_rejected(self) -> None:
        columns = _columns_of(_sample_bars())
        with pytest.raises(ValueError, match="feed column lengths must match"):
            replace(columns, volumes=columns.volumes[:-1]).feed()

    def test_instrument_id_out_of_range_rejected(self) -> None:
        columns = replace(_columns_of(_sample_bars()), instrument_ids=[0, 1, 0, 1, 7])
        with pytest.raises(ValueError, match="instrument id out of range"):
            columns.feed()

    def test_negative_session_index_is_rejected(self) -> None:
        """뒤에서 세는 index는 세션 경계가 뒤집혀 빈 스냅샷이 된다 — 조용히 답하지 않는다."""
        feed = _columns_of(_sample_bars()).feed()
        with pytest.raises(IndexError, match="must be >= 0"):
            feed.snapshot_at(-1)

    def test_empty_feed_is_supported(self) -> None:
        feed = _Columns([], [], [0], [], [], [], [], [], []).feed()
        assert len(feed) == 0
        assert feed.sessions == ()
        assert list(feed.snapshots()) == []
        assert feed.columns().instruments == ()
