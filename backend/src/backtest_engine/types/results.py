"""실행 설정과 최종 결과 타입."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class RunConfig:
    """한 번의 실행을 재현하는 데 필요한 엔진 설정.

    fee_bps: 체결 금액 대비 수수료 (basis point, 매수·매도 동일 적용).
    annualization_days: 연 단위 비용(숏 차입·신용 이자)을 세션으로 나누는 연간 세션 수 (XKRX 252).
    short_borrow_bps_annual: 숏 평가액 대비 연 차입 비용 (bp). 세션마다 /annualization_days.
    margin_interest_bps_annual: 음수 현금 대비 연 이자 (bp). MARGIN 선언 전략에만 의미 있다.
    max_gross_leverage: 총노출/equity 상한. 1.0이면 현금 범위 매수(MARGIN 없음).
    """

    run_id: str
    initial_cash: float
    fee_bps: float = 0.0
    annualization_days: int = 252
    short_borrow_bps_annual: float = 0.0
    margin_interest_bps_annual: float = 0.0
    max_gross_leverage: float = 1.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError(
                f"initial_cash must be > 0 — run_id={self.run_id} initial_cash={self.initial_cash}"
            )
        if self.fee_bps < 0:
            raise ValueError(f"fee_bps must be >= 0 — run_id={self.run_id} fee_bps={self.fee_bps}")
        for label, value in (
            ("short_borrow_bps_annual", self.short_borrow_bps_annual),
            ("margin_interest_bps_annual", self.margin_interest_bps_annual),
        ):
            if value < 0:
                raise ValueError(f"{label} must be >= 0 — run_id={self.run_id} {label}={value}")
        if self.max_gross_leverage < 1.0:
            raise ValueError(
                f"max_gross_leverage must be >= 1.0 — run_id={self.run_id} "
                f"max_gross_leverage={self.max_gross_leverage}"
            )
        if self.annualization_days <= 0:
            raise ValueError(
                f"annualization_days must be > 0 — run_id={self.run_id} "
                f"annualization_days={self.annualization_days}"
            )


@dataclass(frozen=True, eq=False)
class BacktestResult:
    """한 번의 실행을 재현하고 분석하는 데 필요한 최종 출력 묶음.

    엔진 내부 출력은 tuple과 typed model로 고정하고 JSON 경계에서만 dict로 바꾼다.
    """

    run_id: str
    snapshots: tuple[PortfolioSnapshot, ...]
    orders: tuple[OrderEvent, ...]
    fills: tuple[FillEvent, ...]

    @classmethod
    def lazy(
        cls,
        *,
        run_id: str,
        snapshots: Callable[[], tuple[PortfolioSnapshot, ...]],
        orders: Callable[[], tuple[OrderEvent, ...]],
        fills: Callable[[], tuple[FillEvent, ...]],
    ) -> BacktestResult:
        """snapshots/orders/fills tuple을 최초 접근 시에만 만드는 결과."""
        return _LazyBacktestResult(run_id, snapshots, orders, fills)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BacktestResult):
            return NotImplemented
        return (
            self.run_id == other.run_id
            and self.snapshots == other.snapshots
            and self.orders == other.orders
            and self.fills == other.fills
        )

    def __hash__(self) -> int:
        return hash((self.run_id, self.snapshots, self.orders, self.fills))


class _LazyBacktestResult(BacktestResult):
    _snapshot_loader: Callable[[], tuple[PortfolioSnapshot, ...]]
    _order_loader: Callable[[], tuple[OrderEvent, ...]]
    _fill_loader: Callable[[], tuple[FillEvent, ...]]
    _snapshots_cache: tuple[PortfolioSnapshot, ...] | None
    _orders_cache: tuple[OrderEvent, ...] | None
    _fills_cache: tuple[FillEvent, ...] | None

    def __init__(
        self,
        run_id: str,
        snapshot_loader: Callable[[], tuple[PortfolioSnapshot, ...]],
        order_loader: Callable[[], tuple[OrderEvent, ...]],
        fill_loader: Callable[[], tuple[FillEvent, ...]],
    ) -> None:
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "_snapshot_loader", snapshot_loader)
        object.__setattr__(self, "_order_loader", order_loader)
        object.__setattr__(self, "_fill_loader", fill_loader)
        object.__setattr__(self, "_snapshots_cache", None)
        object.__setattr__(self, "_orders_cache", None)
        object.__setattr__(self, "_fills_cache", None)

    @property
    def snapshots(self) -> tuple[PortfolioSnapshot, ...]:
        cached = self._snapshots_cache
        if cached is None:
            cached = self._snapshot_loader()
            object.__setattr__(self, "_snapshots_cache", cached)
        return cached

    @property
    def orders(self) -> tuple[OrderEvent, ...]:
        cached = self._orders_cache
        if cached is None:
            cached = self._order_loader()
            object.__setattr__(self, "_orders_cache", cached)
        return cached

    @property
    def fills(self) -> tuple[FillEvent, ...]:
        cached = self._fills_cache
        if cached is None:
            cached = self._fill_loader()
            object.__setattr__(self, "_fills_cache", cached)
        return cached
