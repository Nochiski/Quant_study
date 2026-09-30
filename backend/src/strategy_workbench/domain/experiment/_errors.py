"""실험을 거절하는 예외와 그 진단 코드(`experiment.*`)의 닫힌 집합.

집합에 없는 코드는 만들 때 거절해 호출 지점이 코드를 지어내지 못하게 한다. 예외 타입이 HTTP 상태를
가른다(설계 입력 422·없음 404·상태 충돌 409). 422 응답과 번역 키로 옮기는 일은 실험 API 가 `code`
를 읽어 한다. 문장은 사용자에게 그대로 보이므로 한글로 완성하고 재현 값을 `key=value` 로 싣는다.
"""

from __future__ import annotations

from typing import ClassVar

# 실험 설계 입력(기반 리비전·탐색 공간·분할 규칙·용량 스윕 금액)이 실험을 만들 수 없는 값이다.
EXPERIMENT_SPEC_CODES: frozenset[str] = frozenset(
    {
        "experiment.base.unsaved",
        "experiment.base.invalid",
        "experiment.search.unknown_parameter",
        "experiment.search.invalid_values",
        "experiment.search.too_many_points",
        "experiment.split.invalid",
        "experiment.split.no_window",
        "experiment.capacity.invalid_amounts",
        "experiment.capacity.base_not_run",
    }
)
EXPERIMENT_NOT_FOUND_CODES: frozenset[str] = frozenset(
    {"experiment.not_found", "experiment.trial.not_found"}
)
# 실험·trial 의 지금 상태로는 받을 수 없는 요청이다.
EXPERIMENT_STATE_CODES: frozenset[str] = frozenset(
    {
        "experiment.trial.not_retryable",
        "experiment.selection.not_completed",
        "experiment.selection.not_finished",
        "experiment.kind.mismatch",
    }
)
EXPERIMENT_CODES = EXPERIMENT_SPEC_CODES | EXPERIMENT_NOT_FOUND_CODES | EXPERIMENT_STATE_CODES


class ExperimentError(Exception):
    """코드가 있는 실험 거절. 하위 타입이 받는 코드 집합을 정한다."""

    codes: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, code: str, message: str) -> None:
        if code not in self.codes:
            raise ValueError(
                f"등록되지 않은 실험 진단 코드다 — code={code!r} type={type(self).__name__}"
            )
        super().__init__(message)
        self.code = code


class InvalidExperimentSpecError(ExperimentError, ValueError):
    """기반 리비전·탐색 공간·분할 규칙이 실험을 만들 수 없는 값이다(spec D5).

    `ValueError` 라 요청 본문 검증 중에 나면 본문 검증 오류로 흘러간다.
    """

    codes = EXPERIMENT_SPEC_CODES


class ExperimentNotFoundError(ExperimentError, LookupError):
    codes = EXPERIMENT_NOT_FOUND_CODES


class ExperimentStateError(ExperimentError, RuntimeError):
    codes = EXPERIMENT_STATE_CODES
