"""파라미터 고원·민감도 — 칸마다 추천·봉우리·실패 판정(검증 랩 V4-03, 수학 노트 5절, spec D8).

칸 점수는 그 칸의 창별 학습 세션 샤프 평균이다. 고원 점수는 이웃 칸 점수의 평균
(`neighbor_mean`)이고, 추천 후보는 고원 점수가 가장 높은 칸이다. 봉우리는 점수가 가장 높지만
이웃이 크게 낮은 칸이다. 민감도는 숫자 파라미터를 ±20% 바꾼 칸(가장 가까운 격자값)에서 점수가
가장 크게 떨어진 비율이다.

학습이 실패·파산한 칸은 "없는 칸"이 아니라 최하 점수(그 실험에서 점수가 난 칸의 최솟값)로 이웃
평균과 민감도에 든다 — 파산 칸 옆 칸이 경계 칸처럼 고원으로 보이지 않게 한다(#364 리뷰 P3-2, #383
과 같은 방향). 아직 돌지 않았거나 취소된 칸은 시도가 아니라 뺀다. 워크포워드 창 선택
(`pick_window_cell`)의 규칙은 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from ._search import GridIndex, SearchSpec, neighbor_mean
from ._trial import TrialStatus

# 봉우리 판정: 가장 높은 점수의 칸인데 이웃 평균이 제 점수의 이 비율보다 낮으면 봉우리다. 수학 노트
# 5절 예시(최고 칸 1.12·이웃 평균 0.31 → 0.28, 추천 칸 0.86·0.82 → 0.95)를 가르는 중간값이다.
PEAK_NEIGHBOR_RATIO = 0.5
# 민감도에서 숫자 파라미터를 바꾸는 폭(수학 노트 5절 ±20%).
SENSITIVITY_STEP = 0.2


class CellVerdict(StrEnum):
    """칸 판정. 화면은 번역만 한다."""

    # 고원 점수(이웃 평균)가 가장 높은 칸. 동점이면 좌표가 앞선 칸이다.
    RECOMMENDED = "recommended"
    # 점수가 가장 높지만 이웃 평균이 제 점수의 `PEAK_NEIGHBOR_RATIO` 보다 낮다 — 운으로 튄 칸.
    PEAK = "peak"
    # 학습이 실패·파산했다. 이웃 평균·민감도에는 최하 점수로 든다.
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
    # 이웃 칸 점수의 평균(실패 이웃은 최하 점수). 점수 있는 이웃이 없으면 None.
    plateau_score: float | None
    # ±20% 칸에서 점수가 가장 크게 떨어진 비율(음수면 모두 나아졌다). 점수가 0 이하이거나 비교할
    # 칸이 없으면 None.
    sensitivity: float | None


def cell_outcomes(
    trials: Iterable[tuple[GridIndex, TrialStatus, bool, float | None]],
) -> tuple[dict[GridIndex, float], set[GridIndex]]:
    """trial `(칸, 상태, 복구 대기, 학습 점수)` 을 칸 점수와 실패 칸으로 모은다.

    창 하나라도 실패로 끝났으면(복구 대기 제외) 그 칸은 실패다. 모든 창이 완료되고 점수가 있으면
    창별 점수의 평균이 칸 점수다. 나머지(대기·도는 중·취소·복구 대기·점수 없음)는 어느 쪽에도 들지
    않는다.
    """
    windows: dict[GridIndex, list[tuple[TrialStatus, bool, float | None]]] = {}
    for cell, status, recovering, score in trials:
        windows.setdefault(cell, []).append((status, recovering, score))
    scores: dict[GridIndex, float] = {}
    failed: set[GridIndex] = set()
    for cell, results in windows.items():
        if any(
            status is TrialStatus.FAILED and not recovering for status, recovering, _ in results
        ):
            failed.add(cell)
        elif all(
            status is TrialStatus.COMPLETED and score is not None for status, _, score in results
        ):
            values = [score for _, _, score in results if score is not None]
            scores[cell] = sum(values) / len(values)
    return scores, failed


def plateau_map(
    search: SearchSpec, scores: Mapping[GridIndex, float], failed: Collection[GridIndex]
) -> tuple[CellPlateau, ...]:
    """그리드 칸마다 판정·점수·고원 점수·민감도(좌표 순)."""
    shape = search.shape
    floor = min(scores.values(), default=None)
    filled = dict(scores) | ({cell: floor for cell in failed} if floor is not None else {})
    plateau = {cell: neighbor_mean(filled, shape, cell) for cell in scores}
    ranked = sorted(scores)

    def plateau_rank(cell: GridIndex) -> float:
        value = plateau[cell]
        return scores[cell] if value is None else value

    recommended = max(ranked, key=plateau_rank, default=None)
    best = max(ranked, key=scores.__getitem__, default=None)
    peak = (
        best
        if best is not None
        and scores[best] > 0
        and plateau_rank(best) < PEAK_NEIGHBOR_RATIO * scores[best]
        else None
    )
    cells: list[CellPlateau] = []
    for cell in search.indices():
        if cell in scores:
            verdict = (
                CellVerdict.RECOMMENDED
                if cell == recommended
                else CellVerdict.PEAK
                if cell == peak
                else CellVerdict.SCORED
            )
            cells.append(
                CellPlateau(
                    cell,
                    verdict,
                    scores[cell],
                    plateau[cell],
                    _sensitivity(search, filled, cell, scores[cell]),
                )
            )
        else:
            verdict = CellVerdict.FAILED if cell in failed else CellVerdict.UNSCORED
            cells.append(CellPlateau(cell, verdict, None, None, None))
    return tuple(cells)


def _sensitivity(
    search: SearchSpec, filled: Mapping[GridIndex, float], cell: GridIndex, score: float
) -> float | None:
    """숫자 축마다 값을 ±20% 바꾼 칸의 점수 하락 비율 가운데 최댓값.

    바꾼 값에 가장 가까운 격자값을 그 방향(값보다 작은 쪽·큰 쪽)에서만 찾는다 — 격자가 성겨 가장
    가까운 값이 제 값이면 그 방향의 다음 격자값을 쓴다(더 멀리 바꾼 값이라 하락이 크게 잡히는
    보수 쪽). 그 방향에 격자값이 없거나(축 끝) 그 칸이 시도가 아니면 그 방향은 빠진다.
    """
    if score <= 0:
        return None
    drops: list[float] = []
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
                drops.append((score - filled[moved]) / score)
    return max(drops, default=None)


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
