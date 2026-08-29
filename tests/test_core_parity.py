"""Python 코어 vs Rust 코어 동일성 (6a).

Rust 확장(`backtest_core`)이 설치돼 있지 않으면 rust 파라미터는 skip한다 — CI 기본은 Python.
설치: `uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release`
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.core import (
    PortfolioLedger,
    core_available,
    make_portfolio,
    make_pricing,
)
from backtest_engine.errors import CoreUnavailable, NegativeCashError, NegativePositionError
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    CostAccrued,
    CostKind,
    FillEvent,
)
from backtest_engine.types.orders import Side
from tests import test_basket, test_corporate_action_engine, test_margin, test_short_selling
from tests.conftest import day, make_bar, make_instrument, make_ohlc, make_snapshot
from tests.test_broker import RULE_TABLE
from tests.test_engine_golden import GOLDEN_BARS, ScriptedStrategy, liquidate, target_70pct

INSTRUMENT = make_instrument()
RUST_ONLY = pytest.mark.skipif(not core_available("rust"), reason="backtest_core 확장 없음")
CORES = ["python", pytest.param("rust", marks=RUST_ONLY)]


@pytest.fixture(params=CORES)
def core(request: pytest.FixtureRequest) -> str:
    return request.param


# --- execution_price ------------------------------------------------------------


def test_rule_table_identical_across_cores() -> None:
    if not core_available("rust"):
        pytest.skip("backtest_core 확장 없음")
    python, rust = make_pricing("python"), make_pricing("rust")
    bar = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0)
    for _name, open_order, _expected in RULE_TABLE:
        assert python.execution_price(open_order.order, bar, open_order.triggered) == (
            rust.execution_price(open_order.order, bar, open_order.triggered)
        ), _name


def test_floor_delta_shares_identical() -> None:
    if not core_available("rust"):
        pytest.skip("backtest_core 확장 없음")
    import backtest_core

    for notional, price in ((70_000.0, 9_999.0), (-450.0, 100.0), (0.0, 1.0), (1e12, 3.0)):
        assert int(floor_delta_shares(notional, price)) == backtest_core.floor_delta_shares(
            notional, price
        )


# --- Portfolio -------------------------------------------------------------------


def fill(side: Side, quantity: int, price: float, fee: float = 0.0, seq: int = 1) -> FillEvent:
    return FillEvent(
        fill_id=f"F-{seq:06d}",
        order_id="O-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        price=price,
        fee=fee,
        slippage_per_share=0.0,
    )


def scenario(portfolio: PortfolioLedger) -> list[tuple[object, ...]]:
    """롱 → 추가 → 부분 매도 → 숏 전환 → 비용 → 분할 → 마크. 각 단계의 스냅샷 튜플을 모은다."""
    trace: list[tuple[object, ...]] = []

    def record() -> None:
        s = portfolio.snapshot(day(1))
        trace.append(
            (
                s.cash,
                tuple(
                    (
                        p.instrument.symbol,
                        p.quantity,
                        p.average_price,
                        p.market_price,
                        p.market_value,
                        p.unrealized_pnl,
                    )
                    for p in s.positions
                ),
                s.equity,
                s.gross_exposure,
            )
        )

    portfolio.apply(fill(Side.BUY, 10, 100.0, fee=1.5))
    record()
    portfolio.apply(fill(Side.BUY, 5, 130.0, seq=2))
    record()
    portfolio.mark(make_snapshot(day(2), make_bar(day(2), INSTRUMENT, 120.0, 125.0)))
    record()
    portfolio.apply(fill(Side.SELL, 8, 110.0, seq=3))
    record()
    portfolio.apply(fill(Side.SELL, 12, 115.0, fee=0.3, seq=4))  # 7 → −5 방향 전환
    record()
    portfolio.charge(
        CostAccrued(ts=day(2), kind=CostKind.SHORT_BORROW, instrument=INSTRUMENT, amount=0.07)
    )
    record()
    portfolio.apply(fill(Side.BUY, 5, 90.0, seq=5))  # 환매 → flat
    record()
    portfolio.apply(fill(Side.BUY, 7, 100.0, seq=6))
    action = CorporateActionEvent(
        ts=day(3),
        instrument=INSTRUMENT,
        action_type=CorporateActionType.SPLIT,
        ratio=Decimal("1.5"),
        detail="t",
    )
    applied = portfolio.apply_corporate_action(action, 60.0)
    assert applied is not None
    trace.append(
        (
            applied.old_quantity,
            applied.new_quantity,
            applied.old_average_price,
            applied.new_average_price,
            applied.cash_paid,
        )
    )
    record()
    return trace


def test_portfolio_scenario_identical_across_cores() -> None:
    if not core_available("rust"):
        pytest.skip("backtest_core 확장 없음")
    python = scenario(make_portfolio("python", 100_000.0, allow_short=True, allow_margin=False))
    rust = scenario(make_portfolio("rust", 100_000.0, allow_short=True, allow_margin=False))
    assert python == rust


def test_portfolio_errors_map_to_domain_exceptions(core: str) -> None:
    portfolio = make_portfolio(core, 500.0)
    with pytest.raises(NegativeCashError, match="cash"):
        portfolio.apply(fill(Side.BUY, 10, 100.0))
    with pytest.raises(NegativePositionError, match="sell"):
        portfolio.apply(fill(Side.SELL, 1, 100.0))


def test_unavailable_core_is_an_error_not_a_fallback() -> None:
    with pytest.raises(CoreUnavailable, match="nope"):
        make_portfolio("nope", 1.0)


# --- Engine result diff ---------------------------------------------------------


ENGINE_SCENARIOS = {
    "golden": lambda core: BacktestEngine(
        RunConfig(run_id="g", initial_cash=100_000.0, fee_bps=10.0), core=core
    ).run(
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)), DataFeed(GOLDEN_BARS)
    ),
    "short": lambda core: BacktestEngine(
        RunConfig(run_id="s", initial_cash=10_000.0, fee_bps=0.0, short_borrow_bps_annual=252.0),
        core=core,
    ).run(
        test_short_selling.ShortStrategy(
            (test_short_selling.target(-10), test_short_selling.target(0))
        ),
        DataFeed(test_short_selling.BARS),
    ),
    "margin": lambda core: BacktestEngine(test_margin.config(), core=core).run(
        test_margin.MarginStrategy((test_margin.target(150), test_margin.target(0))),
        DataFeed(test_margin.BARS),
    ),
    "basket": lambda core: BacktestEngine(
        RunConfig(run_id="b", initial_cash=100_000.0, fee_bps=0.0), max_participation=0.1, core=core
    ).run(
        test_basket.BasketStrategy(
            (test_basket.hold_b(10), test_basket.pair(test_basket.GroupPolicy.PROPORTIONAL))
        ),
        DataFeed(test_basket.BARS),
    ),
    "split": lambda core: BacktestEngine(
        RunConfig(run_id="c", initial_cash=10_000.0, fee_bps=0.0), core=core
    ).run(
        test_corporate_action_engine.Strategy((test_corporate_action_engine.buy(7),)),
        DataFeed(test_corporate_action_engine.bars_with_split(60.0, 70.0)),
        corporate_actions=(test_corporate_action_engine.split("1.5"),),
    ),
}


@pytest.mark.parametrize("name", sorted(ENGINE_SCENARIOS))
def test_engine_records_identical_across_cores(name: str) -> None:
    if not core_available("rust"):
        pytest.skip("backtest_core 확장 없음")
    python, rust = ENGINE_SCENARIOS[name]("python"), ENGINE_SCENARIOS[name]("rust")
    assert python.snapshots == rust.snapshots
    assert python.fills == rust.fills
    assert python.orders == rust.orders
    assert python.metrics == rust.metrics
