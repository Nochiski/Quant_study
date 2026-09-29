"""실험 설계 — 해소된 탐색 그리드와 창 목록에서 trial 을 편다(spec D5).

trial = (실험, 해소된 파라미터, 창)이다. 설계는 실험을 만들 때 한 번 정해 저장하고, trial 은 저장한
설계에서 다시 편다. 전개 순서가 trial 의 `index` 이므로 같은 설계는 늘 같은 trial 목록을 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product

from strategy_workbench.domain.backtest.facade.runs import BacktestRunSpec
from strategy_workbench.domain.backtest.facade.trials import trial_key
from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from ._search import GridIndex, SearchSpec
from ._walk_forward import WalkForwardWindow


@dataclass(frozen=True)
class ExperimentTrial:
    index: int
    grid_index: GridIndex
    # 문서가 선언한 파라미터 전부의 해소 값.
    parameter_values: dict[str, ParameterValue]
    window: WalkForwardWindow


@dataclass(frozen=True)
class ExperimentDesign:
    search: SearchSpec
    # 기반 실행 요청을 해소한 값(문서 파라미터 전부). 탐색 축의 칸 값이 그 위를 덮는다.
    parameter_values: dict[str, ParameterValue]
    windows: tuple[WalkForwardWindow, ...]
    # 창의 학습 끝이 엠바고를 뺀 측정 끝이다(`SplitSpec.measured_windows`, V3-05). 그 전에 만든
    # 실험은 거짓으로 읽히고 워크포워드 검증을 돌리지 않는다 — 이전 의미 그대로 둔다.
    measured: bool = False

    def trials(self) -> tuple[ExperimentTrial, ...]:
        """칸(행 우선) × 창 순서로 편다. 한 칸의 창들이 붙어 있다."""
        return tuple(
            ExperimentTrial(
                index,
                grid_index,
                {**self.parameter_values, **self.search.resolve(grid_index)},
                window,
            )
            for index, (grid_index, window) in enumerate(
                product(self.search.indices(), self.windows)
            )
        )


def experiment_trial_key(base: BacktestRunSpec, trial: ExperimentTrial) -> str:
    """실험 trial 의 시도 키 — 창 날짜가 아니라 실험 기반 실행 설정으로 낸다(spec D2).

    창의 학습 구간은 분할 설계가 정한 평가 구간이지 연구자가 고른 선택지가 아니다. 그래서 한 칸의
    창들은 분할 방식과 상관없이 한 시도이고, 실험 하나가 계열 N 에 더하는 수는 새 칸 수다. 분할
    설계(`SplitSpec`)를 바꿔 다시 돌려도 키가 같다(알려진 한계).

    Args:
        base: 실행 서비스가 해소한 기반 실행 spec(전략·실행 설정·파라미터 값).
    """
    return trial_key(replace(base, parameter_values=trial.parameter_values))
