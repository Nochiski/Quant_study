"""domain.experiment 고원·민감도(V4-03, 수학 노트 5절)를 손계산 정답으로 본다."""

from __future__ import annotations

from collections.abc import Collection, Mapping

import pytest

from strategy_workbench.domain.experiment.facade.design import (
    PEAK_NEIGHBOR_RATIO,
    CellOutcomes,
    CellPlateau,
    CellVerdict,
    GridIndex,
    SearchAxis,
    SearchSpec,
    WindowSelectionRule,
    bankrupt_score,
    cell_outcomes,
    pick_window_cell,
    plateau_map,
)
from strategy_workbench.domain.experiment.facade.trial import TrialStatus
from strategy_workbench.domain.strategy.facade.specification import ParameterValue

# 3×3 격자(lookback 10·20·30 × weight 0.1·0.2·0.3). 행이 lookback, 열이 weight 다.
_GRID = SearchSpec((SearchAxis("lookback", (10, 20, 30)), SearchAxis("weight", (0.1, 0.2, 0.3))))
# 왼쪽 위가 고르게 좋고 오른쪽 아래 (2,2) 하나만 튄다.
#   0.8 0.9 0.2
#   0.9 1.0 0.1
#   0.2 0.1 2.0
_SCORES: dict[GridIndex, float] = {
    (0, 0): 0.8,
    (0, 1): 0.9,
    (0, 2): 0.2,
    (1, 0): 0.9,
    (1, 1): 1.0,
    (1, 2): 0.1,
    (2, 0): 0.2,
    (2, 1): 0.1,
    (2, 2): 2.0,
}


def _axis(*values: ParameterValue) -> SearchSpec:
    return SearchSpec((SearchAxis("lookback", values),))


def _map(
    search: SearchSpec,
    scores: Mapping[GridIndex, float],
    bankrupt: Collection[GridIndex] = (),
    failed: Collection[GridIndex] = (),
) -> tuple[CellPlateau, ...]:
    return plateau_map(search, CellOutcomes(dict(scores), set(bankrupt), set(failed)))


def _by_cell(cells: tuple[CellPlateau, ...]) -> dict[GridIndex, CellPlateau]:
    return {cell.grid_index: cell for cell in cells}


def test_the_recommended_cell_has_the_best_neighbor_mean_and_a_lonely_top_is_a_peak() -> None:
    cells = _map(_GRID, _SCORES)

    # 이웃 평균(체비쇼프 거리 1): (0,0)=(0.9+0.9+1.0)/3, (1,1)=5.2/8, (1,2)=(0.9+0.2+1.0+0.1+2.0)/5,
    # (2,2)=(1.0+0.1+0.1)/3. 최고 점수 (2,2) 2.0 의 이웃 평균 0.4 는 절반(1.0)보다 낮다.
    assert tuple(cell.grid_index for cell in cells) == _GRID.indices()
    assert [cell.plateau_score for cell in cells] == pytest.approx(
        [2.8 / 3, 0.6, 2.0 / 3, 0.6, 0.65, 0.84, 2.0 / 3, 0.84, 0.4]
    )
    assert [cell.verdict for cell in cells] == [
        CellVerdict.RECOMMENDED,
        *[CellVerdict.SCORED] * 7,
        CellVerdict.PEAK,
    ]
    assert [cell.score for cell in cells] == list(_SCORES.values())


def test_a_bankrupt_cell_enters_the_neighbor_mean_at_the_bankrupt_score() -> None:
    # (0,2) 학습이 파산했다. 최하 점수는 min(점수 최솟값 0.1, 0) = 0 이다.
    scores = {cell: score for cell, score in _SCORES.items() if cell != (0, 2)}

    cells = _by_cell(_map(_GRID, scores, bankrupt={(0, 2)}))

    # 빼면 (1,2) 이웃 평균이 (0.9+1.0+0.1+2.0)/4 = 1.0 으로 추천이 된다. 0 으로 넣으면
    # (0.9+0+1.0+0.1+2.0)/5 = 0.8 이라 (0,0) 2.8/3 이 그대로 추천이다.
    assert cells[(1, 2)].plateau_score == pytest.approx(0.8)
    assert cells[(0, 1)].plateau_score == pytest.approx(2.8 / 5)
    assert cells[(1, 1)].plateau_score == pytest.approx(5.0 / 8)
    assert cells[(0, 0)].verdict is CellVerdict.RECOMMENDED
    assert cells[(0, 2)] == CellPlateau((0, 2), CellVerdict.FAILED, None, None, None, None)


@pytest.mark.parametrize(
    ("scores", "expected"), [([0.4, -0.3], -0.3), ([0.4, 0.9], 0.0), ([], 0.0)]
)
def test_the_bankrupt_score_is_never_above_zero(scores: list[float], expected: float) -> None:
    assert bankrupt_score(scores) == expected


def test_a_lone_survivor_among_bankrupt_cells_is_not_a_flat_plateau() -> None:
    """#403 리뷰 P2-1 ①: 살아남은 칸이 하나뿐이어도 파산 이웃이 자기 점수와 같아 보이지 않는다."""
    (cell, *_) = _map(_axis(10, 20, 30), {(0,): 1.5}, bankrupt={(1,), (2,)})

    # 이웃 20 은 파산이라 0. 민감도: 12 쪽 가장 가까운 20 → (1.5 − 0)/1.5 = 1.0.
    assert cell == CellPlateau((0,), CellVerdict.RECOMMENDED, 1.5, 0.0, 1.0, (1,))


def test_moving_into_a_bankrupt_cell_is_a_full_drop_even_from_the_lowest_cell() -> None:
    """#403 리뷰 P2-1 ②: 최저 칸이 +20% 쪽 파산 칸으로 옮기면 하락은 100% 다."""
    scores: dict[GridIndex, float] = {(0,): 1.0, (1,): 0.9, (2,): 0.4}

    cell = _by_cell(_map(_axis(10, 20, 30, 40), scores, bankrupt={(3,)}))[(2,)]

    # 30 → 24 쪽 20(0.9): (0.4 − 0.9)/0.4 = −1.25, 36 쪽 40 파산(0): 1.0.
    assert (cell.sensitivity, cell.sensitivity_cell) == (pytest.approx(1.0), (3,))


def test_a_failure_outside_the_strategy_stays_out_of_the_neighbor_mean() -> None:
    # (1,) 은 접수 거절·엔진 오류로 끝났다 — 다시 돌릴 수 있는 기반 문제라 파산처럼 셈하지 않는다.
    cells = _map(_axis(10, 20, 30), {(0,): 1.0, (2,): 0.5}, failed={(1,)})

    assert [(cell.verdict, cell.plateau_score) for cell in cells] == [
        (CellVerdict.RECOMMENDED, None),
        (CellVerdict.FAILED, None),
        (CellVerdict.SCORED, None),
    ]


def test_window_selection_still_leaves_failed_cells_out() -> None:
    """창 선택(`pick_window_cell`)은 V4-03 에서 바꾸지 않는다 — 실패 칸을 빼고 이웃 평균을 낸다.

    창 선택은 과최적화를 그대로 드러내는 순진한 선택이고 지도는 고를 때 보는 신호라, 같은 점수에서
    둘이 갈리는 것은 의도다(리드 결정).
    """
    scores = {cell: score for cell, score in _SCORES.items() if cell != (0, 2)}
    rule = WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX

    assert pick_window_cell(scores, _GRID.shape, rule) == (1, 2)
    assert pick_window_cell(_SCORES, _GRID.shape, rule) == (0, 0)
    recommended = [
        cell.grid_index
        for cell in _map(_GRID, scores, bankrupt={(0, 2)})
        if cell.verdict is CellVerdict.RECOMMENDED
    ]
    assert recommended == [(0, 0)]


def test_a_lonely_cell_is_not_recommended_on_its_own_score() -> None:
    """#403 리뷰 P2-2: 5×5 에서 고르게 0.8 인 2×2 고원과, 이웃 점수가 없는 (4,4) 1.5 한 칸."""
    search = SearchSpec(
        (SearchAxis("lookback", (10, 20, 30, 40, 50)), SearchAxis("hold", (1, 2, 3, 4, 5)))
    )
    scores: dict[GridIndex, float] = {
        (0, 0): 0.8,
        (0, 1): 0.8,
        (1, 0): 0.8,
        (1, 1): 0.8,
        (4, 4): 1.5,
    }

    cells = _by_cell(_map(search, scores))

    assert cells[(0, 0)].verdict is CellVerdict.RECOMMENDED
    # 이웃이 낮은지 알 수 없어 봉우리도 아니다.
    assert (cells[(4, 4)].verdict, cells[(4, 4)].plateau_score) == (CellVerdict.SCORED, None)
    # 창 선택은 외톨이 칸을 자기 점수로 잰다(바꾸지 않음).
    rule = WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX
    assert pick_window_cell(scores, search.shape, rule) == (4, 4)


def test_unscored_cells_stay_out_and_lonely_cells_wait_behind_neighbored_ones() -> None:
    # (2,) 는 아직 돌지 않았다. (3,) 은 점수 있는 이웃이 없어 0.9 여도 추천 후보가 아니다.
    cells = _map(_axis(10, 20, 30, 40), {(0,): 0.2, (1,): 0.3, (3,): 0.9})

    assert [(cell.verdict, cell.plateau_score) for cell in cells] == [
        (CellVerdict.RECOMMENDED, 0.3),
        (CellVerdict.SCORED, 0.2),
        (CellVerdict.UNSCORED, None),
        (CellVerdict.SCORED, None),
    ]


def test_when_every_cell_is_lonely_the_own_score_decides() -> None:
    cells = _map(_axis(10, 20, 30), {(0,): 0.5, (2,): 0.9})

    assert [cell.verdict for cell in cells] == [
        CellVerdict.SCORED,
        CellVerdict.UNSCORED,
        CellVerdict.RECOMMENDED,
    ]
    assert [cell.verdict for cell in _map(_axis(10), {(0,): 0.4})] == [CellVerdict.RECOMMENDED]


@pytest.mark.parametrize(
    ("neighbor", "verdict"),
    [
        # 최고 칸 1.0 의 이웃 평균이 정확히 절반이면 봉우리가 아니다.
        (PEAK_NEIGHBOR_RATIO, CellVerdict.SCORED),
        (0.25, CellVerdict.PEAK),
    ],
)
def test_a_peak_is_the_top_cell_whose_neighbors_fall_below_half(
    neighbor: float, verdict: CellVerdict
) -> None:
    cells = _map(_axis(10, 20), {(0,): 1.0, (1,): neighbor})

    assert [cell.verdict for cell in cells] == [verdict, CellVerdict.RECOMMENDED]


def test_top_cells_with_the_same_score_are_judged_alike() -> None:
    # 양 끝 1.0 이 같은 모양이다 — 둘 다 이웃 0.1 이라 봉우리. 추천은 이웃 평균 0.55 동점의 앞 칸.
    cells = _map(_axis(10, 20, 30, 40, 50), {(0,): 1.0, (1,): 0.1, (2,): 0.1, (3,): 0.1, (4,): 1.0})

    assert [cell.verdict for cell in cells] == [
        CellVerdict.PEAK,
        CellVerdict.RECOMMENDED,
        CellVerdict.SCORED,
        CellVerdict.SCORED,
        CellVerdict.PEAK,
    ]


def test_a_top_cell_that_is_not_positive_is_not_a_peak() -> None:
    # 최고 칸 -0.1 의 이웃 평균 -2.0 은 제 점수의 절반보다 낮지만 점수가 0 이하라 봉우리가 아니다.
    cells = _map(_axis(10, 20), {(0,): -0.1, (1,): -2.0})

    assert [cell.verdict for cell in cells] == [CellVerdict.SCORED, CellVerdict.RECOMMENDED]


def test_no_cell_is_recommended_before_any_score() -> None:
    cells = _map(_axis(10, 20), {}, bankrupt={(0,)})

    assert [cell.verdict for cell in cells] == [CellVerdict.FAILED, CellVerdict.UNSCORED]


@pytest.mark.parametrize(
    ("cell", "sensitivity", "moved"),
    [
        # 10: 8 쪽 격자값이 없다(축 끝). 12 → 위쪽 가장 가까운 20(1.0) → (0.4-1.0)/0.4.
        ((0,), -1.5, (1,)),
        # 20: 16 → 아래쪽 10(0.4) → 0.6. 24 → 위쪽 가장 가까운 30(0.7) → 0.3.
        ((1,), 0.6, (0,)),
        # 30: 24 → 아래쪽 20(1.0), 36 → 위쪽 45(0.9) 모두 나아졌다. 덜 나아진 쪽 -0.2/0.7.
        ((2,), -0.2 / 0.7, (3,)),
        # 45: 36 → 아래쪽 가장 가까운 30(0.7) → 0.2/0.9. 54 쪽은 축 끝.
        ((3,), 0.2 / 0.9, (2,)),
    ],
)
def test_sensitivity_is_the_worst_drop_at_the_nearest_grid_value_on_each_side(
    cell: GridIndex, sensitivity: float, moved: GridIndex
) -> None:
    scores: dict[GridIndex, float] = {(0,): 0.4, (1,): 1.0, (2,): 0.7, (3,): 0.9}

    result = _by_cell(_map(_axis(10, 20, 30, 45), scores))[cell]

    assert (result.sensitivity, result.sensitivity_cell) == (pytest.approx(sensitivity), moved)


def test_a_coarse_grid_moves_to_the_next_grid_value_instead_of_staying() -> None:
    # 20 의 -20% 16 에 가장 가까운 격자값은 20 자신이고 그다음은 반대쪽 21 이다. 줄인 쪽의 다음
    # 격자값 5(0.2) 를 쓴다 — 더 멀리 바꾼 값이라 하락이 크게 잡히는 보수 쪽이다. 24 쪽은 21(0.9).
    scores: dict[GridIndex, float] = {(0,): 0.2, (1,): 1.0, (2,): 0.9}

    result = _by_cell(_map(_axis(5, 20, 21), scores))[(1,)]

    assert (result.sensitivity, result.sensitivity_cell) == (pytest.approx(0.8), (0,))


def test_equally_near_grid_values_resolve_to_the_one_nearer_the_cell() -> None:
    # 20 의 -20% 16 은 14·18 에서 같은 거리다. 제 칸에 가까운 18(0.9) 을 쓴다.
    cells = _by_cell(_map(_axis(14, 18, 20), {(0,): 0.2, (1,): 0.9, (2,): 1.0}))

    assert cells[(2,)].sensitivity == pytest.approx(0.1)


def test_a_bankrupt_moved_cell_drops_fully_and_other_moved_cells_are_skipped() -> None:
    # 20(1.0) 의 16 쪽은 10 이 파산해 0 → 하락 1.0, 24 쪽 30 은 아직 돌지 않았다.
    scores: dict[GridIndex, float] = {(1,): 1.0, (3,): 0.3}
    cells = _by_cell(_map(_axis(10, 20, 30, 40), scores, bankrupt={(0,)}))

    assert cells[(1,)].sensitivity == pytest.approx(1.0)
    # 40 의 32 쪽 30 은 점수가 없고 48 쪽은 축 끝이라 비교할 칸이 없다.
    assert (cells[(3,)].sensitivity, cells[(3,)].sensitivity_cell) == (None, None)
    # 10 이 비전략 실패면 빠져 20 은 비교할 칸이 없다.
    assert _by_cell(_map(_axis(10, 20, 30, 40), scores, failed={(0,)}))[(1,)].sensitivity is None


@pytest.mark.parametrize(
    ("search", "cell", "score"),
    [
        # 점수가 0 이하면 하락 비율의 뜻이 없다.
        (_axis(10, 20), (1,), 0.0),
        # 값 0 의 ±20% 는 0 이라 바꿀 값이 없다.
        (_axis(0, 5), (0,), 1.0),
        # 선택지·참거짓 축은 ±20% 의 뜻이 없다(참을 1 로 셈하지 않는다).
        (_axis("a", "b"), (1,), 1.0),
        (_axis(False, True), (1,), 1.0),
    ],
)
def test_sensitivity_is_empty_without_a_meaningful_move(
    search: SearchSpec, cell: GridIndex, score: float
) -> None:
    # 다른 칸 점수 0.5 — 옮겨 갈 칸이 있으면 민감도가 찼을 것이다.
    scores: dict[GridIndex, float] = {(0,): 0.5, (1,): 0.5} | {cell: score}

    assert _by_cell(_map(search, scores))[cell].sensitivity is None


def test_only_numeric_axes_move_in_a_mixed_grid() -> None:
    # (lookback 10·20 × mode a·b). (1,0) 의 mode 를 바꾸면 0.1 로 떨어지지만 숫자 축만 본다.
    search = SearchSpec((SearchAxis("lookback", (10, 20)), SearchAxis("mode", ("a", "b"))))
    scores: dict[GridIndex, float] = {(0, 0): 0.8, (0, 1): 0.1, (1, 0): 1.0, (1, 1): 0.1}

    assert _by_cell(_map(search, scores))[(1, 0)].sensitivity == pytest.approx(0.2)


def test_cell_outcomes_average_completed_windows_and_split_bankrupt_from_other_failures() -> None:
    completed, failed, queued = TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.QUEUED
    outcomes = cell_outcomes(
        [
            ((0,), completed, False, False, 0.2),
            ((0,), completed, False, False, 0.4),
            # 창 하나가 파산하면 파산 칸이다.
            ((1,), completed, False, False, 0.9),
            ((1,), failed, False, True, None),
            # 재시작으로 중단돼 복구를 기다리는 칸은 아직 결과가 없다.
            ((2,), failed, True, False, None),
            ((2,), completed, False, False, 0.5),
            # 창 하나가 대기·취소·점수 없음이면 칸 점수를 내지 않는다.
            ((3,), completed, False, False, 0.5),
            ((3,), queued, False, False, None),
            ((4,), completed, False, False, 0.5),
            ((4,), TrialStatus.CANCELLED, False, False, None),
            ((5,), completed, False, False, 0.5),
            ((5,), completed, False, False, None),
            # 비전략 실패만 있으면 실패 칸이고, 파산이 하나라도 있으면 파산 칸이다.
            ((6,), failed, False, False, None),
            ((6,), completed, False, False, 0.5),
            ((7,), failed, False, False, None),
            ((7,), failed, False, True, None),
        ]
    )

    assert outcomes.scores == pytest.approx({(0,): 0.3})
    assert (outcomes.bankrupt, outcomes.failed) == ({(1,), (7,)}, {(6,)})
