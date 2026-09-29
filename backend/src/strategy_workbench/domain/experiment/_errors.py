"""실험 설계 입력을 거절하는 예외와 그 진단 코드의 닫힌 집합."""

from __future__ import annotations

# 실험 설계 진단 코드. 집합에 없는 코드는 만들 때 거절해 호출 지점이 코드를 지어내지 못하게 한다.
EXPERIMENT_SPEC_CODES: frozenset[str] = frozenset(
    {
        "experiment.search.unknown_parameter",
        "experiment.search.invalid_values",
        "experiment.search.too_many_points",
        "experiment.split.invalid",
        "experiment.split.no_window",
    }
)


class InvalidExperimentSpecError(ValueError):
    """탐색 공간이나 분할 규칙이 실험을 만들 수 없는 값이다(spec D5).

    `ValueError` 라 요청 검증 오류로 흘러간다. 422 응답과 번역 키로 옮기는 일은 실험 API(V3-03)가
    `code` 를 읽어 한다. 문장은 사용자에게 그대로 보이므로 한글로 완성하고 재현 값을 `key=value` 로
    싣는다.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in EXPERIMENT_SPEC_CODES:
            raise ValueError(f"등록되지 않은 실험 설계 진단 코드다 — code={code!r}")
        super().__init__(message)
        self.code = code
