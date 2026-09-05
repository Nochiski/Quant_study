"""코어 선택 (6a): 체결 가격 규칙과 포트폴리오 회계를 Python 또는 Rust 구현으로 제공한다.

Python 구현이 진실 원천이다. Rust 확장(`backtest_core`)은 선택 사항이며, 요청했는데 없으면
`CoreUnavailable`로 실패한다 — 조용히 Python으로 떨어지면 성능 비교가 거짓이 된다.
경계는 원시 타입뿐이다: 종목은 `"venue:symbol"` 키, 수량은 정수, 가격·현금은 float.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol, cast

from backtest_engine.engine.broker import (
    ExecutionPricing,
    ExecutionStatus,
    PythonQuoteCore,
    QuoteCore,
    QuoteNumbers,
)
from backtest_engine.engine.compact import CompactOrder
from backtest_engine.engine.orders import BasketGroup, OpenOrder, OrderManager
from backtest_engine.engine.portfolio import Portfolio as PythonPortfolio
from backtest_engine.engine.portfolio import scale_quantity
from backtest_engine.engine.pricing import PriceDecision, execution_price
from backtest_engine.engine.slippage import FixedBpsSlippage, NoSlippage, VolumeShareSlippage
from backtest_engine.errors import CoreUnavailable, NegativeCashError, NegativePositionError
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.types.actions import GroupPolicy
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    CostKind,
    FillEvent,
    OpenOrderSnapshot,
    OrderEvent,
)
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot, Position
from backtest_engine.types.requirements import EverySession, MonthEndSession, Schedule
from backtest_engine.types.results import RunConfig

CORES = ("python", "rust", "rust_legacy", "rust_persistent")
RUST_CORES = frozenset({"rust", "rust_legacy", "rust_persistent"})
PERSISTENT_RUST_CORES = frozenset({"rust", "rust_persistent"})


class PortfolioLedger(Protocol):
    """`engine.portfolio.Portfolio`와 같은 명령/조회 인터페이스."""

    def apply(self, fill: FillEvent) -> None: ...

    def charge(self, cost: CostAccrued) -> None: ...

    def apply_corporate_action(
        self,
        action: CorporateActionEvent,
        settlement_price: float,
        settled_at: datetime | None = None,
    ) -> CorporateActionApplied | None: ...

    def mark(self, snapshot: MarketSnapshot) -> None: ...

    @property
    def cash(self) -> float: ...

    def held_qty(self, instrument: InstrumentId) -> Decimal: ...

    def snapshot(self, ts: datetime) -> PortfolioSnapshot: ...


def core_available(core: str) -> bool:
    if core == "python":
        return True
    if core in RUST_CORES:
        return importlib.util.find_spec("backtest_core") is not None
    return False


def _require_core(core: str) -> None:
    if core not in CORES:
        raise CoreUnavailable(f"unknown core — core={core!r} available={CORES}")
    if not core_available(core):
        raise CoreUnavailable(
            f"core extension not installed — core={core!r}; build with "
            f"`uv run maturin develop --manifest-path "
            f"rust/backtest_core/Cargo.toml --release` from the backend directory"
        )


class PythonPricing:
    def execution_price(
        self, order: OrderEvent, bar: Bar, already_triggered: bool
    ) -> PriceDecision:
        return execution_price(order, bar, already_triggered)


class RustPricing:
    def __init__(self) -> None:
        self._core = importlib.import_module("backtest_core")

    def execution_price(
        self, order: OrderEvent, bar: Bar, already_triggered: bool
    ) -> PriceDecision:
        price, triggered = self._core.execution_price(
            order.order_type.value,
            order.side.value,
            bar.open,
            bar.high,
            bar.low,
            limit_price=None if order.limit_price is None else float(order.limit_price),
            stop_price=None if order.stop_price is None else float(order.stop_price),
            already_triggered=already_triggered,
        )
        return PriceDecision(price, triggered)


@functools.lru_cache(maxsize=65_536)
def instrument_key(instrument: InstrumentId) -> str:
    """InstrumentId의 모든 필드를 담는다 — 통화·자산군만 다른 종목이 합쳐지면 안 된다.

    InstrumentId는 불변·해시 가능하므로 메모한다 (세션마다 종목 수만큼 호출). 유니버스를
    갈아끼우는 스윕에서 무한히 자라지 않도록 상한을 둔다.
    """
    return (
        f"{instrument.venue}:{instrument.symbol}:{instrument.asset_class.value}:"
        f"{instrument.currency}"
    )


class RustPortfolio:
    """`backtest_core.Portfolio` 어댑터. 도메인 타입 변환·Decimal 산술·예외 매핑을 맡는다.

    포지션 순서·equity 합산 순서는 Rust 원장이 Python dict와 같은 삽입 순서로 보존한다.
    """

    def __init__(self, initial_cash: float, *, allow_short: bool, allow_margin: bool) -> None:
        core = importlib.import_module("backtest_core")
        self._inner = core.Portfolio(initial_cash, allow_short, allow_margin)
        self._instruments: dict[str, InstrumentId] = {}
        # (ts, snapshot) 메모 — Python Portfolio와 같은 규칙: 상태를 바꾸는 명령마다 비운다.
        self._snapshot_cache: tuple[datetime, PortfolioSnapshot] | None = None

    def apply(self, fill: FillEvent) -> None:
        if fill.quantity != fill.quantity.to_integral_value():
            raise ValueError(
                f"rust core supports integer share quantities only — fill_id={fill.fill_id} "
                f"quantity={fill.quantity}"
            )
        key = instrument_key(fill.instrument)
        try:
            self._inner.apply(key, fill.side.value, int(fill.quantity), fill.price, fill.fee)
        except ValueError as error:
            message = str(error)
            if message.startswith("negative_position:"):
                raise NegativePositionError(
                    f"{message.removeprefix('negative_position: ')} fill_id={fill.fill_id}"
                ) from error
            if message.startswith("negative_cash:"):
                raise NegativeCashError(
                    f"{message.removeprefix('negative_cash: ')} fill_id={fill.fill_id}"
                ) from error
            raise
        self._instruments.setdefault(key, fill.instrument)
        self._snapshot_cache = None

    def charge(self, cost: CostAccrued) -> None:
        self._inner.charge(cost.amount)
        self._snapshot_cache = None

    def apply_corporate_action(
        self,
        action: CorporateActionEvent,
        settlement_price: float,
        settled_at: datetime | None = None,
    ) -> CorporateActionApplied | None:
        key = instrument_key(action.instrument)
        old_quantity = Decimal(self._inner.held_qty(key))
        if old_quantity == 0:
            return None
        if settlement_price <= 0:
            raise ValueError(
                f"corporate action settlement price must be > 0 — "
                f"instrument={action.instrument.symbol} ts={action.ts} price={settlement_price}"
            )
        old_average = self._inner.average_price(key)
        if old_average is None:  # held_qty != 0이면 원장이 있으므로 방어용
            raise RuntimeError(f"rust portfolio has quantity without ledger — key={key}")
        scaled = scale_quantity(old_quantity, action.ratio)
        new_quantity = scaled.quantize(Decimal(1), rounding=ROUND_DOWN)  # 숏은 0 쪽으로
        cash_paid = float(scaled - new_quantity) * settlement_price
        new_average = old_average / float(action.ratio)
        self._inner.apply_corporate_action(
            key, int(new_quantity), new_average, cash_paid, settlement_price
        )
        self._snapshot_cache = None
        return CorporateActionApplied(
            ts=settled_at if settled_at is not None else action.ts,
            instrument=action.instrument,
            action=action,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            old_average_price=old_average,
            new_average_price=new_average,
            cash_paid=cash_paid,
        )

    def mark(self, snapshot: MarketSnapshot) -> None:
        self._inner.mark([(instrument_key(bar.instrument), bar.close) for bar in snapshot.bars])
        for bar in snapshot.bars:
            self._instruments.setdefault(instrument_key(bar.instrument), bar.instrument)
        self._snapshot_cache = None

    @property
    def cash(self) -> float:
        return self._inner.cash

    def held_qty(self, instrument: InstrumentId) -> Decimal:
        return Decimal(self._inner.held_qty(instrument_key(instrument)))

    def snapshot(self, ts: datetime) -> PortfolioSnapshot:
        if self._snapshot_cache is not None and self._snapshot_cache[0] == ts:
            return self._snapshot_cache[1]
        built = self._build_snapshot(ts)
        self._snapshot_cache = (ts, built)
        return built

    def _build_snapshot(self, ts: datetime) -> PortfolioSnapshot:
        cash, rows, equity, gross_exposure = self._inner.snapshot()
        # Rust 원장이 삽입 순서를 보존하므로 행 순서가 곧 Python dict 순서다.
        positions = tuple(
            Position(
                instrument=self._instruments[key],
                quantity=Decimal(quantity),
                average_price=average_price,
                market_price=market_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
            )
            for key, quantity, average_price, market_price, market_value, unrealized_pnl in rows
        )
        return PortfolioSnapshot(
            ts=ts, cash=cash, positions=positions, equity=equity, gross_exposure=gross_exposure
        )


class PersistentPortfolio:
    """`PersistentEngine`이 소유한 포트폴리오의 Python 도메인 어댑터."""

    def __init__(self, runtime: Any) -> None:
        self._inner = runtime
        self._instruments: dict[str, InstrumentId] = {}
        self._snapshot_cache: tuple[datetime, PortfolioSnapshot] | None = None
        self._feed_loaded = False

    def register_instruments(self, instruments: tuple[InstrumentId, ...]) -> None:
        for instrument in instruments:
            self._instruments.setdefault(instrument_key(instrument), instrument)
        self._feed_loaded = True

    def apply(self, fill: FillEvent) -> None:
        if fill.quantity != fill.quantity.to_integral_value():
            raise ValueError(
                f"rust core supports integer share quantities only — fill_id={fill.fill_id} "
                f"quantity={fill.quantity}"
            )
        key = instrument_key(fill.instrument)
        try:
            self._inner.apply_fill(
                key, fill.side.value, int(fill.quantity), fill.price, fill.fee
            )
        except ValueError as error:
            message = str(error)
            if message.startswith("negative_position:"):
                raise NegativePositionError(
                    f"{message.removeprefix('negative_position: ')} fill_id={fill.fill_id}"
                ) from error
            if message.startswith("negative_cash:"):
                raise NegativeCashError(
                    f"{message.removeprefix('negative_cash: ')} fill_id={fill.fill_id}"
                ) from error
            raise
        self._instruments.setdefault(key, fill.instrument)
        self._snapshot_cache = None

    def charge(self, cost: CostAccrued) -> None:
        self._inner.charge(cost.amount)
        self._snapshot_cache = None

    def apply_corporate_action(
        self,
        action: CorporateActionEvent,
        settlement_price: float,
        settled_at: datetime | None = None,
    ) -> CorporateActionApplied | None:
        key = instrument_key(action.instrument)
        old_quantity = Decimal(self._inner.held_qty(key))
        if old_quantity == 0:
            return None
        if settlement_price <= 0:
            raise ValueError(
                f"corporate action settlement price must be > 0 — "
                f"instrument={action.instrument.symbol} ts={action.ts} price={settlement_price}"
            )
        applied = self._inner.apply_corporate_action_ratio(
            key, str(action.ratio), settlement_price
        )
        if applied is None:
            return None
        old_quantity_raw, new_quantity_raw, old_average, new_average, cash_paid = applied
        old_quantity = Decimal(old_quantity_raw)
        new_quantity = Decimal(new_quantity_raw)
        self._snapshot_cache = None
        return CorporateActionApplied(
            ts=settled_at if settled_at is not None else action.ts,
            instrument=action.instrument,
            action=action,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            old_average_price=old_average,
            new_average_price=new_average,
            cash_paid=cash_paid,
        )

    def mark(self, snapshot: MarketSnapshot) -> None:
        if self._feed_loaded:
            self._inner.mark_current_session()
        else:
            self._inner.mark([(instrument_key(bar.instrument), bar.close) for bar in snapshot.bars])
            for bar in snapshot.bars:
                self._instruments.setdefault(instrument_key(bar.instrument), bar.instrument)
        self._snapshot_cache = None

    @property
    def cash(self) -> float:
        return float(self._inner.cash)

    def held_qty(self, instrument: InstrumentId) -> Decimal:
        return Decimal(self._inner.held_qty(instrument_key(instrument)))

    def snapshot(self, ts: datetime) -> PortfolioSnapshot:
        if self._snapshot_cache is not None and self._snapshot_cache[0] == ts:
            return self._snapshot_cache[1]
        cash, rows, equity, gross_exposure = self._inner.portfolio_snapshot()
        built = self._snapshot_from_wire(ts, cash, rows, equity, gross_exposure)
        self._snapshot_cache = (ts, built)
        return built

    def close_session(
        self, ts: datetime, config: RunConfig, schedule: Schedule
    ) -> tuple[bool, tuple[CostAccrued, ...], PortfolioSnapshot]:
        """종가 평가와 세션 비용 차감을 Rust에서 한 번에 수행한다."""
        if isinstance(schedule, EverySession):
            schedule_wire = "every_session"
        elif isinstance(schedule, MonthEndSession):
            schedule_wire = "month_end"
        else:
            raise TypeError(f"unsupported persistent schedule — got {type(schedule).__name__}")
        should_dispatch, cost_rows, snapshot_wire = self._inner.close_current_session(
            schedule_wire,
            config.short_borrow_bps_annual,
            config.margin_interest_bps_annual,
            config.annualization_days,
        )
        costs = tuple(
            CostAccrued(
                ts=ts,
                kind=CostKind(kind),
                instrument=None if key is None else self._instruments[key],
                amount=amount,
            )
            for kind, key, amount in cost_rows
        )
        cash, rows, equity, gross_exposure = snapshot_wire
        built = self._snapshot_from_wire(ts, cash, rows, equity, gross_exposure)
        self._snapshot_cache = (ts, built)
        return should_dispatch, costs, built

    def _snapshot_from_wire(
        self,
        ts: datetime,
        cash: float,
        rows: list[tuple[str, int, float, float, float, float]],
        equity: float,
        gross_exposure: float,
    ) -> PortfolioSnapshot:
        positions = tuple(
            Position(
                instrument=self._instruments[key],
                quantity=Decimal(quantity),
                average_price=average_price,
                market_price=market_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
            )
            for key, quantity, average_price, market_price, market_value, unrealized_pnl in rows
        )
        built = PortfolioSnapshot(
            ts=ts,
            cash=cash,
            positions=positions,
            equity=equity,
            gross_exposure=gross_exposure,
        )
        return built


class PersistentOrderManager(OrderManager):
    """mutable 주문 상태는 Rust runtime만 소유하고 Python에는 immutable OrderEvent만 보관한다."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._orders: dict[str, CompactOrder] = {}
        self._has_pending = False

    def _states(self) -> dict[str, tuple[int, bool]]:
        return {
            order_id: (remaining, triggered)
            for order_id, remaining, triggered in self._runtime.open_order_states()
        }

    def _entry(self, order_id: str, remaining: int, triggered: bool) -> OpenOrder:
        return OpenOrder(
            order=self._orders[order_id].materialize(),
            remaining=Decimal(remaining),
            triggered=triggered,
        )

    def next_decision_id(self) -> str:
        return cast(str, self._runtime.next_decision_id())

    def next_order_id(self) -> str:
        return cast(str, self._runtime.next_order_id())

    def next_fill_id(self) -> str:
        return cast(str, self._runtime.next_fill_id())

    def next_group_id(self) -> str:
        return cast(str, self._runtime.next_group_id())

    def register_group(self, group: BasketGroup) -> None:
        # Rust Router가 그룹을 같은 route 호출 안에서 pending 상태로 저장한다.
        del group
        self._has_pending = True

    def activate_pending(self) -> None:
        """ORDER priority에서 노출된 주문을 다음 MARKET 직전에 Rust에서 일괄 활성화한다."""
        if not self._has_pending:
            return
        self._runtime.activate_pending()
        self._has_pending = False

    def open_groups(self) -> tuple[BasketGroup, ...]:
        return tuple(
            BasketGroup(group_id, GroupPolicy(policy), tuple(order_ids))
            for group_id, policy, order_ids in self._runtime.open_group_states()
        )

    def group_entries(self, group_id: str) -> tuple[OpenOrder, ...]:
        states = self._states()
        group = next(group for group in self.open_groups() if group.group_id == group_id)
        return tuple(
            self._entry(order_id, *states[order_id])
            for order_id in group.order_ids
            if order_id in states
        )

    def drop_group(self, group_id: str) -> None:
        self._runtime.drop_group(group_id)

    def place(self, order: OrderEvent) -> None:
        if order.quantity != order.quantity.to_integral_value():
            raise ValueError(
                f"rust core supports integer share quantities only — "
                f"order_id={order.order_id} quantity={order.quantity}"
            )
        # mutable 주문은 route 호출에서 이미 Rust pending 영역에 저장됐다. 여기서는 공개
        # EventStore/context materialization에 필요한 immutable 객체만 기억한다.
        self.place_compact(CompactOrder.from_event(order))

    def place_compact(self, order: CompactOrder) -> None:
        self._orders[order.order_id] = order
        self._has_pending = True

    def order_compact(self, order_id: str) -> CompactOrder:
        return self._orders[order_id]

    def compact_open_entries(self) -> tuple[tuple[CompactOrder, int, bool], ...]:
        return tuple(
            (self._orders[order_id], remaining, triggered)
            for order_id, remaining, triggered in self._runtime.open_order_states()
        )

    def compact_open_orders(self) -> tuple[tuple[CompactOrder, int], ...]:
        return tuple(
            (order, remaining)
            for order, remaining, _triggered in self.compact_open_entries()
        )

    def open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        return tuple(
            OpenOrderSnapshot(order=entry.order, remaining=entry.remaining)
            for entry in self.open_entries()
        )

    def open_entries(self) -> tuple[OpenOrder, ...]:
        return tuple(
            self._entry(order_id, remaining, triggered)
            for order_id, remaining, triggered in self._runtime.open_order_states()
        )

    def get(self, order_id: str) -> OpenOrder | None:
        state = self._states().get(order_id)
        return None if state is None else self._entry(order_id, *state)

    def order_event(self, order_id: str) -> OrderEvent:
        return self._orders[order_id].materialize()

    def settle(self, order_id: str, filled: Decimal) -> Decimal:
        if filled != filled.to_integral_value():
            raise ValueError(
                f"rust core supports integer share quantities only — "
                f"order_id={order_id} quantity={filled}"
            )
        return Decimal(self._runtime.settle_order(order_id, int(filled)))

    def mark_triggered(self, order_id: str) -> None:
        self._runtime.mark_triggered(order_id)

    def remove(self, order_id: str) -> OpenOrder:
        _, remaining, triggered = self._runtime.remove_order(order_id)
        return self._entry(order_id, remaining, triggered)

    def cancel_for_instrument(self, instrument: InstrumentId) -> tuple[OrderEvent, ...]:
        return tuple(
            order.materialize() for order in self.cancel_compact_for_instrument(instrument)
        )

    def cancel_compact_for_instrument(
        self, instrument: InstrumentId
    ) -> tuple[CompactOrder, ...]:
        order_ids = self._runtime.cancel_for_key(instrument_key(instrument))
        return tuple(self._orders[order_id] for order_id in order_ids)

    def drain(self) -> tuple[OpenOrder, ...]:
        return tuple(
            OpenOrder(
                order=order.materialize(),
                remaining=Decimal(remaining),
                triggered=triggered,
            )
            for order, remaining, triggered in self.drain_compact()
        )

    def drain_compact(self) -> tuple[tuple[CompactOrder, int, bool], ...]:
        return tuple(
            (self._orders[order_id], remaining, triggered)
            for order_id, remaining, triggered in self._runtime.drain_orders()
        )


def make_persistent_runtime(
    initial_cash: float,
    *,
    allow_short: bool,
    allow_margin: bool,
    leverage: float,
) -> Any:
    _require_core("rust_persistent")
    core = importlib.import_module("backtest_core")
    return core.PersistentEngine(initial_cash, allow_short, allow_margin, leverage)


PowerState = tuple[float, float, dict[InstrumentId, Decimal], dict[InstrumentId, float]]


class BuyingPowerTracker(Protocol):
    """한 세션 안에서 체결이 진행될 때의 매수 여력 = leverage × equity − 총노출."""

    @property
    def available(self) -> float: ...

    def quantity_of(self, instrument: InstrumentId) -> Decimal: ...

    def consume(self, fill: FillEvent) -> None: ...

    def consume_quantity(
        self, instrument: InstrumentId, side: Side, quantity: Decimal, price: float, fee: float
    ) -> None: ...

    def checkpoint(self) -> object: ...

    def restore(self, state: object) -> None: ...


class PythonBuyingPower:
    """MARGIN 없음(leverage 1.0, 롱 전용)이면 정확히 현금과 같다. 체결마다 수량 변화로
    총노출을, 수수료·재평가로 equity를 갱신한다. 가격은 세션 시작 평가가 아니라 체결가를 쓴다."""

    def __init__(self, snapshot: PortfolioSnapshot, leverage: float) -> None:
        self._leverage = leverage
        self._equity = snapshot.equity
        self._gross = sum(abs(p.market_value) for p in snapshot.positions)
        self._quantities = {p.instrument: p.quantity for p in snapshot.positions}
        self._marks = {p.instrument: p.market_price for p in snapshot.positions}

    @property
    def available(self) -> float:
        return self._leverage * self._equity - self._gross

    def quantity_of(self, instrument: InstrumentId) -> Decimal:
        return self._quantities.get(instrument, Decimal(0))

    def consume(self, fill: FillEvent) -> None:
        self.consume_quantity(fill.instrument, fill.side, fill.quantity, fill.price, fill.fee)

    def consume_quantity(
        self, instrument: InstrumentId, side: Side, quantity: Decimal, price: float, fee: float
    ) -> None:
        old = self._quantities.get(instrument, Decimal(0))
        signed = quantity if side is Side.BUY else -quantity
        new = old + signed
        # 기존 노출은 세션 시작 평가가, 변화분은 체결가가 기준이다. 체결가로 재평가되면
        # 기존 보유분의 평가 손익만큼 equity도 움직인다 (equity = cash + Σ qty × mark).
        old_mark = self._marks.get(instrument, price)
        self._gross += float(abs(new)) * price - float(abs(old)) * old_mark
        self._equity += float(old) * (price - old_mark) - fee
        self._marks[instrument] = price
        self._quantities[instrument] = new

    def checkpoint(self) -> object:
        return self._equity, self._gross, dict(self._quantities), dict(self._marks)

    def restore(self, state: object) -> None:
        equity, gross, quantities, marks = cast(PowerState, state)
        self._equity, self._gross = equity, gross
        self._quantities = dict(quantities)
        self._marks = dict(marks)


class RustBuyingPower:
    def __init__(self, snapshot: PortfolioSnapshot, leverage: float) -> None:
        core = importlib.import_module("backtest_core")
        self._instruments = {instrument_key(p.instrument): p.instrument for p in snapshot.positions}
        self._inner = core.BuyingPower(
            snapshot.equity,
            leverage,
            [
                (instrument_key(p.instrument), int(p.quantity), p.market_price, p.market_value)
                for p in snapshot.positions
            ],
        )

    @property
    def available(self) -> float:
        return self._inner.available

    def quantity_of(self, instrument: InstrumentId) -> Decimal:
        return Decimal(self._inner.quantity_of(instrument_key(instrument)))

    def consume(self, fill: FillEvent) -> None:
        self.consume_quantity(fill.instrument, fill.side, fill.quantity, fill.price, fill.fee)

    def consume_quantity(
        self, instrument: InstrumentId, side: Side, quantity: Decimal, price: float, fee: float
    ) -> None:
        self._instruments.setdefault(instrument_key(instrument), instrument)
        self._inner.consume(instrument_key(instrument), side.value, int(quantity), price, fee)

    def checkpoint(self) -> object:
        return self._inner.checkpoint()

    def restore(self, state: object) -> None:
        self._inner.restore(state)

    @property
    def inner(self) -> object:
        """Rust `process_market`에 그대로 넘기는 내부 누산기."""
        return self._inner


class RustQuoteCore:
    def __init__(self) -> None:
        self._core = importlib.import_module("backtest_core")

    def quote_numbers(
        self,
        side: Side,
        base_price: float,
        remaining: Decimal,
        volume: int,
        participation: float | None,
        slip: float,
        limit_price: Decimal | None,
        buying_power: float,
        held: Decimal,
        fee_rate: float,
        fok: bool,
    ) -> QuoteNumbers:
        price, quantity, applied, status = self._core.quote_numbers(
            side.value,
            base_price,
            int(remaining),
            volume,
            None if participation is None else str(participation),
            slip,
            None if limit_price is None else float(limit_price),
            buying_power,
            int(held),
            fee_rate,
            fok,
        )
        return QuoteNumbers(price, Decimal(quantity), applied, ExecutionStatus(status))


def slippage_config(model: SlippageModel) -> tuple[str, float, float]:
    """Rust 코어가 아는 내장 슬리피지 모델만 설정 튜플로 바꾼다. 커스텀 모델은 Python 코어 전용."""
    if isinstance(model, NoSlippage):
        return ("none", 0.0, 0.0)
    if isinstance(model, FixedBpsSlippage):
        return ("fixed_bps", model.bps, 0.0)
    if isinstance(model, VolumeShareSlippage):
        return ("volume_share", model.volume_limit, model.price_impact)
    raise CoreUnavailable(
        f"rust core supports built-in slippage models only — got {type(model).__name__}; "
        f'use core="python" for custom SlippageModel implementations'
    )


def make_quote_core(core: str) -> QuoteCore:
    _require_core(core)
    return RustQuoteCore() if core in RUST_CORES else PythonQuoteCore()


def make_buying_power(
    core: str, snapshot: PortfolioSnapshot, leverage: float
) -> BuyingPowerTracker:
    _require_core(core)
    if core in RUST_CORES:
        return RustBuyingPower(snapshot, leverage)
    return PythonBuyingPower(snapshot, leverage)


def make_pricing(core: str) -> ExecutionPricing:
    _require_core(core)
    return RustPricing() if core in RUST_CORES else PythonPricing()


def make_portfolio(
    core: str, initial_cash: float, *, allow_short: bool = False, allow_margin: bool = False
) -> PortfolioLedger:
    _require_core(core)
    if core == "rust_legacy":
        return RustPortfolio(initial_cash, allow_short=allow_short, allow_margin=allow_margin)
    if core in PERSISTENT_RUST_CORES:
        runtime = make_persistent_runtime(
            initial_cash,
            allow_short=allow_short,
            allow_margin=allow_margin,
            leverage=1.0,
        )
        return PersistentPortfolio(runtime)
    return PythonPortfolio(initial_cash, allow_short=allow_short, allow_margin=allow_margin)
