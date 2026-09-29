"""매도 거래세(spec D7, 검증 랩 V2-01): 엔진은 받은 세율 일정대로 매도 체결에만 부과한다.

골든 시나리오(`test_engine_golden`)를 손으로 계산한다. 8/1 결정 → 8/2 시가 110에 700주 매수,
8/3 청산 결정 → 8/4 시가 90에 700주 매도. 수수료 0.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.types.events import CostKind
from backtest_engine.types.orders import Side
from tests.test_core_parity import CORES
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
