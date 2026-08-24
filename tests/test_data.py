"""CSV 로더와 DataFeed 테스트 (테스트가 직접 만든 임시 파일 사용)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest_engine.data.csv_loader import LoadStatus, OhlcPolicy, load_bars_csv
from backtest_engine.data.feed import DataFeed
from backtest_engine.errors import TimeReversalError
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
