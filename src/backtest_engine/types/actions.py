"""StrategyAction 합 타입과 실행 정책.

목표 비중 변경, 긴급 청산, 주문 제출·취소·정정처럼 뜻이 다른 행동을
각기 다른 타입으로 표현한다. 어떤 필드가 필요한지 타입만 보고 알 수 있고
잘못된 조합은 생성 시점에 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from backtest_engine.types.instruments import InstrumentId, Money
from backtest_engine.types.orders import OrderRequest, TimeInForce


class ActionKind(Enum):
    NO_ACTION = "no_action"
    SET_PORTFOLIO_TARGET = "set_portfolio_target"
    SET_POSITION_TARGET = "set_position_target"
    ADJUST_POSITION = "adjust_position"
    LIQUIDATE_POSITION = "liquidate_position"
    SUBMIT_ORDER = "submit_order"
    CANCEL_ORDER = "cancel_order"
    REPLACE_ORDER = "replace_order"
    BASKET = "basket"


# --- 실행 정책 ---------------------------------------------------------------


class ExecutionTiming(Enum):
    """판단 시점과 체결 시점을 분리해 look-ahead를 막는다."""

    NEXT_OPEN = "next_open"
    NEXT_AVAILABLE = "next_available"


class ExecutionStyle(Enum):
    MARKET = "market"
    VWAP = "vwap"
    TWAP = "twap"


@dataclass(frozen=True)
class ExecutionPolicy:
    style: ExecutionStyle
    timing: ExecutionTiming
    time_in_force: TimeInForce
    max_participation: float | None = None

    @classmethod
    def market_next_open(cls) -> ExecutionPolicy:
        """일봉 종가 신호의 기본 실행 정책."""
        return cls(ExecutionStyle.MARKET, ExecutionTiming.NEXT_OPEN, TimeInForce.DAY)

    @classmethod
    def market_next_available(cls) -> ExecutionPolicy:
        """신호 이후 처음 가능한 가격에 시장가로 실행."""
        return cls(ExecutionStyle.MARKET, ExecutionTiming.NEXT_AVAILABLE, TimeInForce.DAY)


# --- 목표값 ------------------------------------------------------------------


@dataclass(frozen=True)
class WeightTarget:
    instrument: InstrumentId
    weight: float


@dataclass(frozen=True)
class QuantityTarget:
    instrument: InstrumentId
    quantity: Decimal


@dataclass(frozen=True)
class NotionalTarget:
    instrument: InstrumentId
    notional: Money


PositionTarget = WeightTarget | QuantityTarget | NotionalTarget


@dataclass(frozen=True)
class QuantityDelta:
    quantity: Decimal


@dataclass(frozen=True)
class NotionalDelta:
    notional: Money


# --- Action 타입 -------------------------------------------------------------


@dataclass(frozen=True)
class NoAction:
    """None이나 빈 dict 대신 의도적인 무행동을 타입으로 남긴다."""

    reason: str | None = None


class TargetScope(Enum):
    REPLACE = "replace"  # 목록에 없는 기존 포지션은 0으로 본다.
    PATCH = "patch"  # 목록에 들어온 포지션만 변경한다.


@dataclass(frozen=True)
class SetPortfolioTarget:
    targets: tuple[PositionTarget, ...]
    scope: TargetScope
    execution: ExecutionPolicy


@dataclass(frozen=True)
class SetPositionTarget:
    target: PositionTarget
    execution: ExecutionPolicy


@dataclass(frozen=True)
class AdjustPosition:
    """절대 목표가 아니라 현재 상태 대비 증감량."""

    instrument: InstrumentId
    delta: QuantityDelta | NotionalDelta
    execution: ExecutionPolicy


class LiquidationPersistence(Enum):
    ONCE = "once"
    UNTIL_FLAT = "until_flat"


@dataclass(frozen=True)
class LiquidatePosition:
    """목표 0%와 다른, 긴급도가 명시된 청산 의미."""

    instrument: InstrumentId
    execution: ExecutionPolicy
    cancel_open_orders: bool
    persistence: LiquidationPersistence


@dataclass(frozen=True)
class SubmitOrder:
    request: OrderRequest


@dataclass(frozen=True)
class CancelOrder:
    order_id: str


@dataclass(frozen=True)
class ReplaceOrder:
    """주문 정정은 기존 Order를 몰래 수정하지 않고 Cancel/Replace로 추적한다."""

    order_id: str
    replacement: OrderRequest


class GroupPolicy(Enum):
    BEST_EFFORT = "best_effort"
    PROPORTIONAL = "proportional"
    ALL_OR_NONE = "all_or_none"


BasketLeg = SetPositionTarget | AdjustPosition | LiquidatePosition | SubmitOrder


@dataclass(frozen=True)
class BasketAction:
    """페어 전략처럼 한쪽만 체결될 위험이 있을 때 여러 leg를 한 실행 그룹으로 묶는다."""

    legs: tuple[BasketLeg, ...]
    group_policy: GroupPolicy


StrategyAction = (
    NoAction
    | SetPortfolioTarget
    | SetPositionTarget
    | AdjustPosition
    | LiquidatePosition
    | SubmitOrder
    | CancelOrder
    | ReplaceOrder
    | BasketAction
)

# Action 타입 → ActionKind 매핑의 단일 진실 원천.
_ACTION_KINDS: dict[type, ActionKind] = {
    NoAction: ActionKind.NO_ACTION,
    SetPortfolioTarget: ActionKind.SET_PORTFOLIO_TARGET,
    SetPositionTarget: ActionKind.SET_POSITION_TARGET,
    AdjustPosition: ActionKind.ADJUST_POSITION,
    LiquidatePosition: ActionKind.LIQUIDATE_POSITION,
    SubmitOrder: ActionKind.SUBMIT_ORDER,
    CancelOrder: ActionKind.CANCEL_ORDER,
    ReplaceOrder: ActionKind.REPLACE_ORDER,
    BasketAction: ActionKind.BASKET,
}


def kind_of(action: StrategyAction) -> ActionKind:
    """Action 인스턴스가 어떤 ActionKind인지 반환한다."""
    try:
        return _ACTION_KINDS[type(action)]
    except KeyError:
        raise TypeError(
            f"unknown action type — got {type(action).__name__}, "
            f"expected one of {[t.__name__ for t in _ACTION_KINDS]}"
        ) from None
