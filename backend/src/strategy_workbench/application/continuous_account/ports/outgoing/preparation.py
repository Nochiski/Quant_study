"""실험 출처와 기존 평가 파이프라인을 읽는 자체 포트."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.scheduled_construction import ScheduledCandidate

from ..._models import FrozenExperimentInput, FrozenWindowChoice


@dataclass(frozen=True)
class PreparedContinuousSignals:
    candidate: ScheduledCandidate
    warnings: tuple[str, ...] = ()


class ContinuousExperimentSourcePort(Protocol):
    def inspect(self, experiment_id: str) -> FrozenExperimentInput:
        """저장 메타데이터만 검사하며 큰 실행 산출물을 읽지 않는다."""
        ...

    def freeze(self, experiment_id: str) -> FrozenExperimentInput: ...


class ContinuousPipelinePort(Protocol):
    def preflight(self, source: FrozenExperimentInput, environment: RunEnvironment) -> None: ...

    def prepare(
        self,
        source: FrozenExperimentInput,
        choice: FrozenWindowChoice,
        environment: RunEnvironment,
        *,
        progress: Callable[[float, str], None],
    ) -> PreparedContinuousSignals: ...
