"""실험 기록 저장소 포트 (검증 랩 spec D3·D5·D9).

실험 설계·attempt·후보 선택은 이 저장소가 정본이다. trial 은 설계에서 다시 펴므로 따로 적지 않고,
실행에 배정된 attempt 의 상태도 적지 않는다 — 그 실행의 상태에서 파생한다. 행은 지우지 않는다(실패한
trial 도 원장에 남는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import BacktestRunSpec
from strategy_workbench.domain.experiment.facade.design import ExperimentDesign, SplitSpec
from strategy_workbench.domain.experiment.facade.trial import ExperimentControls
from strategy_workbench.domain.strategy.facade.specification import ParameterValue


@dataclass(frozen=True)
class ExperimentRecord:
    """만든 실험. 취소 시각과 대기열 조작 말고는 바뀌지 않는다."""

    experiment_id: str
    created_at: datetime
    # trial 마다 기간·파라미터 값만 바꿔 실행할 기반 요청. 전략은 저장 리비전 참조로만 싣는다.
    run: BacktestRunSpec
    split: SplitSpec
    design: ExperimentDesign
    cancelled_at: datetime | None = None
    controls: ExperimentControls = ExperimentControls()


@dataclass(frozen=True)
class TrialAttempt:
    """trial 의 실행 시도 하나. 배정되면 `run_id`, 접수가 거절되면 거절 코드와 문장이 있다."""

    experiment_id: str
    trial_index: int
    # 1부터. 재시도는 다음 번호의 새 attempt 다.
    attempt: int
    created_at: datetime
    run_id: str | None = None
    # 실행 접수 거절 코드(화면 번역 키 `backtest.error.<code>`)와 실행 서비스의 거절 문장.
    error_code: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if (self.run_id is None) == (self.error_code is None) or (self.error_code is None) != (
            self.error is None
        ):
            raise ValueError(
                "attempt carries a run_id or a rejection (code and message), not both — "
                f"experiment_id={self.experiment_id} trial_index={self.trial_index} "
                f"attempt={self.attempt} run_id={self.run_id!r} error_code={self.error_code!r}"
            )


@dataclass(frozen=True)
class WindowPick:
    """워크포워드 창마다 학습 점수로 자동으로 고른 칸과 그 칸의 검증 실행(V3-05).

    사용자가 이유를 적어 고르는 후보 선택(`ExperimentSelection`, spec D9)과 다르다. 고를 칸이
    없으면(창에서 대표 샤프가 있는 학습 실행이 없다) `trial_index` 가 None 이고 실행도 없다.
    검증 실행이 재시작으로 중단되면 같은 칸으로 다음 번호를 다시 넘긴다.
    """

    experiment_id: str
    window_index: int
    attempt: int
    # 고른 칸의 그 창 학습 trial 과 그 대표 샤프(세션 단위, spec D2).
    trial_index: int | None
    train_sharpe: float | None
    created_at: datetime
    run_id: str | None = None
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExperimentSelection:
    """사용자가 고른 후보 기록(spec D9). 되돌릴 수 없다."""

    experiment_id: str
    trial_index: int
    strategy_id: str
    revision: int
    parameter_values: dict[str, ParameterValue]
    reason: str
    selected_at: datetime


class ExperimentRepositoryPort(Protocol):
    def add(self, record: ExperimentRecord) -> None: ...

    def get(self, experiment_id: str) -> ExperimentRecord:
        """없으면 `ExperimentNotFoundError`(`experiment.not_found`)."""
        ...

    def list(self, *, after: str | None, limit: int) -> tuple[ExperimentRecord, ...]:
        """최근에 만든 순으로 `after` 실험 다음부터 `limit` 개. 모르는 `after` 면 빈 목록이다."""
        ...

    def cancel(self, experiment_id: str, *, cancelled_at: datetime) -> None: ...

    def set_controls(self, experiment_id: str, controls: ExperimentControls) -> None: ...

    def add_attempt(self, attempt: TrialAttempt) -> None: ...

    def attempts(self, experiment_id: str) -> tuple[TrialAttempt, ...]:
        """trial 순, 같은 trial 안에서는 attempt 순."""
        ...

    def add_pick(self, pick: WindowPick) -> None: ...

    def picks(self, experiment_id: str) -> tuple[WindowPick, ...]:
        """창 순, 같은 창 안에서는 attempt 순."""
        ...

    def add_selection(self, selection: ExperimentSelection) -> None: ...

    def selections(self, experiment_id: str) -> tuple[ExperimentSelection, ...]:
        """기록한 순."""
        ...
