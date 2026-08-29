"""Python 코어 vs Rust 코어 동일성 (6a).

Rust 확장(`backtest_core`)이 설치돼 있지 않으면 rust 파라미터는 skip한다 — CI 기본은 Python.
설치: `uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release`
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.core import (
    PortfolioLedger,
    core_available,
    make_portfolio,
    make_pricing,
)
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.errors import CoreUnavailable, NegativeCashError, NegativePositionError
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.actions import (
    BasketAction,
    CancelOrder,
    GroupPolicy,
    ReplaceOrder,
    StrategyAction,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    CostAccrued,
    CostKind,
    FillEvent,
    OrderEvent,
    OrderStatus,
    StrategyEvent,
)
from backtest_engine.types.market import Bar, MarketSnapshot
from backtest_engine.types.orders import (
    LimitOrderRequest,
    OrderCore,
    Side,
    StopLimitOrderRequest,
    TimeInForce,
)
from backtest_engine.types.requirements import EngineFeature, EventKind, StrategyRequirements
from backtest_engine.types.strategy import StrategyContext
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


# --- 6c: 세션 루프 — 주문 생명주기·바스켓·자본변동 시나리오를 두 코어로 실행 ---------------


def _lifecycle(
    core_name: str,
    script: tuple[StrategyAction | None, ...],
    bars: tuple[Bar, ...],
    max_participation: float | None = None,
) -> tuple[BacktestEngine, BacktestResult]:
    from tests import test_order_lifecycle as lc

    engine = BacktestEngine(
        RunConfig(run_id="lc", initial_cash=100_000.0, fee_bps=10.0),
        core=core_name,
        max_participation=max_participation,
        slippage=FixedBpsSlippage(bps=5.0),
    )
    return engine, engine.run(lc.OrderScript(script), DataFeed(bars))


def _stop_bars() -> tuple[Bar, ...]:
    return (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0, volume=1_000),
        make_ohlc(day(2), INSTRUMENT, 110.0, 120.0, 105.0, 115.0, volume=1_000),
        make_ohlc(day(3), INSTRUMENT, 90.0, 95.0, 85.0, 88.0, volume=1_000),
        make_ohlc(day(4), INSTRUMENT, 90.0, 95.0, 85.0, 88.0, volume=1_000),
    )


def _stop_limit(quantity: int) -> StrategyAction:
    from tests import test_order_lifecycle as lc

    core = OrderCore(INSTRUMENT, Side.BUY, Decimal(quantity), TimeInForce.GTC)
    return lc.submit(
        StopLimitOrderRequest(core=core, stop_price=Decimal(105), limit_price=Decimal(120))
    )


def _basket(
    core_name: str, policy: GroupPolicy, gtc: bool
) -> tuple[BacktestEngine, BacktestResult]:
    from tests import test_basket as tb

    if gtc:
        basket = BasketAction(
            legs=(tb.gtc_leg(tb.A, Side.BUY, 10), tb.gtc_leg(tb.B, Side.SELL, 10)),
            group_policy=policy,
        )
    else:
        basket = tb.pair(policy)
    engine = BacktestEngine(
        RunConfig(run_id="bk", initial_cash=100_000.0, fee_bps=0.0),
        core=core_name,
        max_participation=0.1,
    )
    return engine, engine.run(tb.BasketStrategy((tb.hold_b(10), basket)), DataFeed(tb.BARS))


def _halted_split(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    from tests import test_corporate_action_engine as cae

    bars = (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), INSTRUMENT, 100.0, 106.0, 99.0, 105.0),
        make_ohlc(day(4), INSTRUMENT, 20.0, 21.0, 20.0, 21.0),
    )
    engine = BacktestEngine(
        RunConfig(run_id="split", initial_cash=10_000.0, fee_bps=0.0), core=core_name
    )
    strategy = cae.Strategy((cae.buy(7), cae.gtc_limit_buy(50.0)))
    return engine, engine.run(strategy, DataFeed(bars), corporate_actions=(cae.split("5"),))


def _session_scenarios() -> dict[str, Callable[[str], tuple[BacktestEngine, BacktestResult]]]:
    from tests import test_order_lifecycle as lc

    return {
        "gtc_limit": lambda c: _lifecycle(c, (lc.limit_buy(95.0, TimeInForce.GTC),), lc.BARS),
        "day_limit_expires": lambda c: _lifecycle(
            c, (lc.limit_buy(95.0, TimeInForce.DAY),), lc.BARS
        ),
        "stop_limit_partial": lambda c: _lifecycle(c, (_stop_limit(250),), _stop_bars(), 0.1),
        "ioc": lambda c: _lifecycle(c, (lc.market_buy(250, TimeInForce.IOC),), lc.BARS, 0.1),
        "fok": lambda c: _lifecycle(c, (lc.market_buy(250, TimeInForce.FOK),), lc.BARS, 0.1),
        "gtc_partial": lambda c: _lifecycle(
            c, (lc.market_buy(250, TimeInForce.GTC),), lc.BARS, 0.1
        ),
        "no_cash_gtc": lambda c: _lifecycle(
            c,
            (lc.market_buy(2, TimeInForce.GTC),),
            (
                make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
                make_ohlc(day(2), INSTRUMENT, 500.0, 500.0, 500.0, 500.0, volume=0),
                make_ohlc(day(3), INSTRUMENT, 50.0, 50.0, 50.0, 50.0),
            ),
            0.1,
        ),
        "basket_best_effort_gtc": lambda c: _basket(c, GroupPolicy.BEST_EFFORT, True),
        "basket_all_or_none": lambda c: _basket(c, GroupPolicy.ALL_OR_NONE, False),
        "basket_proportional": lambda c: _basket(c, GroupPolicy.PROPORTIONAL, False),
        "halted_split_with_open_order": _halted_split,
    }


def _records(engine: BacktestEngine) -> list[tuple[str, object]]:
    return [(r.kind.value, r.payload) for r in engine.event_store.records]


@RUST_ONLY
@pytest.mark.parametrize("name", sorted(_session_scenarios()))
def test_session_loop_records_identical_across_cores(name: str) -> None:
    scenario = _session_scenarios()[name]
    python_engine, python_result = scenario("python")
    rust_engine, rust_result = scenario("rust")
    assert python_result == rust_result
    assert _records(python_engine) == _records(rust_engine)


@RUST_ONLY
def test_custom_slippage_model_requires_python_core() -> None:
    class Custom:
        def slippage_per_share(
            self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
        ) -> float:
            return 0.0

    from tests import test_order_lifecycle as lc

    engine = BacktestEngine(
        RunConfig(run_id="custom", initial_cash=1_000.0), core="rust", slippage=Custom()
    )
    with pytest.raises(CoreUnavailable, match="built-in slippage"):
        engine.run(lc.OrderScript((None,)), DataFeed(lc.BARS))


# --- 6c 리뷰 반영: 추가 세션 시나리오 -------------------------------------------------


def _short_basket(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """보유 없이 매도 leg를 낸다 → 숏 진입 leg가 여력 캡을 받는 경로."""
    from tests import test_basket as tb

    class ShortBasketStrategy(tb.BasketStrategy):
        def requirements(self) -> StrategyRequirements:
            base = super().requirements()
            return StrategyRequirements(
                histories=base.histories,
                schedule=base.schedule,
                events=base.events,
                actions=base.actions,
                features=base.features | frozenset({EngineFeature.SHORT_SELLING}),
            )

    basket = BasketAction(
        legs=(tb.market_leg(tb.A, Side.BUY, 100), tb.market_leg(tb.B, Side.SELL, 1_500)),
        group_policy=GroupPolicy.BEST_EFFORT,
    )
    engine = BacktestEngine(
        RunConfig(run_id="short-bk", initial_cash=20_000.0, fee_bps=0.0), core=core_name
    )
    return engine, engine.run(ShortBasketStrategy((None, basket)), DataFeed(tb.BARS))


def _two_groups(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """한 세션에 그룹 2개 — 여력을 순서대로 나눠 쓴다."""
    from tests import test_basket as tb

    class TwoGroups(tb.BasketStrategy):
        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            if ctx.now == day(2):
                return StrategyDecision(
                    schema_version=1,
                    as_of=ctx.now,
                    actions=(
                        BasketAction(
                            legs=(tb.market_leg(tb.A, Side.BUY, 600),),
                            group_policy=GroupPolicy.ALL_OR_NONE,
                        ),
                        BasketAction(
                            legs=(
                                tb.market_leg(tb.A, Side.BUY, 300),
                                tb.market_leg(tb.B, Side.BUY, 100),
                            ),
                            group_policy=GroupPolicy.PROPORTIONAL,
                        ),
                    ),
                )
            return StrategyDecision.no_action(ctx.now)

    engine = BacktestEngine(
        RunConfig(run_id="two-groups", initial_cash=70_000.0, fee_bps=5.0),
        core=core_name,
        max_participation=0.5,
    )
    return engine, engine.run(TwoGroups(()), DataFeed(tb.BARS))


def _split_with_group(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """자본변동(B 분할)과 바스켓 그룹이 같은 세션에 — leg 소실 후 AON 판정."""
    from tests import test_basket as tb
    from tests import test_corporate_action_engine as cae

    split_b = CorporateActionEvent(
        ts=day(3),
        instrument=tb.B,
        action_type=CorporateActionType.SPLIT,
        ratio=Decimal(2),
        detail="t",
    )
    engine = BacktestEngine(
        RunConfig(run_id="split-bk", initial_cash=100_000.0, fee_bps=0.0),
        core=core_name,
        max_participation=0.1,
    )
    strategy = tb.BasketStrategy((tb.hold_b(10), tb.pair(GroupPolicy.ALL_OR_NONE)))
    result = engine.run(strategy, DataFeed(tb.BARS), corporate_actions=(split_b,))
    del cae  # 같은 픽스처 패턴(분할 이벤트)만 빌려 쓴다
    return engine, result


def _cancel_replace(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    from tests import test_order_lifecycle as lc

    def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
        if not isinstance(event, MarketSnapshot):
            return None
        if ctx.now == day(1):
            return lc.limit_buy(50.0, TimeInForce.GTC)
        if ctx.now == day(2) and ctx.open_orders():
            core = OrderCore(INSTRUMENT, Side.BUY, Decimal(10), TimeInForce.GTC)
            return ReplaceOrder(
                order_id=ctx.open_orders()[0].order_id,
                replacement=LimitOrderRequest(core=core, limit_price=Decimal(95)),
            )
        if ctx.now == day(3) and ctx.open_orders():
            return CancelOrder(order_id=ctx.open_orders()[0].order_id)
        return None

    engine = BacktestEngine(
        RunConfig(run_id="cancel-replace", initial_cash=100_000.0, fee_bps=0.0), core=core_name
    )
    strategy = lc.CallbackStrategy(
        handler, frozenset({EventKind.MARKET, EventKind.FILL, EventKind.ORDER_UPDATE})
    )
    return engine, engine.run(strategy, DataFeed(lc.BARS))


def _two_orders_notified(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """같은 세션에 서로 다른 두 주문이 체결될 때 FILL/ORDER_UPDATE 알림 순서."""
    from tests import test_order_lifecycle as lc

    def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
        if ctx.now == day(1):
            return None
        return None

    class TwoOrders(lc.CallbackStrategy):
        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            self.received.append(event)
            if ctx.now == day(1) and isinstance(event, MarketSnapshot):
                return StrategyDecision(
                    schema_version=1,
                    as_of=ctx.now,
                    actions=(
                        lc.market_buy(5, TimeInForce.DAY),
                        lc.limit_buy(120.0, TimeInForce.DAY, quantity=7),
                    ),
                )
            return StrategyDecision.no_action(ctx.now)

    engine = BacktestEngine(
        RunConfig(run_id="two-orders", initial_cash=100_000.0, fee_bps=0.0), core=core_name
    )
    strategy = TwoOrders(
        handler, frozenset({EventKind.MARKET, EventKind.FILL, EventKind.ORDER_UPDATE})
    )
    result = engine.run(strategy, DataFeed(lc.BARS))
    return engine, result


_EXTRA_SCENARIOS: dict[str, Callable[[str], tuple[BacktestEngine, BacktestResult]]] = {
    "short_entry_via_basket": _short_basket,
    "two_groups_one_session": _two_groups,
    "split_and_group_same_session": _split_with_group,
    "cancel_replace_rust": _cancel_replace,
    "two_orders_notification_order": _two_orders_notified,
}


@RUST_ONLY
@pytest.mark.parametrize("name", sorted(_EXTRA_SCENARIOS))
def test_extra_session_scenarios_identical_across_cores(name: str) -> None:
    scenario = _EXTRA_SCENARIOS[name]
    python_engine, python_result = scenario("python")
    rust_engine, rust_result = scenario("rust")
    assert python_result == rust_result
    assert _records(python_engine) == _records(rust_engine)


@RUST_ONLY
def test_rejected_no_cash_gtc_waits_in_rust_session() -> None:
    """리뷰 DEFECT-901: 여력 0(1주도 못 삼)이 실제로 REJECTED_NO_CASH → OPEN 경로를 탄다."""
    from tests import test_order_lifecycle as lc

    bars = (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), INSTRUMENT, 500.0, 500.0, 500.0, 500.0),
        make_ohlc(day(3), INSTRUMENT, 50.0, 50.0, 50.0, 50.0),
    )
    traces = []
    for core_name in ("python", "rust"):
        engine = BacktestEngine(
            RunConfig(run_id="nocash", initial_cash=300.0, fee_bps=0.0), core=core_name
        )
        result = engine.run(lc.OrderScript((lc.market_buy(2, TimeInForce.GTC),)), DataFeed(bars))
        updates = [(u.ts, u.status, u.detail) for u in engine.event_store.order_updates()]
        traces.append((updates, [(f.ts, int(f.quantity)) for f in result.fills]))
    assert traces[0] == traces[1]
    assert traces[0][0][0][1] is OrderStatus.OPEN
    assert "cannot afford" in (traces[0][0][0][2] or "")
