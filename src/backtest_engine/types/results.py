"""실행 설정과 최종 결과 타입."""

from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class RunConfig:
    """한 번의 실행을 재현하는 데 필요한 엔진 설정.

    fee_bps: 체결 금액 대비 수수료 (basis point, 매수·매도 동일 적용).
    annualization_days: 연율화 계수 (XKRX 거래일 기준 252).
    """

    run_id: str
    initial_cash: float
    fee_bps: float = 0.0
    annualization_days: int = 252

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError(
                f"initial_cash must be > 0 — run_id={self.run_id} initial_cash={self.initial_cash}"
            )
        if self.fee_bps < 0:
            raise ValueError(f"fee_bps must be >= 0 — run_id={self.run_id} fee_bps={self.fee_bps}")


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
