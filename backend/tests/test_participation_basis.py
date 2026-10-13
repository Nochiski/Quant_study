"""참여 기준(spec D7, 검증 랩 V2-02): 엔진은 받은 기준 거래량에 참여율을 곱해 캡을 정한다.

손계산(초기 현금 100,000, 수수료 0, 참여율 10%, GTC 70% 목표): 8/1 종가 100 에 700주 주문.
- 기준 거래량이 있으면 캡은 floor(기준 거래량 × 0.1)다. 8/2 는 0주라 체결 없이 넘어가고, 8/3·8/4 는
  floor(3,000 × 0.1) = 300주, 8/5 는 남은 100주. 현금 100,000 − (300 × 130 + 300 × 90 + 100 × 95)
  = 24,500.
- 같은 bar 에서 기준 거래량만 빼면 세션 거래량 1,000주로 세션마다 100주다(부분 체결·이월 규칙은
  같다).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.types.market import Bar
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _columnar_feed
from strategy_workbench.application.backtest_run.facade.ports import MarketBarRecord
from tests.test_core_parity import CORES, GTC_TARGET_70PCT, LIQUIDITY_BARS
from tests.test_engine_golden import ScriptedStrategy


def _fills(core: str, bars: tuple[Bar, ...]) -> tuple[list[tuple[int, int, float]], float]:
    engine = BacktestEngine(
        RunConfig(run_id="participation", initial_cash=100_000.0),
        core=core,
        max_participation=0.1,
    )
    engine.run(ScriptedStrategy(script=(GTC_TARGET_70PCT,)), DataFeed(bars))
    store = engine.event_store
    fills = [(fill.ts.day, int(fill.quantity), fill.price) for fill in store.fills()]
    return fills, store.snapshots()[-1].cash


@pytest.mark.parametrize("core", CORES)
def test_cap_follows_the_liquidity_volume_and_carries_the_rest(core: str) -> None:
    assert _fills(core, LIQUIDITY_BARS) == (
        [(3, 300, 130.0), (4, 300, 90.0), (5, 100, 95.0)],
        24_500.0,
    )


@pytest.mark.parametrize("core", CORES)
def test_without_a_liquidity_volume_the_session_volume_caps(core: str) -> None:
    bars = tuple(replace(bar, liquidity_volume=None) for bar in LIQUIDITY_BARS)

    fills, _cash = _fills(core, bars)

    assert fills == [(2, 100, 110.0), (3, 100, 130.0), (4, 100, 90.0), (5, 100, 95.0)]


def test_bar_feed_carries_the_liquidity_column_only_when_some_bar_has_one() -> None:
    """열이 없으면(None) 세션 거래량이다 — 기준 거래량이 하나도 없는 feed 는 거래량 사본을
    싣지 않는다."""
    plain = tuple(replace(bar, liquidity_volume=None) for bar in LIQUIDITY_BARS)
    mixed = (replace(LIQUIDITY_BARS[0], liquidity_volume=None), *LIQUIDITY_BARS[1:])

    assert DataFeed(plain).columns().liquidity_volumes is None
    assert DataFeed(mixed).columns().liquidity_volumes == [1_000, 0, 3_000, 3_000, 3_000]


def test_negative_liquidity_volume_is_rejected() -> None:
    with pytest.raises(ValueError, match="liquidity_volume must be >= 0"):
        replace(LIQUIDITY_BARS[0], liquidity_volume=-1)
    columns = DataFeed(LIQUIDITY_BARS).columns()
    for bad in ([3_000], [-1, 0, 0, 0, 0]):
        with pytest.raises(ValueError, match="liquidity volumes"):
            DataFeed.from_columns(
                sessions=[datetime(2026, 8, index) for index in range(1, 6)],
                instruments=columns.instruments,
                offsets=columns.offsets,
                instrument_ids=columns.instrument_ids,
                opens=columns.opens,
                highs=columns.highs,
                lows=columns.lows,
                closes=columns.closes,
                volumes=columns.volumes,
                liquidity_volumes=bad,
            )


def test_workbench_feed_places_each_liquidity_volume_on_its_own_bar() -> None:
    """어댑터의 열 피드는 dataset 행 순서(종목 먼저)를 세션 순서로 펴므로, 기준 거래량을
    (세션, 종목)으로 찾아 같은 행에 싣는다."""
    sessions = (date(2026, 8, 3), date(2026, 8, 4))
    rows = [
        MarketBarRecord(session, security_id, 100.0, 100.0, 100.0, 100.0, 1_000)
        for security_id in ("B", "A")
        for session in sessions
    ]
    liquidity = {
        (sessions[0], "A"): 1,
        (sessions[0], "B"): 2,
        (sessions[1], "A"): 3,
        (sessions[1], "B"): 4,
    }

    feed = _columnar_feed(rows, liquidity)
    plain = _columnar_feed(rows)

    assert [
        [(bar.instrument.symbol, bar.liquidity_volume) for bar in snapshot.bars]
        for snapshot in feed.snapshots()
    ] == [[("B", 2), ("A", 1)], [("B", 4), ("A", 3)]]
    assert plain.columns().liquidity_volumes is None
