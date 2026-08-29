"""코어 선택 (6a): 체결 가격 규칙과 포트폴리오 회계를 Python 또는 Rust 구현으로 제공한다.

Python 구현이 진실 원천이다. Rust 확장(`backtest_core`)은 선택 사항이며, 요청했는데 없으면
`CoreUnavailable`로 실패한다 — 조용히 Python으로 떨어지면 성능 비교가 거짓이 된다.
경계는 원시 타입뿐이다: 종목은 `"venue:symbol"` 키, 수량은 정수, 가격·현금은 float.
"""

from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from backtest_engine.engine.broker import ExecutionPricing
from backtest_engine.engine.portfolio import Portfolio as PythonPortfolio
from backtest_engine.engine.pricing import PriceDecision, execution_price
from backtest_engine.errors import CoreUnavailable, NegativeCashError, NegativePositionError
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    FillEvent,
    OrderEvent,
)
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from backtest_engine.types.portfolio import PortfolioSnapshot, Position

CORES = ("python", "rust")


class PortfolioLedger(Protocol):
    """`engine.portfolio.Portfolio`와 같은 명령/조회 인터페이스."""

    def apply(self, fill: FillEvent) -> None: ...

    def charge(self, cost: CostAccrued) -> None: ...

    def apply_corporate_action(
        self, action: CorporateActionEvent, settlement_price: float
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


def _key(instrument: InstrumentId) -> str:
    return f"{instrument.venue}:{instrument.symbol}"


class RustPortfolio:
    """`backtest_core.Portfolio` 어댑터. 도메인 타입 변환·Decimal 산술·예외 매핑을 맡는다.

    Python 원장은 dict 삽입 순서로 포지션을 나열하므로 같은 순서를 여기서 유지한다
    (청산 후 재진입하면 맨 뒤로).
    """

    def __init__(self, initial_cash: float, *, allow_short: bool, allow_margin: bool) -> None:
        core = importlib.import_module("backtest_core")
        self._inner = core.Portfolio(initial_cash, allow_short, allow_margin)
        self._instruments: dict[str, InstrumentId] = {}
        self._order: list[str] = []

    def apply(self, fill: FillEvent) -> None:
        if fill.quantity != fill.quantity.to_integral_value():
            raise ValueError(
                f"rust core supports integer share quantities only — fill_id={fill.fill_id} "
                f"quantity={fill.quantity}"
            )
        key = _key(fill.instrument)
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
        self._sync_order(key)

    def charge(self, cost: CostAccrued) -> None:
        self._inner.charge(cost.amount)

    def apply_corporate_action(
        self, action: CorporateActionEvent, settlement_price: float
    ) -> CorporateActionApplied | None:
        key = _key(action.instrument)
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
        scaled = old_quantity * action.ratio
        new_quantity = scaled.quantize(Decimal(1), rounding=ROUND_FLOOR)
        cash_paid = float(scaled - new_quantity) * settlement_price
        new_average = old_average / float(action.ratio)
        self._inner.apply_corporate_action(
            key, int(new_quantity), new_average, cash_paid, settlement_price
        )
        self._sync_order(key)
        return CorporateActionApplied(
            ts=action.ts,
            instrument=action.instrument,
            action=action,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            old_average_price=old_average,
            new_average_price=new_average,
            cash_paid=cash_paid,
        )

    def mark(self, snapshot: MarketSnapshot) -> None:
        self._inner.mark([(_key(bar.instrument), bar.close) for bar in snapshot.bars])
        for bar in snapshot.bars:
            self._instruments.setdefault(_key(bar.instrument), bar.instrument)

    @property
    def cash(self) -> float:
        return self._inner.cash

    def held_qty(self, instrument: InstrumentId) -> Decimal:
        return Decimal(self._inner.held_qty(_key(instrument)))

    def snapshot(self, ts: datetime) -> PortfolioSnapshot:
        cash, rows, equity, gross_exposure = self._inner.snapshot()
        by_key = {row[0]: row for row in rows}
        positions = tuple(
            Position(
                instrument=self._instruments[key],
                quantity=Decimal(by_key[key][1]),
                average_price=by_key[key][2],
                market_price=by_key[key][3],
                market_value=by_key[key][4],
                unrealized_pnl=by_key[key][5],
            )
            for key in self._order
        )
        return PortfolioSnapshot(
            ts=ts, cash=cash, positions=positions, equity=equity, gross_exposure=gross_exposure
        )

    def _sync_order(self, key: str) -> None:
        held = self._inner.held_qty(key)
        if held == 0:
            if key in self._order:
                self._order.remove(key)
        elif key not in self._order:
            self._order.append(key)


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
