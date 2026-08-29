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


@RUST_ONLY
def test_rule_table_identical_across_cores() -> None:
    python, rust = make_pricing("python"), make_pricing("rust")
    bar = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0)
    for _name, open_order, _expected in RULE_TABLE:
        assert python.execution_price(open_order.order, bar, open_order.triggered) == (
            rust.execution_price(open_order.order, bar, open_order.triggered)
        ), _name


@RUST_ONLY
def test_floor_delta_shares_identical() -> None:
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


@RUST_ONLY
def test_portfolio_scenario_identical_across_cores() -> None:
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


@RUST_ONLY
@pytest.mark.parametrize("name", sorted(ENGINE_SCENARIOS))
def test_engine_records_identical_across_cores(name: str) -> None:
    python, rust = ENGINE_SCENARIOS[name]("python"), ENGINE_SCENARIOS[name]("rust")
    assert python.snapshots == rust.snapshots
    assert python.fills == rust.fills
    assert python.orders == rust.orders
    assert python.metrics == rust.metrics


@RUST_ONLY
def test_multi_instrument_equity_is_bit_identical_in_insertion_order() -> None:
    """DEFECT-601/602: 포지션 2개 이상에서 합산 결합 순서와 삽입 순서가 Python과 같아야 한다."""
    a, b, c = make_instrument("005930"), make_instrument("000660"), make_instrument("247540")

    def scenario(portfolio: PortfolioLedger) -> tuple[object, ...]:
        fills = (
            (c, 3, 0.1),
            (b, 3, 7.3),
            (a, 155, 395.4286729267503),
            (b, 841, 536.3461223023825),
        )
        for seq, (instrument, quantity, price) in enumerate(fills, start=1):
            portfolio.apply(
                FillEvent(
                    fill_id=f"F-{seq:06d}",
                    order_id="O-000001",
                    ts=day(1),
                    instrument=instrument,
                    quantity=Decimal(quantity),
                    side=Side.BUY,
                    price=price,
                    fee=0.0,
                    slippage_per_share=0.0,
                )
            )
        portfolio.mark(
            make_snapshot(
                day(2),
                make_bar(day(2), a, 49.23813720318554, 49.23813720318554),
                make_bar(day(2), b, 366.32322799567294, 366.32322799567294),
                make_bar(day(2), c, 0.30000000000000004, 0.30000000000000004),
            )
        )
        s = portfolio.snapshot(day(2))
        return (s.cash, s.equity, s.gross_exposure, tuple(p.instrument.symbol for p in s.positions))

    python = scenario(make_portfolio("python", 3_305_944.3718483075, allow_short=True))
    rust = scenario(make_portfolio("rust", 3_305_944.3718483075, allow_short=True))
    assert python == rust


@RUST_ONLY
def test_instruments_differing_only_in_currency_are_distinct_positions() -> None:
    """DEFECT-603: venue:symbol만으로 키를 만들면 통화가 다른 종목이 합쳐진다."""
    from backtest_engine.types.instruments import AssetClass, InstrumentId

    krw = InstrumentId(venue="XKRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW")
    usd = InstrumentId(venue="XKRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="USD")
    results = []
    for core_name in ("python", "rust"):
        portfolio = make_portfolio(core_name, 1_000_000.0)
        for seq, instrument in enumerate((krw, usd), start=1):
            portfolio.apply(
                FillEvent(
                    fill_id=f"F-{seq:06d}",
                    order_id="O-000001",
                    ts=day(1),
                    instrument=instrument,
                    quantity=Decimal(10),
                    side=Side.BUY,
                    price=100.0,
                    fee=0.0,
                    slippage_per_share=0.0,
                )
            )
        s = portfolio.snapshot(day(1))
        results.append((len(s.positions), s.equity))
    assert results[0] == results[1] == (2, 1_000_000.0)


# --- 6b: 견적 산술 · 매수 여력 -------------------------------------------------------


@RUST_ONLY
@pytest.mark.parametrize("participation", [None, 0.7, 0.07, 0.29, 1.0])
@pytest.mark.parametrize("buying_power", [0.0, 500.0, 6_499.0, 1e9])
@pytest.mark.parametrize("held", [-30, 0, 5, 100])
def test_quote_numbers_identical_across_cores(
    participation: float | None, buying_power: float, held: int
) -> None:
    from backtest_engine.engine.core import make_quote_core

    python, rust = make_quote_core("python"), make_quote_core("rust")
    for side in Side:
        for limit in (None, Decimal(95), Decimal(105)):
            for fok in (False, True):
                args = (
                    side,
                    100.0,
                    Decimal(90),
                    90,
                    participation,
                    0.1,
                    limit,
                    buying_power,
                    Decimal(held),
                    0.001,
                    fok,
                )
                assert python.quote_numbers(*args) == rust.quote_numbers(*args), args


@RUST_ONLY
def test_buying_power_identical_across_cores() -> None:
    from backtest_engine.engine.core import make_buying_power
    from backtest_engine.types.portfolio import PortfolioSnapshot, Position

    a, b = make_instrument("005930"), make_instrument("000660")
    positions = (
        Position(
            a, Decimal(155), 395.4286729267503, 49.23813720318554, 155 * 49.23813720318554, 0.0
        ),
        Position(
            b, Decimal(-841), 536.3461223023825, 366.32322799567294, -841 * 366.32322799567294, 0.0
        ),
    )
    snapshot = PortfolioSnapshot(
        ts=day(1), cash=3_305_944.3718483075, positions=positions, equity=1.0, gross_exposure=0.0
    )
    trace = []
    for core_name in ("python", "rust"):
        power = make_buying_power(core_name, snapshot, 2.0)
        state = power.checkpoint()
        steps = [
            (a, Side.SELL, Decimal(200), 51.1, 1.3),
            (b, Side.BUY, Decimal(900), 360.0, 0.0),
            (make_instrument("247540"), Side.BUY, Decimal(7), 3.3, 0.01),
        ]
        values = []
        for instrument, side, quantity, price, fee in steps:
            power.consume_quantity(instrument, side, quantity, price, fee)
            values.append((power.available, power.quantity_of(instrument)))
        power.restore(state)
        values.append(power.available)
        trace.append(values)
    assert trace[0] == trace[1]
