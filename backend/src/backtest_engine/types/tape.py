"""선언형 목표 tape 계약.

전략이 "세션 날짜 → 목표 비중" 표로 완전히 기술될 때, 엔진은 `on_event()`를 호출하는 대신
표를 실행 코어에 한 번 넘기고 콜백 없이 완주할 수 있다. 결정 규칙의 단일 정본은
`backtest_engine.engine.tape.evaluate_tape`이며 Rust `tape.rs`가 같은 규칙을 구현한다.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

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


class DeclarativeTapeStrategy(abc.ABC):
    """`Strategy`에 더해 결정 표를 노출하는 전략.

    `on_event()`는 Python reference 경로(`core="python"`)와 패리티 테스트가 쓰고, persistent
    Rust 경로는 `tape_frames()`를 적재해 콜백 없이 실행한다. 두 경로의 trace는 같아야 한다.

    구조 일치가 아니라 **명시 상속**으로만 tape 경로에 들어간다. 구조만 보면 우연히 같은 네
    이름을 가진 전략이 tape 경로로 빨려 들어가 `on_event()`가 한 번도 불리지 않는데, 예외도
    경고도 남지 않아 전략이 통째로 무시된 채 결과만 나온다.
    """

    @property
    @abc.abstractmethod
    def idle_reason(self) -> str:
        """프레임이 없는 세션과 market이 아닌 콜백에 남길 NoAction 사유."""

    @abc.abstractmethod
    def requirements(self) -> StrategyRequirements: ...

    @abc.abstractmethod
    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision: ...

    @abc.abstractmethod
    def tape_frames(self) -> Mapping[date, TapeFrame]:
        """세션 날짜별 프레임. 세션에 해당하지 않는 날짜는 무시된다."""
