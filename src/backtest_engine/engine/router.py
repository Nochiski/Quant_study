"""DecisionRouter: 전략 판단을 검증하고 실행 가능한 주문으로 변환한다.

전략은 원하는 상태만 말한다. 주문 수량 계산, 주문 가능 여부,
체결 가격·수수료 반영은 엔진 책임이다.

v1 구현 범위 (Capability와 일치):
- NoAction, SetPortfolioTarget(WeightTarget), LiquidatePosition만 처리.
- ExecutionPolicy는 MARKET 스타일만, 일봉 엔진이므로 NEXT_OPEN과
  NEXT_AVAILABLE 모두 "다음 세션 시가"를 뜻한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.engine.orders import OrderManager
from backtest_engine.errors import (
    CapabilityNotImplemented,
    SchemaVersionMismatch,
    UndeclaredActionReturned,
    UnsupportedActionValue,
)
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    ExecutionStyle,
    LiquidatePosition,
    NoAction,
    SetPortfolioTarget,
    StrategyAction,
    TargetScope,
    WeightTarget,
    kind_of,
)
from backtest_engine.types.decision import SCHEMA_VERSION, StrategyDecision
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class RoutingResult:
    orders: tuple[OrderEvent, ...]
    cancelled: tuple[OrderEvent, ...]


class DecisionRouter:
    def __init__(
        self, declared_actions: frozenset[ActionKind], order_manager: OrderManager
    ) -> None:
        self._declared_actions = declared_actions
        self._order_manager = order_manager

    def route(
        self,
        decision: StrategyDecision,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> RoutingResult:
        if decision.schema_version != SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"decision schema version mismatch — expected {SCHEMA_VERSION}, "
                f"got {decision.schema_version} as_of={decision.as_of} reason={decision.reason}"
            )

        orders: list[OrderEvent] = []
        cancelled: list[OrderEvent] = []
        for action in decision.actions:
            kind = kind_of(action)
            if kind not in self._declared_actions:
                raise UndeclaredActionReturned(
                    f"action kind was not declared in requirements() — kind={kind.value} "
                    f"declared={sorted(k.value for k in self._declared_actions)} "
                    f"decision_id={decision_id} as_of={decision.as_of}"
                )
            orders.extend(self._route_action(action, decision_id, portfolio, market, cancelled))
        return RoutingResult(orders=tuple(orders), cancelled=tuple(cancelled))

    def _route_action(
        self,
        action: StrategyAction,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
        cancelled: list[OrderEvent],
    ) -> list[OrderEvent]:
        match action:
            case NoAction():
                return []
            case SetPortfolioTarget():
                self._check_execution(action.execution, decision_id)
                return self._portfolio_target_orders(action, decision_id, portfolio, market)
            case LiquidatePosition():
                self._check_execution(action.execution, decision_id)
                if action.cancel_open_orders:
                    cancelled.extend(self._order_manager.cancel_for_instrument(action.instrument))
                return self._liquidation_orders(action, decision_id, portfolio, market)
            case _:
                # prepare_strategy의 capability gate가 거절했어야 하는 경로다.
                raise CapabilityNotImplemented(
                    f"action reached router without an implemented handler — "
                    f"kind={kind_of(action).value} decision_id={decision_id}"
                )

    def _check_execution(self, execution: ExecutionPolicy, decision_id: str) -> None:
        if execution.style is not ExecutionStyle.MARKET:
            raise UnsupportedActionValue(
                f"execution style not implemented in v1 — style={execution.style.value} "
                f"decision_id={decision_id}, only MARKET is supported"
            )
        if execution.max_participation is not None:
            raise UnsupportedActionValue(
                f"max_participation requires PARTIAL_FILL feature (not implemented) — "
                f"max_participation={execution.max_participation} decision_id={decision_id}"
            )

    def _portfolio_target_orders(
        self,
        action: SetPortfolioTarget,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        target_notionals: dict[InstrumentId, float] = {}
        for target in action.targets:
            if not isinstance(target, WeightTarget):
                raise UnsupportedActionValue(
                    f"only WeightTarget is implemented in v1 — got {type(target).__name__} "
                    f"decision_id={decision_id}"
                )
            if target.weight < 0:
                raise UnsupportedActionValue(
                    f"negative target weight requires SHORT_SELLING feature (not implemented) — "
                    f"instrument={target.instrument.symbol} weight={target.weight} "
                    f"decision_id={decision_id}"
                )
            if target.instrument in target_notionals:
                raise ValueError(
                    f"duplicate instrument in portfolio target — "
                    f"instrument={target.instrument.symbol} decision_id={decision_id}"
                )
            target_notionals[target.instrument] = target.weight * portfolio.equity

        if action.scope is TargetScope.REPLACE:
            # 목록에 없는 기존 포지션은 0으로 본다.
            for position in portfolio.positions:
                target_notionals.setdefault(position.instrument, 0.0)

        orders: list[OrderEvent] = []
        for instrument, target_notional in target_notionals.items():
            reference_price = market.bar(instrument).close
            current_position = portfolio.position(instrument)
            current_notional = current_position.market_value if current_position else 0.0
            delta_notional = target_notional - current_notional
            quantity = floor_delta_shares(delta_notional, reference_price)
            if quantity <= 0:
                continue
            side = Side.BUY if delta_notional > 0 else Side.SELL
            if side is Side.SELL:
                held = portfolio.position_qty(instrument)
                quantity = min(quantity, held)
                if quantity <= 0:
                    continue
            orders.append(
                OrderEvent(
                    order_id=self._order_manager.next_order_id(),
                    decision_id=decision_id,
                    ts=market.ts,
                    instrument=instrument,
                    quantity=quantity,
                    side=side,
                    source_action=action,
                )
            )
        return orders

    def _liquidation_orders(
        self,
        action: LiquidatePosition,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        held = portfolio.position_qty(action.instrument)
        if held <= 0:
            return []
        return [
            OrderEvent(
                order_id=self._order_manager.next_order_id(),
                decision_id=decision_id,
                ts=market.ts,
                instrument=action.instrument,
                quantity=held,
                side=Side.SELL,
                source_action=action,
            )
        ]
