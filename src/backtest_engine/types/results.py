"""실행 설정과 최종 결과 타입."""

from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class RunConfig:
    """한 번의 실행을 재현하는 데 필요한 엔진 설정.

    fee_bps: 체결 금액 대비 수수료 (basis point, 매수·매도 동일 적용).
    annualization_days: 연율화 계수 (XKRX 거래일 기준 252). 비용 일할에도 쓴다.
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


@dataclass(frozen=True)
class PerformanceMetrics:
    """실행 종료 후 equity curve와 fills에서 계산하는 성과 요약.

    0으로 나눌 수 없는 지표는 0으로 위장하지 않고 None으로 표현한다.
    max_drawdown은 음수 비율로 통일한다 (예: -0.12).
    연율화 지표는 RunConfig.annualization_days 기준이다.
    """

    total_return: float
    cagr: float
    volatility: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    turnover: float


@dataclass(frozen=True)
class BacktestResult:
    """한 번의 실행을 재현하고 분석하는 데 필요한 최종 출력 묶음.

    엔진 내부 출력은 tuple과 typed model로 고정하고 JSON 경계에서만 dict로 바꾼다.
    """

    run_id: str
    snapshots: tuple[PortfolioSnapshot, ...]
    orders: tuple[OrderEvent, ...]
    fills: tuple[FillEvent, ...]
    metrics: PerformanceMetrics
