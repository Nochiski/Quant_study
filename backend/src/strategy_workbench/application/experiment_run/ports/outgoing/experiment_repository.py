"""실험 기록 저장소 포트 (검증 랩 spec D3·D5·D9).

실험 설계·attempt·후보 선택은 이 저장소가 정본이다. trial 은 설계에서 다시 펴므로 따로 적지 않고,
실행에 배정된 attempt 의 상태도 적지 않는다 — 그 실행의 상태에서 파생한다. 행은 지우지 않는다(실패한
trial 도 원장에 남는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import AdmissionRejectionCode, BacktestRunSpec
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
    # 파라미터 탐색의 분할 규칙. 용량 스윕은 창이 없어 None 이다.
    split: SplitSpec | None
    design: ExperimentDesign
    controls: ExperimentControls
    cancelled_at: datetime | None = None
    # "튼튼한지 확인하기"로 만든 실험이면 그 버튼이 있던 결과의 실행(US-DM-11). 결과 화면이 이
    # 실행의 실험을 찾는다. 직접 만든 실험은 None 이다.
    source_run_id: str | None = None


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
    error_code: AdmissionRejectionCode | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _validate_submission(
            self.run_id,
            self.error_code,
            self.error,
            context=f"experiment_id={self.experiment_id} trial_index={self.trial_index} "
            f"attempt={self.attempt}",
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
    error_code: AdmissionRejectionCode | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        context = f"experiment_id={self.experiment_id} window_index={self.window_index}"
        if (self.trial_index is None) != (self.train_sharpe is None):
            raise ValueError(f"window pick carries a trial and training score together — {context}")
        if self.trial_index is None:
            if any(value is not None for value in (self.run_id, self.error_code, self.error)):
                raise ValueError(f"a window without a chosen trial has no submission — {context}")
        else:
            _validate_submission(self.run_id, self.error_code, self.error, context=context)


def _validate_submission(
    run_id: str | None,
    error_code: AdmissionRejectionCode | None,
    error: str | None,
    *,
    context: str,
) -> None:
    """학습·검증 제출은 실행 ID 또는 거절 코드와 문장 가운데 하나를 기록한다."""
    if (run_id is None) == (error_code is None) or (error_code is None) != (error is None):
        raise ValueError(
            "submission carries a run_id or a rejection (code and message), not both — "
            f"{context} run_id={run_id!r} error_code={error_code!r}"
        )


@dataclass(frozen=True)
class ControlChange:
    """실험 조작을 바꾼 기록 한 줄(로드맵 M6 감사 기록). 바꾼 뒤의 값을 싣는다."""

    changed_at: datetime
    controls: ExperimentControls


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
    # 고를 때의 계열 스냅숏(V4-02) — 나중에 N 이 늘어도 고를 때 본 값이 남는다. V4-02 이전 기록은
    # None. 설명은 OpenAPI description 으로 실린다(화면이 뜻을 추정하지 않게).
    trial_count: int | None = field(
        default=None, metadata={"description": "고를 때의 계열 시도 수 N(계열 원장)."}
    )
    ledger_representative_sharpe: float | None = field(
        default=None,
        metadata={
            "description": "고른 trial 이 속한 시도의 계열 원장 대표 샤프. 세션 단위(연율화 "
            "전)이고 그 시도에서 처음 결과가 난 실행의 값이라 고른 trial 실행의 샤프(DSR 분자)가 "
            "아닐 수 있다."
        },
    )
    deflated_sharpe: float | None = field(
        default=None,
        metadata={
            "description": "고른 trial 실행의 샤프를 고를 때의 계열 N·시도 대표 샤프 분산으로 깎은 "
            "DSR(0~1). 지표가 비었거나 분산을 낼 수 없으면 비어 있다."
        },
    )


class ExperimentRepositoryPort(Protocol):
    def add(self, record: ExperimentRecord) -> None: ...

    def get(self, experiment_id: str) -> ExperimentRecord:
        """없으면 `ExperimentNotFoundError`(`experiment.not_found`)."""
        ...

    def controls(self, experiment_id: str) -> ExperimentControls:
        """저장된 레인 조작만 읽는다(설계 디코드 없음). 없으면 `ExperimentNotFoundError`."""
        ...

    def open_ids(self) -> tuple[str, ...]:
        """취소하지 않은 실험 id(만든 순). 기록을 디코드하지 않는다(재시작 복구가 하나씩 읽는다)."""
        ...

    def list(self, *, after: str | None, limit: int) -> tuple[ExperimentRecord, ...]:
        """최근에 만든 순으로 `after` 다음의 읽을 수 있는 기록을 `limit` 개까지 돌려준다.

        디코드 실패 행은 adapter가 식별자를 로그에 남기고 건너뛴다. 원장은 변경하지 않는다.
        모르는 `after` 면 빈 목록이고, 저장소 전체 읽기 실패는 전파한다.
        """
        ...

    def by_source_run(self, run_id: str) -> tuple[ExperimentRecord, ...]:
        """`source_run_id` 가 `run_id` 인 기록, 최근에 만든 순.

        읽지 못하는 행은 `list` 처럼 건너뛴다.
        """
        ...

    def cancel(self, experiment_id: str, *, cancelled_at: datetime) -> None: ...

    def set_controls(
        self, experiment_id: str, controls: ExperimentControls, *, changed_at: datetime
    ) -> None:
        """현재 조작을 바꾸고 같은 트랜잭션에서 이력(`control_changes`)에 한 줄을 더한다."""
        ...

    def control_changes(self, experiment_id: str) -> tuple[ControlChange, ...]:
        """조작을 바꾼 순."""
        ...

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
