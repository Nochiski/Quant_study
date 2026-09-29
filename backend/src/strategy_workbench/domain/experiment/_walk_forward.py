"""워크포워드 분할(`SplitSpec`)과 창 목록(spec D5).

학습 구간에서 파라미터를 고르고 바로 다음 구간에서만 채점한다. 창 경계는 달력 날짜다. domain 에는
거래 세션 달력이 없으므로 엠바고(세션 수)는 창 날짜에 녹이지 않고, 실행 단계(V3-05)가 읽은 세션
목록을 `SplitSpec.train_measurement_end` 에 넘겨 학습 측정 끝을 당긴다.
검증 창은 서로 붙어 있어 이어 붙인 표본 밖 곡선에 빈 구간이 생기지 않는다.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from ._errors import InvalidExperimentSpecError


class SplitMode(StrEnum):
    # 학습 창 길이를 고정하고 창마다 검증 연수만큼 민다.
    ROLLING = "rolling"
    # 학습 시작일을 연구 구간 시작에 고정하고 학습 창을 늘린다.
    ANCHORED = "anchored"


class WindowSelectionRule(StrEnum):
    """창마다 학습 구간 성과로 파라미터 칸 하나를 고르는 기준."""

    TRAIN_SHARPE_MAX = "train_sharpe_max"
    # 이웃 칸 학습 샤프 평균이 가장 큰 칸 — 한 칸만 튄 봉우리를 피한다(수학 노트 5절).
    NEIGHBOR_MEAN_SHARPE_MAX = "neighbor_mean_sharpe_max"


@dataclass(frozen=True)
class WalkForwardWindow:
    """학습·검증 구간 한 쌍. 네 날짜 모두 양끝 포함이고 연구 구간 안이다."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True, kw_only=True)
class SplitSpec:
    mode: SplitMode
    train_years: int
    test_years: int
    # 학습 측정 끝과 검증 시작 사이에 비우는 거래 세션 수.
    embargo_sessions: int
    selection_rule: WindowSelectionRule = WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX

    def __post_init__(self) -> None:
        problems: list[str] = []
        for name, minimum in (("train_years", 1), ("test_years", 1), ("embargo_sessions", 0)):
            value: object = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                problems.append(f"{name}={value!r}(최소 {minimum})")
        # 문자열로 들어온 값을 enum 으로 맞춘다. 맞추지 않으면 `is` 비교가 "anchored" 를
        # 롤링으로 본다.
        for name, kind in (("mode", SplitMode), ("selection_rule", WindowSelectionRule)):
            value = getattr(self, name)
            try:
                object.__setattr__(self, name, kind(value))
            except ValueError:
                problems.append(f"{name}={value!r}(허용 {[member.value for member in kind]})")
        if problems:
            raise InvalidExperimentSpecError(
                "experiment.split.invalid",
                "분할 설정이 허용 범위를 벗어났습니다: " + ", ".join(problems),
            )

    def windows(self, research_start: date, research_end: date) -> tuple[WalkForwardWindow, ...]:
        """연구 구간을 창 목록으로 나눈다.

        첫 학습 창은 `research_start` 에서 시작하고 검증 창은 `test_years` 씩 붙어 이어진다. 검증
        시작일이 `research_end` 이하인 창은 모두 포함하며, 마지막 창의 검증 끝은 `research_end` 로
        자른다(부분 검증 창).

        Args:
            research_start: 연구 구간 시작일. 호출부가 `domain/backtest` 의 `RESEARCH_START`(봉인
                판정 owner)를 넘기고 여기서 다시 적지 않는다.
            research_end: 실험의 측정 종료일.

        Raises:
            InvalidExperimentSpecError: 첫 학습 창 뒤에 검증할 날이 없다.
        """
        windows: list[WalkForwardWindow] = []
        offset_years = 0
        while (
            test_start := _add_years(research_start, offset_years + self.train_years)
        ) <= research_end:
            train_start = (
                research_start
                if self.mode is SplitMode.ANCHORED
                else _add_years(research_start, offset_years)
            )
            # 검증 끝은 다음 창 검증 시작일 전날이다. 같은 식으로 셈해 2월 29일에서도 빈 날이 없다.
            next_test_start = _add_years(
                research_start, offset_years + self.train_years + self.test_years
            )
            windows.append(
                WalkForwardWindow(
                    train_start=train_start,
                    train_end=test_start - timedelta(days=1),
                    test_start=test_start,
                    test_end=min(next_test_start - timedelta(days=1), research_end),
                )
            )
            offset_years += self.test_years
        if not windows:
            raise InvalidExperimentSpecError(
                "experiment.split.no_window",
                "연구 구간이 학습 기간보다 짧아 검증할 창이 없습니다: "
                f"research_start={research_start} research_end={research_end} "
                f"train_years={self.train_years}",
            )
        return tuple(windows)

    def train_measurement_end(self, window: WalkForwardWindow, sessions: Sequence[date]) -> date:
        """엠바고를 뺀 학습 측정 마지막 세션 — 검증 시작 직전 `embargo_sessions` 세션을 비운다.

        Args:
            window: `windows` 가 낸 창.
            sessions: 오름차순 거래 세션. 학습 창을 덮어야 한다(실행 단계가 세션 달력에서 읽는다).

        Raises:
            ValueError: 엠바고를 빼면 학습 창에 세션이 남지 않는다.
        """
        first = bisect_left(sessions, window.train_start)
        last = bisect_left(sessions, window.test_start) - 1 - self.embargo_sessions
        if last < first:
            raise ValueError(
                "엠바고를 빼면 학습 창에 세션이 남지 않는다 — "
                f"train_start={window.train_start} test_start={window.test_start} "
                f"embargo_sessions={self.embargo_sessions} "
                f"train_sessions={bisect_left(sessions, window.test_start) - first}"
            )
        return sessions[last]


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        # 2월 29일에서 평년으로 가면 2월 28일로 맞춘다.
        return day.replace(year=day.year + years, day=28)
