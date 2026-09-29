"""실험 설계 — 해소된 탐색 그리드와 창 목록에서 trial 을 편다(spec D5).

trial = (실험, 해소된 파라미터, 창)이다. 설계는 실험을 만들 때 한 번 정해 저장하고, trial 은 저장한
설계에서 다시 편다. 전개 순서가 trial 의 `index` 이므로 같은 설계는 늘 같은 trial 목록을 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

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
