"""워크포워드 분할(`SplitSpec`)과 창 목록(spec D5).

학습 구간에서 파라미터를 고르고 바로 다음 구간에서만 채점한다. 창 경계는 달력 날짜다. domain 에는
거래 세션 달력이 없으므로 엠바고(세션 수)는 실험이 읽은 세션 목록을 `SplitSpec.measured_windows` 에
넘겨 학습 끝을 엠바고를 뺀 학습 측정 끝으로 당긴다(V3-05). 검증 창은 서로 붙어 있어 이어 붙인
표본 밖 곡선에 빈 구간이 생기지 않는다.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    EquityCurvePoint,
    MetricRegistry,
    compute_analytics,
    session_sharpe,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunState, RunStatus

from ._errors import InvalidExperimentSpecError
from ._search import GridIndex, neighbor_rank
from ._trial import awaiting_recovery


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
            research_start: 실험의 측정 시작일. 실험 유스케이스는 봉인 판정(`domain/backtest` 의
                `require_environment`)을 통과한 실행 설정의 시작일을 넘기고, 연구 하한은 여기서
                다시 적지 않는다.
            research_end: 실험의 측정 종료일(실행 설정의 종료일).

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
            InvalidExperimentSpecError: 엠바고를 빼면 학습 창에 세션이 남지 않는다.
        """
        first = bisect_left(sessions, window.train_start)
        last = bisect_left(sessions, window.test_start) - 1 - self.embargo_sessions
        if last < first:
            raise InvalidExperimentSpecError(
                "experiment.split.invalid",
                "엠바고를 빼면 학습 창에 세션이 남지 않습니다: "
                f"train_start={window.train_start} test_start={window.test_start} "
                f"embargo_sessions={self.embargo_sessions} "
                f"train_sessions={bisect_left(sessions, window.test_start) - first}",
            )
        return sessions[last]

    def measured_windows(
        self, research_start: date, research_end: date, sessions: Sequence[date]
    ) -> tuple[WalkForwardWindow, ...]:
        """`windows` 의 학습 끝을 엠바고를 뺀 학습 측정 끝으로 당긴 창 — trial 이 학습하는
        구간이다."""
        return tuple(
            replace(window, train_end=self.train_measurement_end(window, sessions))
            for window in self.windows(research_start, research_end)
        )


def pick_window_cell(
    scores: Mapping[GridIndex, float], shape: tuple[int, ...], rule: WindowSelectionRule
) -> GridIndex | None:
    """창 하나의 학습 점수(칸마다 학습 실행의 대표 샤프)로 칸을 고른다. 점수가 없으면 None.

    이웃 평균 기준에서 이웃 점수가 하나도 없는 칸은 자기 점수로 잰다(`neighbor_rank`) — 실패 칸은
    빼고 외톨이 칸도 후보다. 파라미터 지도(`plateau_map`)는 파산 칸을 최하 점수로 넣고 외톨이 칸을
    추천에서 빼므로 둘이 다를 수 있다(의도, V4-03). 값이 같으면 좌표가 앞선 칸이다.
    """

    def value(cell: GridIndex) -> float:
        if rule is WindowSelectionRule.TRAIN_SHARPE_MAX:
            return scores[cell]
        return neighbor_rank(scores, shape, cell)

    return max(sorted(scores), key=value, default=None)


def stitch_out_of_sample(
    segments: Sequence[Sequence[EquityCurvePoint]],
) -> tuple[EquityCurvePoint, ...]:
    """검증 창 실행들의 세션 수익률만 이어 붙인 곡선. 첫 검증 창의 첫 세션이 기준점 1.0 이다.

    수익률은 창마다 그 실행의 첫 스냅숏부터 센다 — 학습 점수(엔진 전체 구간 샤프)도 첫 스냅숏부터
    세므로 초기 자본 → 첫 세션(진입 비용) 수익률은 양쪽 모두 들지 않는다. 그래서 둘째 창부터는 첫
    세션 점이 곡선에 없고, 앞 창의 마지막 값에서 그 창 둘째 세션 수익률로 이어진다. 학습 구간
    수익률은 들어가지 않는다.
    """
    stitched: list[EquityCurvePoint] = []
    for segment in segments:
        if segment and not stitched:
            stitched.append(EquityCurvePoint(segment[0].session, 1.0, None))
        for before, after in pairwise(segment):
            value = stitched[-1].equity * after.equity / before.equity
            stitched.append(EquityCurvePoint(after.session, value, None))
    return tuple(stitched)


def out_of_sample_sharpe(
    curve: Sequence[EquityCurvePoint], registry: MetricRegistry, annualization_days: int
) -> float | None:
    """이어 붙인 곡선의 세션 샤프(연율화 전). 곡선이 비면 None.

    학습 점수(`representative_sharpe`)와 같은 정의다 — 곡선 첫 점부터 수익률을 세어 지표
    레지스트리의 `sharpe` 를 재고 `session_sharpe` 로 세션 단위로 바꾼다.
    """
    if not curve:
        return None
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(AnalysisPoint(p.session, p.equity, 0.0, 0.0) for p in curve),
            traded_notional=0.0,
        ),
        registry,
        annualization_days=annualization_days,
    )
    sharpe = next(metric.value for metric in report.metrics if metric.metric_id == "sharpe")
    return None if sharpe is None else session_sharpe(sharpe, annualization_days)


class WalkForwardGap(StrEnum):
    """이어 붙인 곡선의 요약 지표(표본 밖 샤프·유지율)가 비는 이유. 화면은 번역만 한다.

    값의 정의 순서가 우선순위다 — 끝난 결과(실패·칸 없음)가 아직 도는 창보다 앞선다.
    """

    # 창이 엠바고를 뺀 측정 창이 아닌 V3-05 이전 실험이라 워크포워드 검증을 돌리지 않는다.
    LEGACY_DESIGN = "legacy_design"
    # 실험을 취소해 남은 창을 고르거나 검증하지 않는다(`walk_forward_gap(cancelled=True)`).
    CANCELLED = "cancelled"
    # 모든 창의 검증 실행은 완료됐지만 그 결과 파일을 읽을 수 없어 곡선을 잇지 못한다.
    RESULT_UNREADABLE = "result_unreadable"
    # 검증 실행이 실패·취소로 끝났거나 접수가 거절된 창이 있다. 남은 창만 이으면 낙관 쪽 누락이다.
    TEST_FAILED = "test_failed"
    # 학습에서 대표 샤프가 난 칸이 없는 창이 있다.
    NO_CELL = "no_cell"
    # 아직 고르지 않았거나 검증 실행이 끝나지 않은(재시작 복구 대기 포함) 창이 있다.
    PENDING = "pending"


def window_gap(has_cell: bool, test_run: BacktestRunState | None) -> WalkForwardGap | None:
    """칸을 고른 창 하나가 곡선에 들지 못하는 이유. 검증 실행이 완료됐으면 None.

    Args:
        has_cell: 창에서 고를 칸이 있었다.
        test_run: 검증 실행 상태. 접수가 거절돼 실행이 없으면 None.
    """
    if not has_cell:
        return WalkForwardGap.NO_CELL
    if test_run is None:
        return WalkForwardGap.TEST_FAILED
    if test_run.status is RunStatus.COMPLETED:
        return None
    if test_run.status in (RunStatus.FAILED, RunStatus.CANCELLED) and not awaiting_recovery(
        test_run
    ):
        return WalkForwardGap.TEST_FAILED
    return WalkForwardGap.PENDING


def walk_forward_gap(
    gaps: Sequence[WalkForwardGap | None], *, cancelled: bool
) -> WalkForwardGap | None:
    """창별 이유 가운데 우선순위가 가장 높은 것. 모두 None 이면 None(요약 지표를 낸다).

    취소한 실험은 빈 창이 하나라도 있으면 취소가 이유다 — 남은 창은 더 고르거나 검증하지 않으므로
    "진행 중"으로 남기지 않는다. 취소 전에 모든 창이 완료됐으면 요약을 그대로 낸다.
    """
    gap = next((gap for gap in WalkForwardGap if gap in gaps), None)
    return WalkForwardGap.CANCELLED if cancelled and gap is not None else gap


def walk_forward_retention(
    out_of_sample_sharpe: float | None, train_sharpes: Sequence[float]
) -> float | None:
    """유지율 = 이어 붙인 검증 곡선의 세션 샤프 ÷ 창마다 고른 칸의 학습 세션 샤프 평균.

    학습에서 보인 위험 대비 성과가 표본 밖에서 얼마나 남았는지다. 학습 평균이 0 이하이거나 값이
    없으면 비율이 뜻이 없어 None 이다.
    """
    if out_of_sample_sharpe is None or not train_sharpes:
        return None
    train = sum(train_sharpes) / len(train_sharpes)
    return out_of_sample_sharpe / train if train > 0 else None


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        # 2월 29일에서 평년으로 가면 2월 28일로 맞춘다.
        return day.replace(year=day.year + years, day=28)
