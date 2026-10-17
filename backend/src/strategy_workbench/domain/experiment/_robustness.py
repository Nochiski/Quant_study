"""결과 화면 "튼튼한지 확인하기"의 기본 실험 설계(검증 랩 V5-02, US-DM-11).

실험 설정을 모르는 사용자가 버튼 하나로 지금 설정의 표본 밖 성과와 파라미터 고원을 보게 한다.
설계 규칙의 정본은 이 파일 하나다 — 화면은 미리 계산 응답(설계·시도 수)을 그대로 보인다.

- 탐색: 문서가 선언한 파라미터 전부를 그 정의의 격자 값 전체로 편다(`search` 값 None). 선언이 없으면
  탐색 축이 없어 칸 하나(기본값)로 워크포워드만 돈다 — 고원은 볼 수 없다.
- 분할: 롤링 워크포워드, 검증 1년, 엠바고 5세션. 학습은 연구 관행인 3년을 쓰되 실행 기간이 학습 3년
  + 검증 1년을 다 담지 못하면 1년으로 줄인다. 학습 1년 + 검증 1년도 담지 못하면(2년 미만) 거절한다 —
  1년보다 짧은 검증 창 하나로는 표본 밖 성과를 말할 수 없다.
- 엠바고 5세션(한 주)은 학습 끝의 체결·포지션이 검증 첫 세션 수익으로 새는 것을 막는 최소 간격이다
  (López de Prado 의 purging·embargo). 분할의 창 선택 기준은 기본값(이웃 평균 샤프 최대)이다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from strategy_workbench.domain.strategy.facade.specification import (
    ParameterDefinition,
    ParameterValue,
)

from ._errors import InvalidExperimentSpecError
from ._walk_forward import SplitMode, SplitSpec, _add_years

__all__ = [
    "ROBUSTNESS_EMBARGO_SESSIONS",
    "ROBUSTNESS_TEST_YEARS",
    "ROBUSTNESS_TRAIN_YEARS",
    "robustness_search",
    "robustness_split",
]

ROBUSTNESS_TEST_YEARS = 1
# 앞에서부터 고른다: 담을 수 있는 가장 긴 학습 기간.
ROBUSTNESS_TRAIN_YEARS = (3, 1)
ROBUSTNESS_EMBARGO_SESSIONS = 5
# 기간이 몇 년을 담는지 셀 때의 여유. 연구 기간은 거래일로 정해 첫 거래일(1월 초)부터 마지막
# 거래일(12월 말)까지라 달력 연수보다 며칠 짧다 — 연말연시 휴장을 넉넉히 덮는 2주다.
_CALENDAR_SLACK = timedelta(days=14)


def robustness_search(
    parameters: Iterable[ParameterDefinition],
) -> dict[str, list[ParameterValue] | None]:
    """선언한 파라미터마다 정의의 격자 전체(None). 격자를 펼 수 없으면 실험 만들기가 거절한다."""
    return {parameter.parameter_id: None for parameter in parameters}


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
