"""코어 선택 (6a): 체결 가격 규칙과 포트폴리오 회계를 Python 또는 Rust 구현으로 제공한다.

Python 구현이 진실 원천이다. Rust 확장(`backtest_core`)은 선택 사항이며, 요청했는데 없으면
`CoreUnavailable`로 실패한다 — 조용히 Python으로 떨어지면 성능 비교가 거짓이 된다.
경계는 원시 타입뿐이다: 종목은 `"venue:symbol"` 키, 수량은 정수, 가격·현금은 float.
"""

from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol, cast

from backtest_engine.engine.broker import (
    ExecutionPricing,
    ExecutionStatus,
    PythonQuoteCore,
    QuoteCore,
    QuoteNumbers,
)
from backtest_engine.engine.portfolio import Portfolio as PythonPortfolio
from backtest_engine.engine.portfolio import scale_quantity
from backtest_engine.engine.pricing import PriceDecision, execution_price
from backtest_engine.engine.slippage import FixedBpsSlippage, NoSlippage, VolumeShareSlippage
from backtest_engine.errors import CoreUnavailable, NegativeCashError, NegativePositionError
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    FillEvent,
    OrderEvent,
)
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot, Position

CORES = ("python", "rust")


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
    if core == "rust":
        return importlib.util.find_spec("backtest_core") is not None
    return False


def _require_core(core: str) -> None:
    if core not in CORES:
        raise CoreUnavailable(f"unknown core — core={core!r} available={CORES}")
    if not core_available(core):
        raise CoreUnavailable(
            f"core extension not installed — core={core!r}; build with "
            f"`uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release`"
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


def instrument_key(instrument: InstrumentId) -> str:
    """InstrumentId의 모든 필드를 담는다 — 통화·자산군만 다른 종목이 합쳐지면 안 된다."""
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

    def charge(self, cost: CostAccrued) -> None:
        self._inner.charge(cost.amount)

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

    @property
    def cash(self) -> float:
        return self._inner.cash

    def held_qty(self, instrument: InstrumentId) -> Decimal:
        return Decimal(self._inner.held_qty(instrument_key(instrument)))

    def snapshot(self, ts: datetime) -> PortfolioSnapshot:
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
    return RustQuoteCore() if core == "rust" else PythonQuoteCore()


def make_buying_power(
    core: str, snapshot: PortfolioSnapshot, leverage: float
) -> BuyingPowerTracker:
    _require_core(core)
    if core == "rust":
        return RustBuyingPower(snapshot, leverage)
    return PythonBuyingPower(snapshot, leverage)


def make_pricing(core: str) -> ExecutionPricing:
    _require_core(core)
    return RustPricing() if core == "rust" else PythonPricing()


def make_portfolio(
    core: str, initial_cash: float, *, allow_short: bool = False, allow_margin: bool = False
) -> PortfolioLedger:
    _require_core(core)
    if core == "rust":
        return RustPortfolio(initial_cash, allow_short=allow_short, allow_margin=allow_margin)
    return PythonPortfolio(initial_cash, allow_short=allow_short, allow_margin=allow_margin)
