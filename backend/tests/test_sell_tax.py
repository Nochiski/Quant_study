"""매도 거래세(spec D7, 검증 랩 V2-01): 엔진은 받은 세율 일정대로 매도 체결에만 부과한다.

골든 시나리오(`test_engine_golden`)를 손으로 계산한다. 8/1 결정 → 8/2 시가 110에 700주 매수,
8/3 청산 결정 → 8/4 시가 90에 700주 매도. 수수료 0.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.types.actions import (
    ExecutionPolicy,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.events import CostKind
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.orders import Side
from tests.conftest import day, make_instrument, make_ohlc
from tests.test_core_parity import CORES, _TwoNameReplaceStrategy
from tests.test_engine_golden import GOLDEN_BARS, ScriptedStrategy, liquidate, target_70pct


def _run(core: str, schedule: tuple[tuple[date, float], ...]) -> BacktestEngine:
    engine = BacktestEngine(
        RunConfig(run_id="sell-tax", initial_cash=100_000.0, sell_tax_schedule=schedule),
        core=core,
    )
    engine.run(
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)), DataFeed(GOLDEN_BARS)
    )
    return engine


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize(
    ("schedule", "expected_tax"),
    [
        # 8/4 부터 25bp: 매도일이 경계일이면 새 세율. 700 × 90 × 0.0025 = 157.5
        (((date(2026, 8, 1), 20.0), (date(2026, 8, 4), 25.0)), 157.5),
        # 8/5 부터 25bp: 매도일(8/4)은 경계 전이라 옛 세율. 700 × 90 × 0.0020 = 126.0
        (((date(2026, 8, 1), 20.0), (date(2026, 8, 5), 25.0)), 126.0),
    ],
)
def test_only_the_sell_fill_is_taxed_at_the_rate_of_its_session(
    core: str, schedule: tuple[tuple[date, float], ...], expected_tax: float
) -> None:
    engine = _run(core, schedule)
    store = engine.event_store

    assert [(fill.side, int(fill.quantity), fill.price) for fill in store.fills()] == [
        (Side.BUY, 700, 110.0),
        (Side.SELL, 700, 90.0),
    ]
    taxes = store.costs()
    assert [(cost.kind, cost.ts.date(), cost.amount) for cost in taxes] == [
        (CostKind.SELL_TAX, date(2026, 8, 4), expected_tax)
    ]
    # 현금: 100,000 − 77,000(매수) + 63,000(매도) − 세금. 매수에는 세금이 없다.
    final = store.snapshots()[-1]
    assert final.cash == pytest.approx(86_000.0 - expected_tax)


@pytest.mark.parametrize("core", CORES)
def test_empty_schedule_and_dates_before_the_first_row_are_untaxed(core: str) -> None:
    assert _run(core, ()).event_store.costs() == ()
    assert _run(core, ((date(2026, 8, 5), 20.0),)).event_store.costs() == ()


@pytest.mark.parametrize(
    "schedule",
    [
        ((date(2026, 8, 4), 20.0), (date(2026, 8, 1), 25.0)),
        ((date(2026, 8, 1), 20.0), (date(2026, 8, 1), 25.0)),
        ((date(2026, 8, 1), -1.0),),
        ((date(2026, 8, 1), float("nan")),),
    ],
)
def test_unordered_or_negative_schedule_is_rejected(
    schedule: tuple[tuple[date, float], ...],
) -> None:
    with pytest.raises(ValueError, match="sell_tax_schedule"):
        RunConfig(run_id="bad", initial_cash=1.0, sell_tax_schedule=schedule)


_A, _B = make_instrument("005930"), make_instrument("000660")


def _all_in(instrument: InstrumentId) -> SetPortfolioTarget:
    return SetPortfolioTarget(
        targets=(WeightTarget(instrument, 1.0),),
        scope=TargetScope.REPLACE,
        execution=ExecutionPolicy.market_next_open(),
    )


@pytest.mark.parametrize("core", CORES)
def test_same_session_switch_buys_only_what_is_left_after_the_sell_tax(core: str) -> None:
    """매도 대금으로 같은 세션에 100% 갈아타면, 매수는 세금을 뺀 여력만큼만 산다.

    8/2 A 1000주 @100 매수(현금 0) → 8/3 A 1000주 @100 매도(대금 100,000, 세금 20bp = 200) →
    같은 세션 B @50 매수. 여력 99,800 / 50 = 1996주. 세금을 여력에서 빼지 않으면 2000주를 사고
    현금이 −200 이 된다.
    """
    bars = tuple(
        bar
        for session in (1, 2, 3, 4)
        for bar in (
            make_ohlc(day(session), _A, 100.0, 100.0, 100.0, 100.0, volume=100_000),
            make_ohlc(day(session), _B, 50.0, 50.0, 50.0, 50.0, volume=100_000),
        )
    )
    engine = BacktestEngine(
        RunConfig(
            run_id="switch",
            initial_cash=100_000.0,
            sell_tax_schedule=((date(2026, 8, 1), 20.0),),
        ),
        core=core,
    )
    engine.run(_TwoNameReplaceStrategy((_all_in(_A), _all_in(_B), None, None)), DataFeed(bars))
    store = engine.event_store

    assert [
        (fill.instrument.symbol, fill.side, int(fill.quantity), fill.price)
        for fill in store.fills()
    ] == [
        ("005930", Side.BUY, 1000, 100.0),
        ("005930", Side.SELL, 1000, 100.0),
        ("000660", Side.BUY, 1996, 50.0),
    ]
    assert [(cost.kind, cost.amount) for cost in store.costs()] == [(CostKind.SELL_TAX, 200.0)]
    assert [snapshot.cash for snapshot in store.snapshots()][-1] == 0.0
