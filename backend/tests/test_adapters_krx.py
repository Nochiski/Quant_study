"""KRX 원장 parquet 어댑터 테스트.

동작 검증은 테스트가 직접 만든 소형 parquet로 한다. 레포의 `tests/fixtures/krx_parquet/`
슬라이스는 마지막 스모크 테스트에서 "엔진까지 예외 없이 도는지"만 확인하며
행 수·특정 값은 단언하지 않는다 (`.claude/rules/testing.md`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from backtest_engine.adapters.krx_parquet import (  # noqa: E402  # reason: importorskip 이후 import
    KOSDAQ_TRADES_FILE,
    KOSPI_TRADES_FILE,
    KrxParquetBarSource,
)
from backtest_engine.data.feed import DataFeed  # noqa: E402  # reason: importorskip 이후 import
from backtest_engine.ports.market_data import (  # noqa: E402  # reason: importorskip 이후 import
    BarQuery,
    LoadStatus,
    OhlcPolicy,
)
from tests.conftest import make_instrument  # noqa: E402  # reason: importorskip 이후 import

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "krx_parquet"
SAMSUNG = make_instrument("005930")
HYNIX = make_instrument("000660")

TradeRow = tuple[date, str, int, int, int, int, int]


def write_trades(path: Path, rows: list[TradeRow]) -> None:
    """원장과 같은 컬럼·타입으로 parquet을 쓴다 (관심 컬럼만)."""
    table = pa.table(
        {
            "bas_dd": pa.array([r[0] for r in rows], type=pa.date32()),
            "isu_cd": pa.array([r[1] for r in rows], type=pa.string()),
            "tdd_clsprc": pa.array([r[5] for r in rows], type=pa.int64()),
            "tdd_opnprc": pa.array([r[2] for r in rows], type=pa.int64()),
            "tdd_hgprc": pa.array([r[3] for r in rows], type=pa.int64()),
            "tdd_lwprc": pa.array([r[4] for r in rows], type=pa.int64()),
            "fluc_rt": pa.array([Decimal("0.00")] * len(rows), type=pa.decimal128(18, 2)),
            "acc_trdvol": pa.array([r[6] for r in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, path)


def d(day: int) -> date:
    return date(2018, 4, day)


class TestKrxParquetBarSource:
    def test_missing_root_files_is_no_data(self, tmp_path: Path) -> None:
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.NO_DATA
        assert result.detail is not None and "no KRX trade parquet" in result.detail

    def test_unsorted_rows_are_sorted_by_session(self, tmp_path: Path) -> None:
        write_trades(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (d(4), "005930", 100, 110, 95, 105, 1000),
                (d(2), "005930", 90, 100, 85, 95, 1000),
                (d(3), "005930", 95, 105, 90, 100, 1000),
            ],
        )
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.ok
        assert [bar.ts for bar in result.bars] == [
            datetime(2018, 4, 2),
            datetime(2018, 4, 3),
            datetime(2018, 4, 4),
        ]

    def test_halt_rows_dropped_by_zero_volume_even_if_close_carried(self, tmp_path: Path) -> None:
        # 국보(001140) 2026-01 패턴: 정지 중 종가는 직전값 유지, 거래량만 0
        write_trades(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (d(2), "005930", 100, 110, 95, 105, 1000),
                (d(3), "005930", 105, 105, 105, 105, 0),
                (d(4), "005930", 105, 115, 100, 110, 1000),
            ],
        )
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.ok
        assert result.dropped_rows == 1
        assert [bar.ts.day for bar in result.bars] == [2, 4]

    def test_split_halt_rows_with_zero_ohl_are_dropped(self, tmp_path: Path) -> None:
        # 삼성전자 2018-04-30 액면분할 정지 패턴: o/h/l=0, close 유지, 거래량 0
        write_trades(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (d(27), "005930", 2_650_000, 2_660_000, 2_640_000, 2_650_000, 100),
                (d(30), "005930", 0, 0, 0, 2_650_000, 0),
            ],
        )
        result = KrxParquetBarSource(tmp_path).load_bars(
            BarQuery(instruments=(SAMSUNG,), ohlc_policy=OhlcPolicy.STRICT)
        )
        assert result.ok
        assert result.dropped_rows == 1
        assert len(result.bars) == 1

    def test_date_range_filter_is_inclusive(self, tmp_path: Path) -> None:
        write_trades(
            tmp_path / KOSPI_TRADES_FILE,
            [(d(day), "005930", 100, 110, 95, 105, 1000) for day in (2, 3, 4, 5, 6)],
        )
        result = KrxParquetBarSource(tmp_path).load_bars(
            BarQuery(instruments=(SAMSUNG,), start=d(3), end=d(5))
        )
        assert result.ok
        assert [bar.ts.day for bar in result.bars] == [3, 4, 5]

    def test_reads_across_kospi_and_kosdaq_files(self, tmp_path: Path) -> None:
        write_trades(tmp_path / KOSPI_TRADES_FILE, [(d(2), "005930", 100, 110, 95, 105, 1000)])
        write_trades(tmp_path / KOSDAQ_TRADES_FILE, [(d(2), "247540", 50, 55, 45, 52, 500)])
        kosdaq = make_instrument("247540")
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG, kosdaq)))
        assert result.ok
        assert {bar.instrument.symbol for bar in result.bars} == {"005930", "247540"}

    def test_missing_instrument_fails_whole_query(self, tmp_path: Path) -> None:
        write_trades(tmp_path / KOSPI_TRADES_FILE, [(d(2), "005930", 100, 110, 95, 105, 1000)])
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG, HYNIX)))
        assert result.status is LoadStatus.NO_DATA
        assert result.detail is not None and "symbol=000660" in result.detail

    def test_duplicate_session_is_format_error(self, tmp_path: Path) -> None:
        write_trades(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (d(2), "005930", 100, 110, 95, 105, 1000),
                (d(2), "005930", 100, 110, 95, 106, 1000),
            ],
        )
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "duplicate session" in result.detail

    def test_strict_policy_rejects_ohlc_violation(self, tmp_path: Path) -> None:
        write_trades(tmp_path / KOSPI_TRADES_FILE, [(d(2), "005930", 100, 99, 95, 105, 1000)])
        result = KrxParquetBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "session=2018-04-02" in result.detail


@pytest.mark.skipif(not FIXTURE_DIR.exists(), reason="KRX fixture slice not present")
def test_fixture_slice_runs_through_engine_smoke() -> None:
    """레포 슬라이스가 포트→DataFeed→엔진까지 예외 없이 흐르는지만 본다."""
    from backtest_engine import BacktestEngine, RunConfig
    from examples.golden_cross import GoldenCrossConfig, GoldenCrossStrategy

    result = KrxParquetBarSource(FIXTURE_DIR).load_bars(
        BarQuery(
            instruments=(SAMSUNG,),
            start=date(2019, 1, 1),
            end=date(2020, 12, 31),
            ohlc_policy=OhlcPolicy.CLAMP,
        )
    )
    assert result.ok, result.detail
    feed = DataFeed(result.bars)
    outcome = BacktestEngine(RunConfig(run_id="smoke", initial_cash=10_000_000, fee_bps=15)).run(
        GoldenCrossStrategy(GoldenCrossConfig(instrument=SAMSUNG)), feed
    )
    assert len(outcome.snapshots) == len(feed)
    assert outcome.snapshots[-1].equity > 0
