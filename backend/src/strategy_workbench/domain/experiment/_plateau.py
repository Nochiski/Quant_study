"""파라미터 고원·민감도 — 칸마다 추천·봉우리·실패 판정(검증 랩 V4-03, 수학 노트 5절, spec D8).

칸 점수는 그 칸의 창별 학습 세션 샤프 평균이다. 고원 점수는 이웃 칸 점수의 평균
(`neighbor_mean`)이고, 추천 후보는 고원 점수가 가장 높은 칸이다. 봉우리는 점수가 가장 높지만
이웃이 크게 낮은 칸이다. 민감도는 숫자 파라미터를 ±20% 바꾼 칸(가장 가까운 격자값)에서 점수가
가장 크게 떨어진 비율이다.

학습이 파산한 칸은 "없는 칸"이 아니라 최하 점수(`bankrupt_score`)로 이웃 평균과 민감도에 든다 —
파산 칸 옆 칸이 경계 칸처럼 고원으로 보이지 않게 한다(#364 리뷰 P3-2). 파산은 전략의 결과라 든다
(#383 과 같은 기준). 접수 거절·엔진 오류 같은 비전략 실패는 다시 돌릴 수 있는 기반 문제라 아직
돌지 않았거나 취소된 칸처럼 뺀다. 워크포워드 창 선택(`pick_window_cell`)의 규칙은 바꾸지 않는다 —
창 선택은 과최적화를 그대로 드러내는 순진한 선택이고, 이 지도는 사용자가 고를 때 보는 신호다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from ._search import GridIndex, SearchSpec, neighbor_mean, neighbor_rank
from ._trial import TrialStatus

# 봉우리 판정: 점수가 가장 높은 칸인데 이웃 평균이 제 점수의 이 비율보다 낮으면 봉우리다. 수학 노트
# 5절 예시의 봉우리 비율 0.28(1.12 → 이웃 0.31)을 봉우리로 잡고 추천 칸 비율 0.95(0.86 → 0.82)는
# 잡지 않는 둥근 값이다(두 비율의 기하 평균 ≈ 0.52). 노트에 기준이 없어 정한 제품 판단이다.
PEAK_NEIGHBOR_RATIO = 0.5
# 민감도에서 숫자 파라미터를 바꾸는 폭(수학 노트 5절 ±20%).
SENSITIVITY_STEP = 0.2


class CellVerdict(StrEnum):
    """칸 판정. 화면은 번역만 한다."""

    # 고원 점수(이웃 평균)가 가장 높은 칸. 동점이면 좌표가 앞선 칸이다.
    RECOMMENDED = "recommended"
    # 점수가 가장 높지만 이웃 평균이 제 점수의 `PEAK_NEIGHBOR_RATIO` 보다 낮다 — 운으로 튄 칸.
    PEAK = "peak"
    # 학습이 실패했다. 파산이면 이웃 평균·민감도에 최하 점수로 들고, 비전략 실패면 빠진다.
    FAILED = "failed"
    # 아직 돌지 않았거나 취소됐거나 점수가 비었다. 시도가 아니라 이웃 평균에서도 빠진다.
    UNSCORED = "unscored"
    # 점수가 있는 나머지 칸.
    SCORED = "scored"


@dataclass(frozen=True)
class CellPlateau:
    grid_index: GridIndex
    verdict: CellVerdict
    # 칸 점수(창별 학습 세션 샤프 평균). 실패·미채점 칸은 None.
    score: float | None
    # 이웃 칸 점수의 평균(파산 이웃은 최하 점수). 점수 있는 이웃이 없으면 None.
    plateau_score: float | None
    # ±20% 칸에서 점수가 가장 크게 떨어진 비율(음수면 모두 나아졌다). 점수가 0 이하이거나 비교할
    # 칸이 없으면 None.
    sensitivity: float | None
    # 민감도를 낸 칸(하락이 가장 컸던 옮긴 칸). 격자가 성기면 ±20% 보다 멀리 옮긴 칸일 수 있다.
    sensitivity_cell: GridIndex | None


@dataclass(frozen=True)
class CellOutcomes:
    """칸별 결과. 점수 칸·파산 칸·비전략 실패 칸은 서로 겹치지 않는다."""

    scores: dict[GridIndex, float]
    bankrupt: set[GridIndex]
    failed: set[GridIndex]


def bankrupt_score(scores: Iterable[float]) -> float:
    """파산 칸을 대신하는 최하 점수 = min(점수가 난 칸의 최솟값, 0).

    파산은 아무것도 하지 않은 것(샤프 0)보다 나쁘다 — 살아남은 칸이 하나뿐이거나 최저 칸 옆이
    파산일 때도 파산 이웃이 자기 점수와 같아 보이지 않는다.
    """
    return min((*scores, 0.0))


def cell_outcomes(
    trials: Iterable[tuple[GridIndex, TrialStatus, bool, bool, float | None]],
) -> CellOutcomes:
    """trial `(칸, 상태, 복구 대기, 파산, 학습 점수)` 을 칸 점수와 파산·비전략 실패 칸으로 모은다.

    창 하나라도 파산했으면 파산 칸이고, 아니면서 창 하나라도 실패로 끝났으면(복구 대기 제외) 비전략
    실패 칸이다. 모든 창이 완료되고 점수가 있으면 창별 점수의 평균이 칸 점수다. 나머지(대기·도는
    중·취소·복구 대기·점수 없음)는 어느 쪽에도 들지 않는다.
    """
    windows: dict[GridIndex, list[tuple[TrialStatus, bool, bool, float | None]]] = {}
    for cell, status, recovering, bankrupt, score in trials:
        windows.setdefault(cell, []).append((status, recovering, bankrupt, score))
    outcomes = CellOutcomes({}, set(), set())
    for cell, results in windows.items():
        failures = [
            bankrupt
            for status, recovering, bankrupt, _ in results
            if status is TrialStatus.FAILED and not recovering
        ]
        if any(failures):
            outcomes.bankrupt.add(cell)
        elif failures:
            outcomes.failed.add(cell)
        elif all(
            status is TrialStatus.COMPLETED and score is not None for status, *_, score in results
        ):
            values = [score for *_, score in results if score is not None]
            outcomes.scores[cell] = sum(values) / len(values)
    return outcomes


def plateau_map(search: SearchSpec, outcomes: CellOutcomes) -> tuple[CellPlateau, ...]:
    """그리드 칸마다 판정·점수·고원 점수·민감도(좌표 순)."""
    shape, scores = search.shape, outcomes.scores
    floor = bankrupt_score(scores.values())
    filled = dict(scores) | {cell: floor for cell in outcomes.bankrupt}
    plateau = {cell: neighbor_mean(filled, shape, cell) for cell in scores}
    # 점수 있는 이웃이 없는 외톨이 칸은 추천 후보가 아니다 — 운으로 튄 한 칸이 원점수로 추천되지
    # 않게 한다. 모든 칸이 외톨이일 때만(한 칸 그리드 등) 자기 점수로 잰다(`neighbor_rank`).
    candidates = [cell for cell in sorted(scores) if plateau[cell] is not None] or sorted(scores)
    recommended = max(candidates, key=lambda cell: neighbor_rank(filled, shape, cell), default=None)
    top = max(scores.values(), default=0.0)
    # 최고 점수가 같은 칸은 모두 같은 기준으로 본다. 외톨이 칸은 자기 점수로 재므로(이웃이 낮은지
    # 알 수 없다) 봉우리가 아니다.
    peaks = {
        cell
        for cell, score in scores.items()
        if score == top > 0 and neighbor_rank(filled, shape, cell) < PEAK_NEIGHBOR_RATIO * score
    }
    cells: list[CellPlateau] = []
    for cell in search.indices():
        if cell in scores:
            verdict = (
                CellVerdict.RECOMMENDED
                if cell == recommended
                else CellVerdict.PEAK
                if cell in peaks
                else CellVerdict.SCORED
            )
            sensitivity, moved = _sensitivity(search, filled, cell, scores[cell])
            cells.append(
                CellPlateau(cell, verdict, scores[cell], plateau[cell], sensitivity, moved)
            )
        else:
            failed = cell in outcomes.bankrupt or cell in outcomes.failed
            verdict = CellVerdict.FAILED if failed else CellVerdict.UNSCORED
            cells.append(CellPlateau(cell, verdict, None, None, None, None))
    return tuple(cells)


def _sensitivity(
    search: SearchSpec, filled: Mapping[GridIndex, float], cell: GridIndex, score: float
) -> tuple[float | None, GridIndex | None]:
    """숫자 축마다 값을 ±20% 바꾼 칸의 점수 하락 비율 가운데 최댓값과 그 칸.

    바꾼 값에 가장 가까운 격자값을 그 방향(값보다 작은 쪽·큰 쪽)에서만 찾는다 — 격자가 성겨 가장
    가까운 값이 제 값이면 그 방향의 다음 격자값을 쓴다(더 멀리 바꾼 값이라 하락이 크게 잡히는
    보수 쪽). 그 방향에 격자값이 없거나(축 끝) 그 칸이 시도가 아니면 그 방향은 빠진다.
    """
    if score <= 0:
        return None, None
    drops: list[tuple[float, GridIndex]] = []
    for axis_index, axis in enumerate(search.axes):
        numbers = _numbers(axis.values)
        if numbers is None:
            continue
        position = cell[axis_index]
        for factor in (1 - SENSITIVITY_STEP, 1 + SENSITIVITY_STEP):
            other = _nearest_on_side(numbers, position, numbers[position] * factor)
            if other is None:
                continue
            moved = (*cell[:axis_index], other, *cell[axis_index + 1 :])
            if moved in filled:
                drops.append(((score - filled[moved]) / score, moved))
    worst = max(drops, key=lambda drop: drop[0], default=None)
    return (None, None) if worst is None else worst


def _nearest_on_side(values: Sequence[float], position: int, target: float) -> int | None:
    """`target` 쪽(제 값보다 작은 쪽이나 큰 쪽)의 격자값 가운데 `target` 에 가장 가까운 위치.

    같은 거리면 제 위치에 가까운 쪽이다. 그쪽에 값이 없거나 `target` 이 제 값과 같으면(값 0) None.
    """
    value = values[position]
    if target == value:
        return None
    side = [
        index
        for index, candidate in enumerate(values)
        if (candidate < value if target < value else candidate > value)
    ]
    return min(
        side,
        key=lambda index: (abs(values[index] - target), abs(index - position)),
        default=None,
    )


def _numbers(values: Sequence[ParameterValue]) -> tuple[float, ...] | None:
    """숫자 축이면 값들, 선택지(문자열·참거짓) 축이면 None — ±20% 는 숫자에만 뜻이 있다."""
    if all(isinstance(value, int | float) and not isinstance(value, bool) for value in values):
        return tuple(float(value) for value in values)
    return None
