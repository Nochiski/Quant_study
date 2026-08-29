"""자본변동 검출(D1): 소스 무관 검출 규칙과 KRX parquet 어댑터.

검출 규칙: 연속 세션의 상장주식수 비율 r이 1.5 이상 벌어지면 사건. 종가가 반비례
(0.5 ≤ p×r ≤ 2)로 확인되면 SPLIT/REVERSE_SPLIT, 아니면 SHARE_COUNT_CHANGE.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from backtest_engine.data.corporate_actions import ShareCountRow, detect_share_count_events
from backtest_engine.ports.corporate_actions import CorporateActionQuery
from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.types.events import CorporateActionType
from tests.conftest import make_instrument

INSTRUMENT = make_instrument()
OTHER = make_instrument("000660")


def row(day_of_month: int, shares: int, close: int, volume: int = 100) -> ShareCountRow:
    return ShareCountRow(
        session=date(2018, 5, day_of_month), listed_shares=shares, close=close, volume=volume
    )


class TestDetection:
    def test_split_confirmed_by_inverse_price_move(self) -> None:
        events = detect_share_count_events(
            [row(2, 100, 2_650_000), row(3, 100, 2_650_000), row(4, 5_000, 51_900)], INSTRUMENT
        )
        assert len(events) == 1
        event = events[0]
        assert event.action_type is CorporateActionType.SPLIT
        assert event.ratio == Decimal(50)
        assert event.ts == datetime(2018, 5, 4)
        assert event.instrument == INSTRUMENT
        assert "100" in event.detail and "5000" in event.detail

    def test_reverse_split_confirmed(self) -> None:
        events = detect_share_count_events([row(2, 1_000, 500), row(3, 100, 5_100)], INSTRUMENT)
        assert [(e.action_type, e.ratio) for e in events] == [
            (CorporateActionType.REVERSE_SPLIT, Decimal("0.1"))
        ]

    def test_unconfirmed_when_price_is_halt_marker(self) -> None:
        # 주식 수 ÷924인데 종가가 1원(정지 마커) → 가격 확인 실패
        events = detect_share_count_events(
            [row(2, 92_446_775, 537), row(3, 100_000, 1)], INSTRUMENT
        )
        assert [e.action_type for e in events] == [CorporateActionType.SHARE_COUNT_CHANGE]
        assert events[0].ratio == Decimal(100_000) / Decimal(92_446_775)

    def test_unconfirmed_when_previous_close_is_zero(self) -> None:
        events = detect_share_count_events([row(2, 100, 0), row(3, 5_000, 50)], INSTRUMENT)
        assert [e.action_type for e in events] == [CorporateActionType.SHARE_COUNT_CHANGE]

    def test_small_share_count_change_is_not_an_event(self) -> None:
        # 자사주 소각 −6% 같은 소폭 변화
        assert detect_share_count_events([row(2, 100, 100), row(3, 94, 100)], INSTRUMENT) == ()

    def test_price_limit_streak_without_share_change_is_not_an_event(self) -> None:
        assert (
            detect_share_count_events(
                [row(2, 100, 100), row(3, 100, 130), row(4, 100, 169)], INSTRUMENT
            )
            == ()
        )

    def test_halt_rows_participate_in_detection(self) -> None:
        # 분할 직후 첫 행이 거래정지(거래량 0)여도 사건 세션은 그 행이다.
        events = detect_share_count_events(
            [row(3, 100, 2_650_000), row(4, 5_000, 53_000, volume=0)], INSTRUMENT
        )
        assert [(e.action_type, e.ts) for e in events] == [
            (CorporateActionType.SPLIT, datetime(2018, 5, 4))
        ]

    def test_rows_must_be_sorted(self) -> None:
        with pytest.raises(ValueError, match="sorted"):
            detect_share_count_events([row(4, 100, 100), row(3, 100, 100)], INSTRUMENT)


class TestQuery:
    def test_rejects_empty_and_reversed(self) -> None:
        with pytest.raises(ValueError, match="at least one instrument"):
            CorporateActionQuery(instruments=())
        with pytest.raises(ValueError, match="start must be <= end"):
            CorporateActionQuery(
                instruments=(INSTRUMENT,), start=date(2018, 5, 4), end=date(2018, 5, 1)
            )


# --- KRX parquet 어댑터 --------------------------------------------------------

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from backtest_engine.adapters.krx_parquet import (  # noqa: E402  # reason: importorskip 이후 import
    KOSPI_TRADES_FILE,
    KrxParquetCorporateActionSource,
)

LedgerRow = tuple[date, str, int, int, int]  # session, symbol, close, volume, list_shrs


def write_ledger(path: Path, rows: list[LedgerRow]) -> None:
    table = pa.table(
        {
            "bas_dd": pa.array([r[0] for r in rows], type=pa.date32()),
            "isu_cd": pa.array([r[1] for r in rows], type=pa.string()),
            "tdd_clsprc": pa.array([r[2] for r in rows], type=pa.int64()),
            "tdd_opnprc": pa.array([r[2] for r in rows], type=pa.int64()),
            "tdd_hgprc": pa.array([r[2] for r in rows], type=pa.int64()),
            "tdd_lwprc": pa.array([r[2] for r in rows], type=pa.int64()),
            "acc_trdvol": pa.array([r[3] for r in rows], type=pa.int64()),
            "list_shrs": pa.array([r[4] for r in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, path)


class TestKrxAdapter:
    def test_detects_split_from_unsorted_rows_and_filters_period(self, tmp_path: Path) -> None:
        write_ledger(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (date(2018, 5, 4), "005930", 51_900, 10, 5_000),
                (date(2018, 5, 3), "005930", 2_650_000, 0, 100),
                (date(2018, 5, 2), "005930", 2_650_000, 10, 100),
                (date(2018, 12, 12), "005930", 40_450, 10, 4_650),  # 소폭(−7%) 변화, 사건 아님
            ],
        )
        source = KrxParquetCorporateActionSource(tmp_path)
        result = source.load_actions(CorporateActionQuery(instruments=(INSTRUMENT,)))
        assert result.ok, result.detail
        assert [(e.action_type, e.ratio, e.ts.date()) for e in result.actions] == [
            (CorporateActionType.SPLIT, Decimal(50), date(2018, 5, 4))
        ]
        # 기간을 사건 이후로 잡으면 사건이 없다 (사건 세션 = 새 주식 수 첫 등장일)
        later = source.load_actions(
            CorporateActionQuery(instruments=(INSTRUMENT,), start=date(2018, 5, 5))
        )
        assert later.ok and later.actions == ()

    def test_missing_instrument_fails_whole_query(self, tmp_path: Path) -> None:
        write_ledger(tmp_path / KOSPI_TRADES_FILE, [(date(2018, 5, 2), "005930", 100, 10, 100)])
        result = KrxParquetCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(INSTRUMENT, OTHER))
        )
        assert result.status is LoadStatus.NO_DATA
        assert "000660" in (result.detail or "")

    def test_missing_files_is_no_data(self, tmp_path: Path) -> None:
        result = KrxParquetCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(INSTRUMENT,))
        )
        assert result.status is LoadStatus.NO_DATA


def test_fixture_slice_split_period_runs_through_engine_smoke() -> None:
    """슬라이스의 분할 구간(2018-05)을 포함해 사건 로드 → 엔진이 예외 없이 도는지만 본다."""
    from backtest_engine import BacktestEngine, RunConfig
    from backtest_engine.adapters.krx_parquet import KrxParquetBarSource
    from backtest_engine.data.feed import DataFeed
    from backtest_engine.ports.market_data import BarQuery, OhlcPolicy
    from examples.golden_cross import GoldenCrossConfig, GoldenCrossStrategy

    fixture = Path(__file__).resolve().parent / "fixtures" / "krx_parquet"
    query = BarQuery(
        instruments=(INSTRUMENT,),
        start=date(2018, 1, 1),
        end=date(2018, 12, 31),
        ohlc_policy=OhlcPolicy.CLAMP,
    )
    bars = KrxParquetBarSource(fixture).load_bars(query)
    actions = KrxParquetCorporateActionSource(fixture).load_actions(
        CorporateActionQuery(instruments=(INSTRUMENT,), start=query.start, end=query.end)
    )
    assert bars.ok, bars.detail
    assert actions.ok, actions.detail
    feed = DataFeed(bars.bars)
    result = BacktestEngine(
        RunConfig(run_id="smoke-split", initial_cash=10_000_000, fee_bps=15)
    ).run(
        GoldenCrossStrategy(GoldenCrossConfig(instrument=INSTRUMENT)),
        feed,
        corporate_actions=actions.actions,
    )
    assert len(result.snapshots) == len(feed)
    assert result.snapshots[-1].equity > 0


class TestKrxAdapterDuplicates:
    def test_duplicate_session_across_files_is_format_error(self, tmp_path: Path) -> None:
        """DEFECT-206: 두 시세 파일에 겹치는 세션은 예외가 아니라 FORMAT_ERROR."""
        from backtest_engine.adapters.krx_parquet import KOSDAQ_TRADES_FILE

        write_ledger(
            tmp_path / KOSPI_TRADES_FILE,
            [
                (date(2020, 1, 2), "005930", 100, 10, 100),
                (date(2020, 1, 3), "005930", 100, 10, 100),
            ],
        )
        write_ledger(
            tmp_path / KOSDAQ_TRADES_FILE,
            [
                (date(2020, 1, 3), "005930", 100, 10, 100),
                (date(2020, 1, 6), "005930", 100, 10, 100),
            ],
        )
        result = KrxParquetCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(INSTRUMENT,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "2020-01-03" in (result.detail or "")
