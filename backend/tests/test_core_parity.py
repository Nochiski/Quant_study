"""Python 코어 vs Rust 코어 동일성 (6a).

Rust 확장(`backtest_core`)이 설치돼 있지 않으면 rust 파라미터는 skip한다 — CI 기본은 Python.
설치: `uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release`
"""

from __future__ import annotations

import random
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.core import (
    PortfolioLedger,
    core_available,
    make_persistent_runtime,
    make_portfolio,
    make_pricing,
)
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.errors import (
    CoreUnavailable,
    NegativeCashError,
    NegativePositionError,
    RustCorePanic,
    SchemaVersionMismatch,
    UndeclaredActionReturned,
    UnknownOrderId,
    UnsupportedActionValue,
)
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    BasketAction,
    CancelOrder,
    ExecutionPolicy,
    GroupPolicy,
    LiquidatePosition,
    LiquidationPersistence,
    QuantityDelta,
    QuantityTarget,
    ReplaceOrder,
    SetPortfolioTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
    TargetScope,
    WeightTarget,
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
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCore,
    Side,
    StopLimitOrderRequest,
    TimeInForce,
)
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import Strategy, StrategyContext
from tests import test_basket, test_corporate_action_engine, test_margin, test_short_selling
from tests.conftest import day, make_bar, make_instrument, make_ohlc, make_snapshot
from tests.test_broker import RULE_TABLE
from tests.test_engine_golden import (
    GOLDEN_BARS,
    ScriptedStrategy,
    adjust_by,
    liquidate,
    target_70pct,
)

INSTRUMENT = make_instrument()
RUST_ONLY = pytest.mark.skipif(not core_available("rust"), reason="backtest_core 확장 없음")
RUST_ENGINE_CORES = [
    pytest.param("rust", marks=RUST_ONLY, id="rust"),
    pytest.param("rust_persistent", marks=RUST_ONLY, id="rust_persistent"),
    pytest.param(
        "rust_legacy",
        marks=[
            RUST_ONLY,
            pytest.mark.filterwarnings("ignore:.*deprecated.*:DeprecationWarning"),
        ],
        id="rust_legacy",
    ),
]
CORES = ["python", *RUST_ENGINE_CORES]


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


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
def test_portfolio_scenario_identical_across_cores(rust_core: str) -> None:
    python = scenario(make_portfolio("python", 100_000.0, allow_short=True, allow_margin=False))
    rust = scenario(make_portfolio(rust_core, 100_000.0, allow_short=True, allow_margin=False))
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


def _engine_scenario(
    core: str,
    config: RunConfig,
    strategy: Strategy,
    feed: DataFeed,
    *,
    max_participation: float | None = None,
    corporate_actions: tuple[CorporateActionEvent, ...] | None = None,
) -> tuple[BacktestEngine, BacktestResult]:
    engine = BacktestEngine(config, core=core, max_participation=max_participation)
    result = (
        engine.run(strategy, feed)
        if corporate_actions is None
        else engine.run(strategy, feed, corporate_actions=corporate_actions)
    )
    return engine, result


class _TwoNameReplaceStrategy:
    """두 종목 REPLACE 목표를 순서대로 내는 전략.

    정지·상폐로 바가 끊긴 보유 종목의 청산 시나리오에 쓴다.
    """

    def __init__(self, script: tuple[StrategyAction | None, ...]) -> None:
        self._script = script
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(
                HistoryRequest(
                    instruments=(_DELIST_A, _DELIST_B), field=PriceField.CLOSE, lookback=1
                ),
            ),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now, "scripted_idle")
        return StrategyDecision.of(ctx.now, action, f"scripted_{index}")


_DELIST_A, _DELIST_B = make_instrument("005930"), make_instrument("000660")


def _delisted_replace_scenario(core: str) -> tuple[BacktestEngine, BacktestResult]:
    """D1 A·B 40% 씩 → D2 시가 체결. B 는 D2 가 마지막 바(상폐). D3 REPLACE 목표에 A 만 남기면
    B 청산 주문이 나오는데 그날 B 바가 없다 — 주문은 바가 올 때까지 대기해야지 run 이 죽으면 안
    된다(실데이터 028150 GS홈쇼핑 2021-07 상폐에서 Rust 코어만 실패)."""
    bars = (
        make_ohlc(day(1), _DELIST_A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
        make_ohlc(day(1), _DELIST_B, 50.0, 50.0, 50.0, 50.0, volume=1_000),
        make_ohlc(day(2), _DELIST_A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
        make_ohlc(day(2), _DELIST_B, 50.0, 50.0, 50.0, 50.0, volume=1_000),
        make_ohlc(day(3), _DELIST_A, 110.0, 110.0, 110.0, 110.0, volume=1_000),
        make_ohlc(day(4), _DELIST_A, 105.0, 105.0, 105.0, 105.0, volume=1_000),
    )
    both = SetPortfolioTarget(
        targets=(WeightTarget(_DELIST_A, 0.4), WeightTarget(_DELIST_B, 0.4)),
        scope=TargetScope.REPLACE,
        execution=ExecutionPolicy.market_next_open(),
    )
    only_a = SetPortfolioTarget(
        targets=(WeightTarget(_DELIST_A, 0.4),),
        scope=TargetScope.REPLACE,
        execution=ExecutionPolicy.market_next_open(),
    )
    return _engine_scenario(
        core,
        RunConfig(run_id="d", initial_cash=100_000.0, fee_bps=0.0),
        _TwoNameReplaceStrategy((both, None, only_a, None)),
        DataFeed(bars),
    )


ENGINE_SCENARIOS = {
    "golden": lambda core: _engine_scenario(
        core,
        RunConfig(run_id="g", initial_cash=100_000.0, fee_bps=10.0),
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)),
        DataFeed(GOLDEN_BARS),
    ),
    "short": lambda core: _engine_scenario(
        core,
        RunConfig(run_id="s", initial_cash=10_000.0, fee_bps=0.0, short_borrow_bps_annual=252.0),
        test_short_selling.ShortStrategy(
            (test_short_selling.target(-10), test_short_selling.target(0))
        ),
        DataFeed(test_short_selling.BARS),
    ),
    "margin": lambda core: _engine_scenario(
        core,
        test_margin.config(),
        test_margin.MarginStrategy((test_margin.target(150), test_margin.target(0))),
        DataFeed(test_margin.BARS),
    ),
    "basket": lambda core: _engine_scenario(
        core,
        RunConfig(run_id="b", initial_cash=100_000.0, fee_bps=0.0),
        test_basket.BasketStrategy(
            (test_basket.hold_b(10), test_basket.pair(test_basket.GroupPolicy.PROPORTIONAL))
        ),
        DataFeed(test_basket.BARS),
        max_participation=0.1,
    ),
    "delisted_replace": _delisted_replace_scenario,
    "split": lambda core: _engine_scenario(
        core,
        RunConfig(run_id="c", initial_cash=10_000.0, fee_bps=0.0),
        test_corporate_action_engine.Strategy((test_corporate_action_engine.buy(7),)),
        DataFeed(test_corporate_action_engine.bars_with_split(60.0, 70.0)),
        corporate_actions=(test_corporate_action_engine.split("1.5"),),
    ),
}


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
@pytest.mark.parametrize("name", sorted(ENGINE_SCENARIOS))
def test_engine_records_identical_across_cores(name: str, rust_core: str) -> None:
    python_engine, python = ENGINE_SCENARIOS[name]("python")
    rust_engine, rust = ENGINE_SCENARIOS[name](rust_core)
    assert python == rust
    assert python_engine.event_store.trace_bytes() == rust_engine.event_store.trace_bytes()
    assert (
        python_engine.event_store.decision_tape_bytes()
        == rust_engine.event_store.decision_tape_bytes()
    )


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


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
@pytest.mark.parametrize("name", sorted(_session_scenarios()))
def test_session_loop_records_identical_across_cores(name: str, rust_core: str) -> None:
    scenario = _session_scenarios()[name]
    python_engine, python_result = scenario("python")
    rust_engine, rust_result = scenario(rust_core)
    assert python_result == rust_result
    assert _records(python_engine) == _records(rust_engine)
    assert python_engine.event_store.trace_bytes() == rust_engine.event_store.trace_bytes()
    assert (
        python_engine.event_store.decision_tape_bytes()
        == rust_engine.event_store.decision_tape_bytes()
    )
    assert python_engine.event_store.trace_bytes() == rust_engine.event_store.trace_bytes()
    assert (
        python_engine.event_store.decision_tape_bytes()
        == rust_engine.event_store.decision_tape_bytes()
    )


@pytest.mark.parametrize("core_name", CORES)
def test_trace_serialization_is_byte_stable(core_name: str) -> None:
    scenario = _session_scenarios()["gtc_partial"]
    first_engine, first_result = scenario(core_name)
    second_engine, second_result = scenario(core_name)
    assert first_result == second_result
    assert first_engine.event_store.trace_bytes() == second_engine.event_store.trace_bytes()
    assert (
        first_engine.event_store.decision_tape_bytes()
        == second_engine.event_store.decision_tape_bytes()
    )


@RUST_ONLY
def test_persistent_basic_targets_bypass_python_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_python_route(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("persistent basic target unexpectedly used Python DecisionRouter")

    monkeypatch.setattr(DecisionRouter, "route", unexpected_python_route)
    engine = BacktestEngine(
        RunConfig(run_id="persistent-router", initial_cash=100_000.0, fee_bps=10.0),
        core="rust_persistent",
    )
    result = engine.run(
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)),
        DataFeed(GOLDEN_BARS),
    )
    assert len(result.orders) == 2
    assert len(result.fills) == 2

    adjust_engine = BacktestEngine(
        RunConfig(run_id="persistent-adjust", initial_cash=100_000.0, fee_bps=10.0),
        core="rust_persistent",
    )
    adjust_result = adjust_engine.run(
        ScriptedStrategy(
            script=(adjust_by(10), None, adjust_by(-4), None),
            declared_actions=frozenset({ActionKind.NO_ACTION, ActionKind.ADJUST_POSITION}),
        ),
        DataFeed(GOLDEN_BARS),
    )
    assert [int(fill.quantity) for fill in adjust_result.fills] == [10, 4]

    _, lifecycle_result = _session_scenarios()["gtc_limit"]("rust_persistent")
    assert lifecycle_result.orders[0].order_type.value == "limit"

    replace_engine, replace_result = _cancel_replace("rust_persistent")
    assert len(replace_result.orders) == 2
    statuses = [update.status for update in replace_engine.event_store.order_updates()]
    assert OrderStatus.REPLACED in statuses

    _, basket_result = _basket("rust_persistent", GroupPolicy.PROPORTIONAL, False)
    assert {order.group_id for order in basket_result.orders if order.group_id is not None} == {
        "G-000001"
    }


class _RandomActionStrategy:
    """All public action variants with deterministic randomized values."""

    def __init__(self, seed: int, first: InstrumentId, second: InstrumentId) -> None:
        self._rng = random.Random(seed)
        self._first = first
        self._second = second
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset(ActionKind),
            features=frozenset({EngineFeature.LIMIT_ORDER, EngineFeature.PROPORTIONAL_BASKET}),
        )

    @staticmethod
    def _request(
        instrument: InstrumentId,
        quantity: int,
        *,
        limit: int | None = None,
    ) -> MarketOrderRequest | LimitOrderRequest:
        core = OrderCore(instrument, Side.BUY, Decimal(quantity), TimeInForce.GTC)
        if limit is None:
            return MarketOrderRequest(core)
        return LimitOrderRequest(core, Decimal(limit))

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        del event
        phase = self._calls % 10
        self._calls += 1
        open_orders = ctx.open_orders()
        if phase == 0:
            action: StrategyAction = SubmitOrder(self._request(self._first, 2, limit=1))
        elif phase == 1 and open_orders:
            action = ReplaceOrder(
                open_orders[0].order_id,
                self._request(open_orders[0].instrument, 3, limit=2),
            )
        elif phase == 2 and open_orders:
            action = CancelOrder(open_orders[0].order_id)
        elif phase == 3:
            action = SetPortfolioTarget(
                targets=(WeightTarget(self._first, self._rng.choice((0.1, 0.25, 0.4))),),
                scope=TargetScope.PATCH,
                execution=ExecutionPolicy.market_next_open(),
            )
        elif phase == 4:
            action = SetPositionTarget(
                QuantityTarget(self._first, Decimal(self._rng.randrange(0, 12))),
                ExecutionPolicy.market_next_open(),
            )
        elif phase == 5:
            action = AdjustPosition(
                self._first,
                QuantityDelta(Decimal(self._rng.randrange(1, 4))),
                ExecutionPolicy.market_next_open(),
            )
        elif phase == 6:
            action = LiquidatePosition(
                self._first,
                ExecutionPolicy.market_next_available(),
                True,
                LiquidationPersistence.UNTIL_FLAT,
            )
        elif phase == 7:
            action = BasketAction(
                legs=(
                    SubmitOrder(self._request(self._first, self._rng.randrange(1, 4))),
                    SubmitOrder(self._request(self._second, self._rng.randrange(1, 4))),
                ),
                group_policy=self._rng.choice(tuple(GroupPolicy)),
            )
        elif phase == 9:
            action = SubmitOrder(self._request(self._second, self._rng.randrange(1, 4)))
        else:
            return StrategyDecision.no_action(ctx.now, f"random_phase_{phase}")
        return StrategyDecision.of(ctx.now, action, f"random_phase_{phase}")


@RUST_ONLY
@pytest.mark.parametrize("seed", range(10))
def test_all_actions_randomized_trace_matches_persistent_rust(seed: int) -> None:
    first, second = make_instrument("RND-A"), make_instrument("RND-B")
    bars = tuple(
        bar
        for session in range(1, 21)
        for bar in (
            make_bar(day(session), first, 100.0 + session, 100.5 + session, volume=10_000),
            make_bar(day(session), second, 80.0 + session, 80.5 + session, volume=10_000),
        )
    )

    def run(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
        engine = BacktestEngine(
            RunConfig(run_id=f"random-{seed}", initial_cash=1_000_000.0, fee_bps=10.0),
            core=core_name,
        )
        result = engine.run(_RandomActionStrategy(seed, first, second), DataFeed(bars))
        return engine, result

    python_engine, python_result = run("python")
    rust_engine, rust_result = run("rust_persistent")
    assert python_result == rust_result
    assert python_engine.event_store.trace_bytes() == rust_engine.event_store.trace_bytes()
    assert (
        python_engine.event_store.decision_tape_bytes()
        == rust_engine.event_store.decision_tape_bytes()
    )


@RUST_ONLY
def test_promoted_rust_sends_one_decision_batch_per_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest_engine.engine import loop as loop_module

    real_factory = loop_module.make_persistent_runtime
    proxies: list[Any] = []

    class CountingRuntime:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.route_calls = 0
            self.load_feed_calls = 0
            self.indexed_market_calls = 0
            self.legacy_market_calls = 0
            self.activate_pending_calls = 0
            self.place_order_calls = 0
            self.register_group_calls = 0

        def load_feed(self, *args: object) -> object:
            self.load_feed_calls += 1
            return self.inner.load_feed(*args)

        def route_basic_decision(self, *args: object) -> object:
            self.route_calls += 1
            return self.inner.route_basic_decision(*args)

        def submit_decision(self, *args: object) -> object:
            self.route_calls += 1
            return self.inner.submit_decision(*args)

        def process_market_index(self, *args: object) -> object:
            self.indexed_market_calls += 1
            return self.inner.process_market_index(*args)

        def process_market(self, *args: object) -> object:
            self.legacy_market_calls += 1
            return self.inner.process_market(*args)

        def activate_pending(self, *args: object) -> object:
            self.activate_pending_calls += 1
            return self.inner.activate_pending(*args)

        def place_order(self, *args: object) -> object:
            self.place_order_calls += 1
            return self.inner.place_order(*args)

        def register_group(self, *args: object) -> object:
            self.register_group_calls += 1
            return self.inner.register_group(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    def counting_factory(*args: Any, **kwargs: Any) -> CountingRuntime:
        proxy = CountingRuntime(real_factory(*args, **kwargs))
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr(loop_module, "make_persistent_runtime", counting_factory)
    engine = BacktestEngine(RunConfig(run_id="ffi-count", initial_cash=100_000.0), core="rust")
    engine.run(
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)),
        DataFeed(GOLDEN_BARS),
    )
    assert len(proxies) == 1
    assert proxies[0].route_calls == len(engine.event_store.decision_tape)
    assert proxies[0].load_feed_calls == 1
    assert proxies[0].indexed_market_calls == len(GOLDEN_BARS)
    assert proxies[0].legacy_market_calls == 0
    assert proxies[0].activate_pending_calls == 2
    assert proxies[0].place_order_calls == 0
    assert proxies[0].register_group_calls == 0
    assert proxies[0].inner.lifecycle_state() == "finished"


@RUST_ONLY
def test_rust_panic_becomes_engine_error_and_poisons_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest_engine.engine import loop as loop_module
    from backtest_engine.engine.store import RecordKind

    real_factory = loop_module.make_persistent_runtime
    runtimes: list[Any] = []

    class PanicRuntime:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def process_market_index(self, *_args: object) -> object:
            return self.inner._debug_force_panic()

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    def panic_factory(*args: Any, **kwargs: Any) -> PanicRuntime:
        runtime = PanicRuntime(real_factory(*args, **kwargs))
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(loop_module, "make_persistent_runtime", panic_factory)
    engine = BacktestEngine(RunConfig(run_id="rust-panic", initial_cash=100_000.0), core="rust")
    with pytest.raises(RustCorePanic, match="forced persistent runtime panic"):
        engine.run(ScriptedStrategy(script=(None,)), DataFeed(GOLDEN_BARS))

    assert runtimes[0].inner.lifecycle_state() == "failed"
    assert "Rust core panic" in runtimes[0].inner.failure_detail
    assert [record.kind for record in engine.event_store.records] == [RecordKind.MARKET]


@RUST_ONLY
def test_legacy_rust_core_is_explicitly_deprecated() -> None:
    engine = BacktestEngine(
        RunConfig(run_id="legacy-rust", initial_cash=100_000.0), core="rust_legacy"
    )
    with pytest.warns(DeprecationWarning) as warnings_seen:
        result = engine.run(ScriptedStrategy(script=(None,)), DataFeed(GOLDEN_BARS))
    messages = {str(warning.message) for warning in warnings_seen}
    assert any('use core="rust"' in message for message in messages)
    assert any("process_market() is deprecated" in message for message in messages)
    assert len(result.snapshots) == len(DataFeed(GOLDEN_BARS))


@pytest.mark.parametrize("core_name", ["python", pytest.param("rust", marks=RUST_ONLY)])
def test_empty_feed_fails_identically_after_clean_finish(core_name: str) -> None:
    engine = BacktestEngine(RunConfig(run_id="empty-feed", initial_cash=100_000.0), core=core_name)
    with pytest.raises(ValueError, match="cannot compute metrics from an empty run"):
        engine.run(ScriptedStrategy(script=()), DataFeed(()))
    assert engine.event_store.records == ()


@RUST_ONLY
def test_persistent_event_queue_matches_timestamp_priority_and_fifo_order() -> None:
    from backtest_engine.engine.queue import (
        EventPriority,
        MarketArrived,
        PersistentEventQueue,
        SessionClose,
    )

    runtime = make_persistent_runtime(
        100_000.0, allow_short=False, allow_margin=False, leverage=1.0
    )
    queue = PersistentEventQueue(runtime)
    late = MarketArrived(make_snapshot(day(2), make_bar(day(2), INSTRUMENT, 100.0, 100.0)))
    close = SessionClose(make_snapshot(day(1), make_bar(day(1), INSTRUMENT, 100.0, 100.0)))
    first = MarketArrived(make_snapshot(day(1), make_bar(day(1), INSTRUMENT, 100.0, 100.0)))
    second = MarketArrived(make_snapshot(day(1), make_bar(day(1), INSTRUMENT, 101.0, 101.0)))
    queue.push(day(2), EventPriority.MARKET, late)
    queue.push(day(1), EventPriority.SESSION_CLOSE, close)
    queue.push(day(1), EventPriority.MARKET, first)
    queue.push(day(1), EventPriority.MARKET, second)
    assert len(queue) == 4
    assert [queue.pop(), queue.pop(), queue.pop(), queue.pop()] == [
        first,
        second,
        close,
        late,
    ]


@RUST_ONLY
def test_persistent_callback_tokens_reject_stale_and_double_submit() -> None:
    from backtest_engine.engine.wire import decision_to_wire

    runtime = make_persistent_runtime(
        100_000.0, allow_short=False, allow_margin=False, leverage=1.0
    )
    runtime.configure_router([ActionKind.NO_ACTION.value], [])
    runtime.load_feed(
        ["XKRX:005930:equity:KRW"],
        ["005930"],
        [str(day(1))],
        [0, 1],
        [0],
        [100.0],
        [100.0],
        [100.0],
        [100.0],
        [1_000],
    )
    runtime.process_market_index(0, 0.0, None, ("none", 0.0, 0.0))
    frame = runtime.run_until_callback("market", 0)
    assert frame.token == 1
    assert frame.event_kind == "market"
    assert frame.session_index == 0
    decision = decision_to_wire(StrategyDecision.no_action(day(1), "token"))
    with pytest.raises(ValueError, match="stale callback token"):
        runtime.submit_decision(frame.token + 1, decision)
    runtime.submit_decision(frame.token, decision)
    with pytest.raises(ValueError, match="no callback is awaiting"):
        runtime.submit_decision(frame.token, decision)


@RUST_ONLY
def test_persistent_strategy_exception_poison_runtime_and_preserves_partial_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest_engine.engine import loop as loop_module
    from backtest_engine.engine.store import RecordKind

    real_factory = loop_module.make_persistent_runtime
    runtimes: list[Any] = []

    def capturing_factory(*args: Any, **kwargs: Any) -> Any:
        runtime = real_factory(*args, **kwargs)
        runtimes.append(runtime)
        return runtime

    class ExplodingStrategy:
        def requirements(self) -> StrategyRequirements:
            return StrategyRequirements(
                histories=(),
                schedule=EverySession(),
                events=frozenset({EventKind.MARKET}),
                actions=frozenset({ActionKind.NO_ACTION}),
                features=frozenset(),
            )

        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            from backtest_engine.engine.context import RustStrategyContext

            assert isinstance(ctx, RustStrategyContext)
            del event
            raise RuntimeError("strategy exploded")

    monkeypatch.setattr(loop_module, "make_persistent_runtime", capturing_factory)
    engine = BacktestEngine(
        RunConfig(run_id="callback-failure", initial_cash=100_000.0),
        core="rust_persistent",
    )
    with pytest.raises(RuntimeError, match="strategy exploded"):
        engine.run(ExplodingStrategy(), DataFeed(GOLDEN_BARS))
    assert runtimes[0].lifecycle_state() == "failed"
    assert runtimes[0].failure_detail == "RuntimeError: strategy exploded"
    assert [record.kind for record in engine.event_store.records] == [
        RecordKind.MARKET,
        RecordKind.SNAPSHOT,
    ]


@RUST_ONLY
def test_persistent_finish_keeps_order_fill_results_lazy_and_compact_traceable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest_engine.engine.compact import CompactFill, CompactOrder
    from backtest_engine.engine.store import PersistentEventStore, RecordKind

    finished = False
    materialized = {"order": 0, "fill": 0}
    real_finish = PersistentEventStore.finish
    real_order_materialize = CompactOrder.materialize
    real_fill_materialize = CompactFill.materialize

    def tracked_finish(store: PersistentEventStore) -> None:
        nonlocal finished
        real_finish(store)
        finished = True

    def tracked_order(order: CompactOrder) -> OrderEvent:
        assert finished, "public OrderEvent was allocated before finish()"
        materialized["order"] += 1
        return real_order_materialize(order)

    def tracked_fill(fill: CompactFill) -> FillEvent:
        assert finished, "public FillEvent was allocated before finish()"
        materialized["fill"] += 1
        return real_fill_materialize(fill)

    monkeypatch.setattr(PersistentEventStore, "finish", tracked_finish)
    monkeypatch.setattr(CompactOrder, "materialize", tracked_order)
    monkeypatch.setattr(CompactFill, "materialize", tracked_fill)

    engine = BacktestEngine(
        RunConfig(run_id="lazy-result", initial_cash=100_000.0, fee_bps=10.0),
        core="rust_persistent",
    )
    result = engine.run(
        ScriptedStrategy(script=(target_70pct(), None, liquidate(), None)),
        DataFeed(GOLDEN_BARS),
    )
    assert materialized == {"order": 0, "fill": 0}

    store = engine.event_store
    assert isinstance(store, PersistentEventStore)
    compact_trace = store.compact_trace()
    assert [row[0] for row in compact_trace] == list(range(len(compact_trace)))
    assert {
        RecordKind.MARKET.value,
        RecordKind.DECISION.value,
        RecordKind.ORDER.value,
        RecordKind.ORDER_UPDATE.value,
        RecordKind.FILL.value,
        RecordKind.SNAPSHOT.value,
    }.issubset({row[2] for row in compact_trace})
    assert materialized == {"order": 0, "fill": 0}

    assert isinstance(result.orders, tuple)
    assert materialized == {"order": 2, "fill": 0}
    assert isinstance(result.fills, tuple)
    assert materialized == {"order": 2, "fill": 2}
    assert tuple(record.kind.value for record in store.records) == tuple(
        row[2] for row in compact_trace
    )


class _FaultStrategy:
    def __init__(
        self,
        decision: StrategyDecision,
        declared_actions: frozenset[ActionKind],
    ) -> None:
        self._decision = decision
        self._declared_actions = declared_actions

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=self._declared_actions,
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        del ctx, event
        return self._decision


@RUST_ONLY
def test_persistent_router_error_type_order_and_trace_match_python() -> None:
    negative_target = SetPositionTarget(
        WeightTarget(INSTRUMENT, -0.25), ExecutionPolicy.market_next_open()
    )
    cases = (
        (
            StrategyDecision(schema_version=99, as_of=day(1), actions=()),
            frozenset({ActionKind.NO_ACTION}),
            SchemaVersionMismatch,
        ),
        (
            StrategyDecision.of(day(1), CancelOrder("O-999999")),
            frozenset({ActionKind.NO_ACTION}),
            UndeclaredActionReturned,
        ),
        (
            StrategyDecision.of(day(1), CancelOrder("O-999999")),
            frozenset({ActionKind.NO_ACTION, ActionKind.CANCEL_ORDER}),
            UnknownOrderId,
        ),
        (
            StrategyDecision.of(day(1), negative_target),
            frozenset({ActionKind.NO_ACTION, ActionKind.SET_POSITION_TARGET}),
            UnsupportedActionValue,
        ),
    )
    feed = DataFeed((make_bar(day(1), INSTRUMENT, 100.0, 100.0),))
    for decision, declared, expected_type in cases:
        failures: list[tuple[type[BaseException], str, bytes, bytes]] = []
        for core_name in ("python", "rust_persistent"):
            engine = BacktestEngine(
                RunConfig(run_id="router-error", initial_cash=100_000.0), core=core_name
            )
            with pytest.raises(expected_type) as caught:
                engine.run(_FaultStrategy(decision, declared), feed)
            failures.append(
                (
                    type(caught.value),
                    str(caught.value).partition(" — ")[0],
                    engine.event_store.trace_bytes(),
                    engine.event_store.decision_tape_bytes(),
                )
            )
        assert failures[0] == failures[1]


@RUST_ONLY
def test_persistent_retained_context_history_keeps_end_and_nan_alignment() -> None:
    import numpy as np

    first, second = make_instrument("HIST-A"), make_instrument("HIST-B")
    request = HistoryRequest((first, second), PriceField.CLOSE, 2)

    class RetainsContexts:
        def __init__(self) -> None:
            self.contexts: list[StrategyContext] = []

        def requirements(self) -> StrategyRequirements:
            return StrategyRequirements(
                histories=(request,),
                schedule=EverySession(),
                events=frozenset({EventKind.MARKET}),
                actions=frozenset({ActionKind.NO_ACTION}),
                features=frozenset(),
            )

        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            del event
            self.contexts.append(ctx)
            return StrategyDecision.no_action(ctx.now)

    bars = (
        make_bar(day(1), first, 10.0, 11.0),
        make_bar(day(1), second, 20.0, 21.0),
        make_bar(day(2), first, 11.0, 12.0),
        make_bar(day(3), first, 12.0, 13.0),
        make_bar(day(3), second, 22.0, 23.0),
    )
    windows = []
    for core_name in ("python", "rust_persistent"):
        strategy = RetainsContexts()
        BacktestEngine(
            RunConfig(run_id="retained-history", initial_cash=10_000.0), core=core_name
        ).run(strategy, DataFeed(bars))
        assert len(strategy.contexts) == 2
        first_window = strategy.contexts[0].history(request)
        last_window = strategy.contexts[-1].history(request)
        windows.append((first_window.timestamps, first_window.values.copy()))
        assert first_window.timestamps == (day(1), day(2))
        assert first_window.values[0, 1] == 21.0
        assert np.isnan(first_window.values[1, 1])
        assert last_window.timestamps == (day(2), day(3))
    assert windows[0][0] == windows[1][0]
    np.testing.assert_equal(windows[0][1], windows[1][1])


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
def test_custom_slippage_model_requires_python_core(rust_core: str) -> None:
    class Custom:
        def slippage_per_share(
            self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
        ) -> float:
            return 0.0

    from tests import test_order_lifecycle as lc

    engine = BacktestEngine(
        RunConfig(run_id="custom", initial_cash=1_000.0), core=rust_core, slippage=Custom()
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


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
@pytest.mark.parametrize("name", sorted(_EXTRA_SCENARIOS))
def test_extra_session_scenarios_identical_across_cores(name: str, rust_core: str) -> None:
    scenario = _EXTRA_SCENARIOS[name]
    python_engine, python_result = scenario("python")
    rust_engine, rust_result = scenario(rust_core)
    assert python_result == rust_result
    assert _records(python_engine) == _records(rust_engine)


def test_rejected_no_cash_gtc_waits_in_rust_session() -> None:
    """리뷰 DEFECT-901: 여력 0(1주도 못 삼)이 실제로 REJECTED_NO_CASH → OPEN 경로를 탄다."""
    from tests import test_order_lifecycle as lc

    bars = (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), INSTRUMENT, 500.0, 500.0, 500.0, 500.0),
        make_ohlc(day(3), INSTRUMENT, 50.0, 50.0, 50.0, 50.0),
    )
    traces = []
    for core_name in ("python", "rust", "rust_persistent"):
        engine = BacktestEngine(
            RunConfig(run_id="nocash", initial_cash=300.0, fee_bps=0.0), core=core_name
        )
        result = engine.run(lc.OrderScript((lc.market_buy(2, TimeInForce.GTC),)), DataFeed(bars))
        updates = [(u.ts, u.status, u.detail) for u in engine.event_store.order_updates()]
        traces.append((updates, [(f.ts, int(f.quantity)) for f in result.fills]))
    assert traces[0] == traces[1] == traces[2]
    assert traces[0][0][0][1] is OrderStatus.OPEN
    assert "cannot afford" in (traces[0][0][0][2] or "")


# --- 6c 리뷰(Opus) 반영 시나리오 -----------------------------------------------------


def _best_effort_leg_missing_bar(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """DEFECT-801: BEST_EFFORT 그룹의 bar 결측 leg는 다음 세션에 이어서 체결된다."""
    from tests import test_basket as tb

    bars = tuple(b for b in tb.BARS if not (b.instrument == tb.B and b.ts == day(3)))
    basket = BasketAction(
        legs=(tb.gtc_leg(tb.A, Side.BUY, 10), tb.gtc_leg(tb.B, Side.SELL, 10)),
        group_policy=GroupPolicy.BEST_EFFORT,
    )
    engine = BacktestEngine(
        RunConfig(run_id="be-missing", initial_cash=100_000.0, fee_bps=0.0),
        core=core_name,
        max_participation=0.1,
    )
    return engine, engine.run(tb.BasketStrategy((tb.hold_b(10), basket)), DataFeed(bars))


def _cash_limited_partial(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """DEFECT-802: CASH_LIMITED 진단의 buying_power는 체결 전 값."""
    from tests import test_order_lifecycle as lc

    engine = BacktestEngine(
        RunConfig(run_id="cash-lim", initial_cash=1_000.0, fee_bps=10.0), core=core_name
    )
    return engine, engine.run(
        lc.OrderScript((lc.market_buy(250, TimeInForce.GTC),)), DataFeed(lc.BARS)
    )


def _triggered_unfilled(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """DEFECT-803: TRIGGERED 진단의 stop/limit 표기 (Decimal 그대로)."""
    from tests import test_order_lifecycle as lc

    core = OrderCore(INSTRUMENT, Side.BUY, Decimal(10), TimeInForce.GTC)
    action = lc.submit(
        StopLimitOrderRequest(core=core, stop_price=Decimal("105.0"), limit_price=Decimal("106.50"))
    )
    engine = BacktestEngine(
        RunConfig(run_id="trig", initial_cash=100_000.0, fee_bps=0.0), core=core_name
    )
    return engine, engine.run(lc.OrderScript((action,)), DataFeed(_stop_bars()))


def _tiny_buying_power(core_name: str) -> tuple[BacktestEngine, BacktestResult]:
    """DEFECT-804: 진단의 float 표기가 지수 범위에서도 Python repr과 같다."""
    from tests import test_order_lifecycle as lc

    bars = (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), INSTRUMENT, 3.0, 3.0, 3.0, 3.0),
        make_ohlc(day(3), INSTRUMENT, 1e6, 1e6, 1e6, 1e6),
    )
    engine = BacktestEngine(
        RunConfig(run_id="tiny", initial_cash=2.000_01, fee_bps=0.0), core=core_name
    )
    return engine, engine.run(lc.OrderScript((lc.market_buy(5, TimeInForce.GTC),)), DataFeed(bars))


_OPUS_SCENARIOS: dict[str, Callable[[str], tuple[BacktestEngine, BacktestResult]]] = {
    "best_effort_leg_missing_bar": _best_effort_leg_missing_bar,
    "cash_limited_partial": _cash_limited_partial,
    "triggered_unfilled_stop_limit": _triggered_unfilled,
    "tiny_buying_power_repr": _tiny_buying_power,
}


@pytest.mark.parametrize("rust_core", RUST_ENGINE_CORES)
@pytest.mark.parametrize("name", sorted(_OPUS_SCENARIOS))
def test_opus_review_scenarios_identical_across_cores(name: str, rust_core: str) -> None:
    scenario = _OPUS_SCENARIOS[name]
    python_engine, python_result = scenario("python")
    rust_engine, rust_result = scenario(rust_core)
    assert python_result == rust_result
    assert _records(python_engine) == _records(rust_engine)


def test_execution_policy_validates_participation() -> None:
    from backtest_engine.types.actions import ExecutionPolicy, ExecutionStyle, ExecutionTiming

    with pytest.raises(ValueError, match="max_participation"):
        ExecutionPolicy(
            ExecutionStyle.MARKET, ExecutionTiming.NEXT_OPEN, TimeInForce.DAY, max_participation=1.5
        )
