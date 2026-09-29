"""√ 시장충격(spec D7, 검증 랩 V2-03): 엔진은 bar 의 척도에 체결가와 √체결 수량을 곱할 뿐이다.

손계산(초기 현금 100,000, 수수료 0, 캡 없음): 8/1 에 400주 매수 → 8/2 시가 110 에 체결, 척도 0.001.
주당 충격 110 × 0.001 × √400 = 2.2 라 매수가는 위로 112.2. 8/3 에 400주 매도 → 8/4 시가 90, 척도
0.0005. 주당 충격 90 × 0.0005 × 20 = 0.9 라 매도가는 아래로 89.1. 현금 100,000 − 44,880 + 35,640
= 90,760. 충격 합계 400 × 2.2 + 400 × 0.9 = 1,240 은 충격 없는 실행과의 현금 차이와 같다 — 체결가에
한 번만 들어가고 따로 빼지 않는다. 충격 상한은 domain 의 `MAX_IMPACT_FRACTION`(0.99)이다.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import SqrtImpactSlippage
from backtest_engine.errors import CoreUnavailable
from backtest_engine.types.actions import ActionKind
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.market import Bar
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _columnar_feed
from strategy_workbench.application.backtest_run.facade.ports import MarketBarRecord
from strategy_workbench.domain.backtest.facade.environment import MAX_IMPACT_FRACTION
from tests.conftest import day, make_bar
from tests.test_core_parity import CORES, IMPACT_BARS, INSTRUMENT
from tests.test_engine_golden import ScriptedStrategy, adjust_by

BARS = tuple(
    replace(make_bar(day(session), INSTRUMENT, price, price), impact_scale=scale)
    for session, price, scale in (
        (1, 100.0, None),
        (2, 110.0, 0.001),
        (3, 120.0, None),
        (4, 90.0, 0.0005),
    )
)


def _run(core: str, bars: tuple[Bar, ...]) -> BacktestEngine:
    engine = BacktestEngine(
        RunConfig(run_id="impact", initial_cash=100_000.0),
        core=core,
        slippage=SqrtImpactSlippage(MAX_IMPACT_FRACTION),
    )
    engine.run(
        ScriptedStrategy(
            script=(adjust_by(400), None, adjust_by(-400)),
            declared_actions=frozenset({ActionKind.NO_ACTION, ActionKind.ADJUST_POSITION}),
        ),
        DataFeed(bars),
    )
    return engine


@pytest.mark.parametrize("core", CORES)
def test_buy_fills_above_and_sell_below_by_price_times_scale_times_root_quantity(
    core: str,
) -> None:
    store = _run(core, BARS).event_store

    fills = [
        (fill.ts.day, fill.side.value, int(fill.quantity), fill.price, fill.slippage_per_share)
        for fill in store.fills()
    ]

    assert fills == [
        (2, "buy", 400, pytest.approx(112.2), pytest.approx(2.2)),
        (4, "sell", 400, pytest.approx(89.1), pytest.approx(0.9)),
    ]
    assert store.snapshots()[-1].cash == pytest.approx(90_760.0)


@pytest.mark.parametrize("core", CORES)
def test_impact_above_the_cap_keeps_the_sell_price_positive(core: str) -> None:
    """`session_volume` 참여에서는 Q 가 ADV 의 수백 배가 될 수 있다. 8/4 척도 0.06 은 k 1 × σ 3% /
    √ADV 0.25주에 해당하고, 400주 매도는 ADV 의 1,600배다. 충격 비율 0.06 × √400 = 1.2 는 상한
    0.99 에서 잘린다. 매도가 90 × (1 − 0.99) = 0.9 로 양수이고, 현금 100,000 − 44,880 + 360
    = 55,480 이다. 매수(8/2)는 상한 아래라 그대로 112.2 다. 모든 코어가 같은 값을 낸다."""
    bars = (*BARS[:3], replace(BARS[3], impact_scale=0.06))

    store = _run(core, bars).event_store

    assert [(fill.price, fill.slippage_per_share) for fill in store.fills()] == [
        (pytest.approx(112.2), pytest.approx(2.2)),
        (pytest.approx(0.9), pytest.approx(89.1)),
    ]
    assert store.snapshots()[-1].cash == pytest.approx(55_480.0)


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5])
def test_impact_cap_must_leave_the_sell_price_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="max_fraction"):
        SqrtImpactSlippage(bad)


@pytest.mark.parametrize("core", CORES)
def test_impact_is_counted_once_in_the_fill_price(core: str) -> None:
    """`total_slippage_cost` 는 체결가에 이미 든 충격을 보고할 뿐 현금에서 다시 빼지 않는다."""
    impacted = _run(core, BARS).event_store
    plain = _run(core, tuple(replace(bar, impact_scale=None) for bar in BARS)).event_store

    total = impacted.result_tables().fill_totals.total_slippage_cost

    assert total == pytest.approx(1_240.0)
    assert plain.snapshots()[-1].cash - impacted.snapshots()[-1].cash == pytest.approx(total)


def test_bar_feed_carries_the_impact_column_only_when_some_bar_has_one() -> None:
    plain = tuple(replace(bar, impact_scale=None) for bar in BARS)

    assert DataFeed(plain).columns().impact_scales is None
    assert DataFeed(BARS).columns().impact_scales == [0.0, 0.001, 0.0, 0.0005]


@pytest.mark.parametrize("bad", [-0.1, math.inf, math.nan])
def test_negative_or_non_finite_impact_scale_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="impact_scale must be finite"):
        replace(BARS[0], impact_scale=bad)
    columns = DataFeed(BARS).columns()
    with pytest.raises(ValueError, match="impact scales"):
        DataFeed.from_columns(
            sessions=[day(index) for index in range(1, 5)],
            instruments=columns.instruments,
            offsets=columns.offsets,
            instrument_ids=columns.instrument_ids,
            opens=columns.opens,
            highs=columns.highs,
            lows=columns.lows,
            closes=columns.closes,
            volumes=columns.volumes,
            impact_scales=[0.0, 0.0, 0.0, bad],
        )


@pytest.mark.parametrize("core", CORES)
def test_parity_scenario_really_pays_impact_in_both_directions(core: str) -> None:
    """`test_core_parity` 의 `sqrt_impact` 시나리오가 충격 0 인 체결만 비교하지 않는지 확인한다."""
    from tests.test_core_parity import ENGINE_SCENARIOS

    engine, _result = ENGINE_SCENARIOS["sqrt_impact"](core)
    slips = {(fill.side.value, fill.slippage_per_share > 0) for fill in engine.event_store.fills()}

    assert {("buy", True), ("sell", True)} <= slips
    assert any(bar.impact_scale is None for bar in IMPACT_BARS)


def test_rust_core_names_the_models_it_supports() -> None:
    """Rust 코어가 모르는 슬리피지 모델은 지원 목록과 함께 Python 코어로 돌리라고 거절한다."""

    class Custom:
        def slippage_per_share(
            self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
        ) -> float:
            return 0.0

    from backtest_engine.engine.core import slippage_config

    assert slippage_config(SqrtImpactSlippage(0.99)) == ("sqrt", 0.99, 0.0)
    with pytest.raises(CoreUnavailable) as error:
        slippage_config(Custom())
    assert "SqrtImpactSlippage" in str(error.value)
    assert 'use core="python"' in str(error.value)


def test_workbench_feed_places_each_impact_scale_on_its_own_bar() -> None:
    """어댑터의 열 피드는 dataset 행 순서(종목 먼저)를 세션 순서로 펴므로, 척도를 (세션, 종목)으로
    찾아 같은 행에 싣는다."""
    sessions = (date(2026, 8, 3), date(2026, 8, 4))
    rows = [
        MarketBarRecord(session, security_id, 100.0, 100.0, 100.0, 100.0, 1_000)
        for security_id in ("B", "A")
        for session in sessions
    ]
    impact = {
        (sessions[0], "A"): 0.1,
        (sessions[0], "B"): 0.2,
        (sessions[1], "A"): 0.3,
        (sessions[1], "B"): 0.4,
    }

    feed = _columnar_feed(rows, impact=impact)

    assert [
        [(bar.instrument.symbol, bar.impact_scale) for bar in snapshot.bars]
        for snapshot in feed.snapshots()
    ] == [[("B", 0.2), ("A", 0.1)], [("B", 0.4), ("A", 0.3)]]
    assert _columnar_feed(rows).columns().impact_scales is None
