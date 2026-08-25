"""설계 노트의 골든크로스 예제 전략.

평상시에는 SetPortfolioTarget으로 목표 비중을 조정하고,
급락 시에는 LiquidatePosition으로 긴급 청산 의미를 반환한다.
MA와 drawdown은 Context 필드가 아니라 PriceWindow로부터 계산하는 전략 로직이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    LiquidatePosition,
    LiquidationPersistence,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceField
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext


@dataclass(frozen=True)
class GoldenCrossConfig:
    """실행 중 바뀌지 않는 전략 파라미터 (Config)."""

    instrument: InstrumentId
    short_window: int = 20
    long_window: int = 60
    target_weight: float = 0.7
    emergency_drawdown: float = -0.12
    rebalance_band: float = 0.01


class GoldenCrossStrategy:
    def __init__(self, config: GoldenCrossConfig) -> None:
        self.config = config
        # 전략이 소유할 데이터 요청을 한 번 만들어 재사용한다.
        self.prices = HistoryRequest(
            instruments=(config.instrument,),
            field=PriceField.CLOSE,
            lookback=config.long_window,
        )

    def requirements(self) -> StrategyRequirements:
        # 엔진은 이 선언으로 워밍업과 매일 호출을 준비한다.
        return StrategyRequirements(
            histories=(self.prices,),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset(
                {
                    ActionKind.NO_ACTION,
                    ActionKind.SET_PORTFOLIO_TARGET,
                    ActionKind.LIQUIDATE_POSITION,
                }
            ),
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        if not isinstance(event, MarketSnapshot):
            return StrategyDecision.no_action(ctx.now, "market_event_only")

        closes = ctx.history(self.prices).column(self.config.instrument)

        # 급락은 일반 이동평균 규칙보다 먼저 평가한다.
        drawdown = closes[-1] / closes.max() - 1.0
        held_qty = ctx.position_qty(self.config.instrument)
        if drawdown <= self.config.emergency_drawdown and held_qty > 0:
            return StrategyDecision.of(
                ctx.now,
                LiquidatePosition(
                    instrument=self.config.instrument,
                    execution=ExecutionPolicy.market_next_open(),
                    cancel_open_orders=True,
                    persistence=LiquidationPersistence.UNTIL_FLAT,
                ),
                reason="emergency_drawdown_exit",
            )

        short_ma = float(closes[-self.config.short_window :].mean())
        long_ma = float(closes.mean())
        target = self.config.target_weight if short_ma > long_ma else 0.0

        # 이미 목표 비중과 비슷하면 불필요한 주문을 만들지 않는다.
        current = ctx.current_weight(self.config.instrument)
        if abs(current - target) < self.config.rebalance_band:
            return StrategyDecision.no_action(ctx.now, "within_rebalance_band")

        return StrategyDecision.of(
            ctx.now,
            SetPortfolioTarget(
                targets=(WeightTarget(self.config.instrument, target),),
                scope=TargetScope.PATCH,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason="golden_cross",
        )
