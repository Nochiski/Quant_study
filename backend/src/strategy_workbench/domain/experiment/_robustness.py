"""결과 화면 "튼튼한지 확인하기"의 기본 실험 설계(검증 랩 V5-02, US-DM-11).

실험 설정을 모르는 사용자가 버튼 하나로 지금 설정의 표본 밖 성과와 파라미터 고원을 보게 한다. 설계
규칙의 정본은 이 파일 하나다 — 화면은 미리 계산 응답(설계·시도 수)을 그대로 보인다.

- 탐색: 지금 설정 **주변의 국소 격자**다(견고성 확인의 관행 — 전체 격자는 최적화이지 확인이
  아니고, 칸 하나하나가 계열 시도로 남아 DSR 을 깎는다). 숫자 파라미터는 지금 값과 그 양옆(간격이
  있으면 ±간격, 없으면 ±20% — 고원 민감도와 같은 폭)을 범위 안에서 편다. 선택지·참거짓
  파라미터는 구조를 바꾸는 선택이라 지금 값에 둔다. 칸 수가 `ROBUSTNESS_MAX_POINTS`
  를 넘으면 거절한다(파라미터가 너무 많다 — 실험 화면에서 축을 고른다). 선언이 없으면 탐색 축이 없어
  칸 하나(기본값)로 워크포워드만 돈다 — 고원은 볼 수 없다.
- 분할: 롤링 워크포워드, 검증 1년, 엠바고 5세션. 학습은 연구 관행인 3년을 쓰되 실행 기간이 학습
  3년 + 검증 1년을 다 담지 못하면 1년으로 줄인다. 학습 1년 + 검증 1년도 담지 못하면(2년
  미만) 거절한다 — 1년보다 짧은 검증 창 하나로는 표본 밖 성과를 말할 수 없다.
- 기간 끝: 마지막 검증 창이 `ROBUSTNESS_MIN_TEST_DAYS` 보다 짧으면 그 꼬리 창을
  버리도록 실험 기간을 앞 창의 검증 끝으로 당긴다 — 며칠짜리 검증 창의 샤프는 잡음인데 학습을 한 번
  더 돌리고 유지율에 섞인다.
- 엠바고 5세션(한 주)은 학습 끝과 검증 시작 사이의 수익률 자기상관·겹치는 보유 기간으로 학습 점수와
  검증 성과가 이어 붙는 것을 줄이는 최소 간격이다(López de Prado 의
  purging·embargo). 창 선택 기준은 기본값(이웃 평균 샤프 최대)이다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    IntegerParameter,
    ParameterDefinition,
    ParameterValue,
)

from ._errors import InvalidExperimentSpecError
from ._walk_forward import SplitMode, SplitSpec, _add_years

__all__ = [
    "ROBUSTNESS_EMBARGO_SESSIONS",
    "ROBUSTNESS_MAX_POINTS",
    "ROBUSTNESS_MIN_TEST_DAYS",
    "ROBUSTNESS_RELATIVE_STEP",
    "ROBUSTNESS_TEST_YEARS",
    "ROBUSTNESS_TRAIN_YEARS",
    "robustness_end",
    "robustness_search",
    "robustness_split",
]

ROBUSTNESS_TEST_YEARS = 1
# 앞에서부터 고른다: 담을 수 있는 가장 긴 학습 기간.
ROBUSTNESS_TRAIN_YEARS = (3, 1)
ROBUSTNESS_EMBARGO_SESSIONS = 5
# 간격 없는 실수 파라미터를 옮기는 폭(고원 민감도 `SENSITIVITY_STEP` 과 같은 ±20%).
ROBUSTNESS_RELATIVE_STEP = 0.2
# 국소 격자 칸 수 상한: 숫자 파라미터 넷(3⁴)까지.
ROBUSTNESS_MAX_POINTS = 81
# 꼬리 검증 창의 최소 길이(약 반년). 이보다 짧은 마지막 창은 버린다.
ROBUSTNESS_MIN_TEST_DAYS = 182
# 기간이 몇 년을 담는지 셀 때의 여유. 연구 기간은 거래일로 정해 첫 거래일(1월 초)부터 마지막
# 거래일(12월 말)까지라 달력 연수보다 며칠 짧다 — 연말연시 휴장을 넉넉히 덮는 2주다.
_CALENDAR_SLACK = timedelta(days=14)


def robustness_search(
    parameters: Iterable[ParameterDefinition],
    current: Mapping[str, ParameterValue],
) -> dict[str, list[ParameterValue] | None]:
    """지금 값(`current`, 해소된 실행 값) 주변의 국소 격자(모듈 docstring)."""
    search: dict[str, list[ParameterValue] | None] = {}
    points = 1
    for parameter in parameters:
        if isinstance(parameter, ChoiceParameter):
            continue
        value = current.get(parameter.parameter_id, parameter.default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if isinstance(parameter, IntegerParameter):
            delta: float = parameter.step
        elif parameter.step is not None:
            delta = parameter.step
        else:
            delta = abs(value) * ROBUSTNESS_RELATIVE_STEP or (
                (parameter.maximum - parameter.minimum) * ROBUSTNESS_RELATIVE_STEP / 2
            )
        values: list[ParameterValue] = []
        for candidate in (value - delta, value, value + delta):
            if parameter.minimum <= candidate <= parameter.maximum and candidate not in values:
                values.append(
                    int(candidate) if isinstance(parameter, IntegerParameter) else float(candidate)
                )
        search[parameter.parameter_id] = values
        points *= len(values)
    if points > ROBUSTNESS_MAX_POINTS:
        raise InvalidExperimentSpecError(
            "experiment.robustness.too_many_parameters",
            f"숫자 파라미터가 많아 주변 격자가 {points}칸입니다(상한 {ROBUSTNESS_MAX_POINTS}). "
            f"실험 화면에서 확인할 파라미터를 골라 주세요: parameters={sorted(search)}",
        )
    return search


def robustness_split(start: date, end: date) -> SplitSpec:
    """실행 기간(`start`~`end`, 양끝 포함)에 맞는 롤링 분할(모듈 docstring)."""
    for train_years in ROBUSTNESS_TRAIN_YEARS:
        span_years = train_years + ROBUSTNESS_TEST_YEARS
        if _add_years(start, span_years) - timedelta(days=1) - _CALENDAR_SLACK <= end:
            return SplitSpec(
                mode=SplitMode.ROLLING,
                train_years=train_years,
                test_years=ROBUSTNESS_TEST_YEARS,
                embargo_sessions=ROBUSTNESS_EMBARGO_SESSIONS,
            )
    shortest = min(ROBUSTNESS_TRAIN_YEARS) + ROBUSTNESS_TEST_YEARS
    raise InvalidExperimentSpecError(
        "experiment.robustness.period_too_short",
        f"튼튼한지 확인하려면 실행 기간이 {shortest}년 이상이어야 합니다(학습 "
        f"{min(ROBUSTNESS_TRAIN_YEARS)}년 + 검증 {ROBUSTNESS_TEST_YEARS}년): "
        f"start={start} end={end}",
    )


def robustness_end(split: SplitSpec, start: date, end: date) -> date:
    """실험 기간의 끝. 마지막 검증 창이 너무 짧으면 그 앞 창의 검증 끝이다(모듈 docstring)."""
    windows = split.windows(start, end)
    last = windows[-1]
    if len(windows) > 1 and (last.test_end - last.test_start).days < ROBUSTNESS_MIN_TEST_DAYS:
        return windows[-2].test_end
    return end
