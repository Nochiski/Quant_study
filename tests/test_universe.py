"""유니버스 포트(D2): 구간 결과 타입, KRX 마스터 어댑터, ctx.universe()."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.errors import UniverseNotProvided
from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.ports.universe import Membership, UniverseQuery, UniverseResult
from backtest_engine.types.actions import ActionKind
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import PriceField
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_bar, make_instrument

A = make_instrument("005930")
B = make_instrument("247540")  # 기간 중간 상장
C = make_instrument("008080")  # 기간 중간 상장폐지


def universe() -> UniverseResult:
    return UniverseResult(
        memberships=(
            Membership(A, date(2026, 8, 1), date(2026, 8, 31)),
            Membership(B, date(2026, 8, 3), date(2026, 8, 31)),
            Membership(C, date(2026, 8, 1), date(2026, 8, 2)),
        ),
        status=LoadStatus.OK,
    )


class TestUniverseResult:
    def test_members_include_both_interval_ends(self) -> None:
        u = universe()
        assert u.members(date(2026, 8, 1)) == frozenset({A, C})
        assert u.members(date(2026, 8, 2)) == frozenset({A, C})
        assert u.members(date(2026, 8, 3)) == frozenset({A, B})

    def test_not_listed_yet_is_excluded_no_look_ahead(self) -> None:
        assert B not in universe().members(date(2026, 8, 2))

    def test_delisted_is_excluded_from_next_session(self) -> None:
        assert C not in universe().members(date(2026, 8, 3))

    def test_instruments_active_between_is_sorted_and_deduplicated(self) -> None:
        u = universe()
        assert u.instruments_active_between(date(2026, 8, 3), date(2026, 8, 10)) == (A, B)
        assert u.instruments_active_between(date(2026, 8, 1), date(2026, 8, 1)) == (A, C)
        assert u.instruments_active_between(date(2026, 9, 1), date(2026, 9, 2)) == ()

    def test_membership_rejects_reversed_interval(self) -> None:
        with pytest.raises(ValueError, match="first_session must be <= last_session"):
            Membership(A, date(2026, 8, 2), date(2026, 8, 1))

    def test_query_rejects_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="start must be <= end"):
            UniverseQuery(venue="XKRX", start=date(2026, 8, 2), end=date(2026, 8, 1))


# --- ctx.universe() ----------------------------------------------------------


class ProbeStrategy:
    def __init__(self) -> None:
        self.seen: dict[int, frozenset[InstrumentId]] = {}

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(HistoryRequest(instruments=(A,), field=PriceField.CLOSE, lookback=1),),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION}),
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.seen[ctx.now.day] = ctx.universe()
        return StrategyDecision.no_action(ctx.now)


BARS = tuple(make_bar(day(d), A, 100.0, 100.0) for d in (1, 2, 3))


def test_context_universe_changes_per_session() -> None:
    strategy = ProbeStrategy()
    engine = BacktestEngine(RunConfig(run_id="universe", initial_cash=1_000.0))
    engine.run(strategy, DataFeed(BARS), universe=universe())
    assert strategy.seen == {
        1: frozenset({A, C}),
        2: frozenset({A, C}),
        3: frozenset({A, B}),
    }


def test_context_universe_without_provider_raises() -> None:
    engine = BacktestEngine(RunConfig(run_id="universe", initial_cash=1_000.0))
    with pytest.raises(UniverseNotProvided, match="universe"):
        engine.run(ProbeStrategy(), DataFeed(BARS))


# --- KRX 마스터 어댑터 ---------------------------------------------------------

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from backtest_engine.adapters.krx_parquet import (  # noqa: E402  # reason: importorskip 이후 import
    KOSDAQ_MASTER_FILE,
    KOSPI_MASTER_FILE,
    KrxParquetUniverseSource,
)

MasterRow = tuple[date, str, str]  # bas_dd_req, isu_srt_cd, secugrp_nm


def write_master(path: Path, rows: list[MasterRow]) -> None:
    table = pa.table(
        {
            "bas_dd_req": pa.array([r[0] for r in rows], type=pa.date32()),
            "isu_srt_cd": pa.array([r[1] for r in rows], type=pa.string()),
            "isu_cd": pa.array(["KR7" + r[1] + "003" for r in rows], type=pa.string()),
            "secugrp_nm": pa.array([r[2] for r in rows], type=pa.string()),
            "mkt_tp_nm": pa.array(["KOSPI"] * len(rows), type=pa.string()),
            "list_dd": pa.array([date(2000, 1, 1)] * len(rows), type=pa.date32()),
        }
    )
    pq.write_table(table, path)


def d(day_of_month: int) -> date:
    return date(2026, 8, day_of_month)


class TestKrxUniverseAdapter:
    def test_intervals_from_daily_snapshots_across_both_files(self, tmp_path: Path) -> None:
        write_master(
            tmp_path / KOSPI_MASTER_FILE,
            [
                (d(1), "005930", "주권"),
                (d(3), "005930", "주권"),
                (d(2), "005930", "주권"),
                (d(1), "008080", "주권"),
                (d(2), "008080", "주권"),
            ],
        )
        write_master(tmp_path / KOSDAQ_MASTER_FILE, [(d(3), "247540", "주권")])
        result = KrxParquetUniverseSource(tmp_path).load_universe(UniverseQuery(venue="XKRX"))
        assert result.ok, result.detail
        by_symbol = {
            m.instrument.symbol: (m.first_session, m.last_session) for m in result.memberships
        }
        assert by_symbol == {
            "005930": (d(1), d(3)),
            "008080": (d(1), d(2)),
            "247540": (d(3), d(3)),
        }
        assert all(m.instrument.venue == "XKRX" for m in result.memberships)

    def test_security_group_filter(self, tmp_path: Path) -> None:
        write_master(
            tmp_path / KOSPI_MASTER_FILE,
            [(d(1), "005930", "주권"), (d(1), "005935", "우선주")],
        )
        result = KrxParquetUniverseSource(
            tmp_path, security_groups=frozenset({"주권"})
        ).load_universe(UniverseQuery(venue="XKRX"))
        assert [m.instrument.symbol for m in result.memberships] == ["005930"]

    def test_period_filter_clips_intervals(self, tmp_path: Path) -> None:
        write_master(
            tmp_path / KOSPI_MASTER_FILE, [(d(n), "005930", "주권") for n in (1, 2, 3, 4, 5)]
        )
        result = KrxParquetUniverseSource(tmp_path).load_universe(
            UniverseQuery(venue="XKRX", start=d(2), end=d(4))
        )
        (membership,) = result.memberships
        assert (membership.first_session, membership.last_session) == (d(2), d(4))

    def test_missing_files_is_no_data(self, tmp_path: Path) -> None:
        result = KrxParquetUniverseSource(tmp_path).load_universe(UniverseQuery(venue="XKRX"))
        assert result.status is LoadStatus.NO_DATA

    def test_empty_after_filter_is_no_data(self, tmp_path: Path) -> None:
        write_master(tmp_path / KOSPI_MASTER_FILE, [(d(1), "005930", "주권")])
        result = KrxParquetUniverseSource(tmp_path).load_universe(
            UniverseQuery(venue="XKRX", start=d(5), end=d(6))
        )
        assert result.status is LoadStatus.NO_DATA


def test_fixture_master_slice_smoke() -> None:
    """슬라이스 마스터가 어댑터를 통해 읽히고 API가 예외 없이 도는지만 본다 (값 단언 없음)."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "krx_parquet"
    result = KrxParquetUniverseSource(fixture).load_universe(UniverseQuery(venue="XKRX"))
    assert result.ok, result.detail
    assert result.memberships
    first = result.memberships[0]
    assert first.instrument in result.members(first.first_session)
    assert first.instrument not in result.members(
        date(first.last_session.year + 1, first.last_session.month, first.last_session.day)
    )
    assert result.instruments_active_between(first.first_session, first.last_session)


class TestKrxUniverseGaps:
    def test_gap_in_daily_snapshots_splits_interval(self, tmp_path: Path) -> None:
        """DEFECT-209: 마스터에 다른 종목은 있는데 이 종목이 빠진 날은 구간을 끊는다."""
        write_master(
            tmp_path / KOSPI_MASTER_FILE,
            [(d(n), "000660", "주권") for n in (1, 2, 3, 4, 5)]
            + [(d(n), "005930", "주권") for n in (1, 2, 4, 5)],
        )
        result = KrxParquetUniverseSource(tmp_path).load_universe(UniverseQuery(venue="XKRX"))
        intervals = [
            (m.first_session, m.last_session)
            for m in result.memberships
            if m.instrument.symbol == "005930"
        ]
        assert intervals == [(d(1), d(2)), (d(4), d(5))]
        assert make_instrument("005930") not in result.members(d(3))
        assert make_instrument("000660") in result.members(d(3))
