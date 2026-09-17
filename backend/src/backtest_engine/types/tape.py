"""선언형 목표 tape 계약.

전략이 "세션 날짜 → 목표 비중" 표로 완전히 기술될 때, 엔진은 `on_event()`를 호출하는 대신
표를 실행 코어에 한 번 넘기고 콜백 없이 완주할 수 있다. 결정 규칙의 단일 정본은
`backtest_engine.engine.tape.evaluate_tape`이며 Rust `tape.rs`가 같은 규칙을 구현한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from backtest_engine.types.actions import SetPortfolioTarget, WeightTarget
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.requirements import StrategyRequirements
from backtest_engine.types.strategy import StrategyContext


@dataclass(frozen=True)
class TapeFrame:
    """한 세션에 적용할 목표. `action.targets`는 `WeightTarget`만 허용한다."""

    action: SetPortfolioTarget
    reason: str

    def __post_init__(self) -> None:
        for target in self.action.targets:
            if not isinstance(target, WeightTarget):
                raise TypeError(
                    "tape frame targets must be WeightTarget — "
                    f"got {type(target).__name__} for instrument={target.instrument.symbol} "
                    f"reason={self.reason!r}"
                )


@runtime_checkable
class DeclarativeTapeStrategy(Protocol):
    """`Strategy`에 더해 결정 표를 노출하는 전략.

    `on_event()`는 Python reference 경로(`core="python"`)와 패리티 테스트가 쓰고, persistent
    Rust 경로는 `tape_frames()`를 적재해 콜백 없이 실행한다. 두 경로의 trace는 같아야 한다.
    """

    @property
    def idle_reason(self) -> str:
        """프레임이 없는 세션과 market이 아닌 콜백에 남길 NoAction 사유."""
        ...

    def requirements(self) -> StrategyRequirements: ...

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision: ...

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        """세션 날짜별 프레임. 세션에 해당하지 않는 날짜는 무시된다."""
        ...
