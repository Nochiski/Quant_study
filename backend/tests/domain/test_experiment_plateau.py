"""domain.experiment 고원·민감도(V4-03, 수학 노트 5절)를 손계산 정답으로 본다."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.experiment.facade.design import (
    PEAK_NEIGHBOR_RATIO,
    CellPlateau,
    CellVerdict,
    GridIndex,
    SearchAxis,
    SearchSpec,
    WindowSelectionRule,
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


def _by_cell(cells: tuple[CellPlateau, ...]) -> dict[GridIndex, CellPlateau]:
    return {cell.grid_index: cell for cell in cells}


def test_the_recommended_cell_has_the_best_neighbor_mean_and_a_lonely_top_is_a_peak() -> None:
    cells = plateau_map(_GRID, _SCORES, ())

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


def test_a_failed_cell_enters_the_neighbor_mean_at_the_lowest_score() -> None:
    # (0,2) 학습이 실패했다. 최하 점수는 점수가 난 칸의 최솟값 0.1 이다.
    scores = {cell: score for cell, score in _SCORES.items() if cell != (0, 2)}

    cells = _by_cell(plateau_map(_GRID, scores, {(0, 2)}))

    # 빼면 (1,2) 이웃 평균이 (0.9+1.0+0.1+2.0)/4 = 1.0 으로 추천이 된다. 최하 점수로 넣으면
    # (0.9+0.1+1.0+0.1+2.0)/5 = 0.82 라 (0,0) 이 그대로 추천이다.
    assert cells[(1, 2)].plateau_score == pytest.approx(0.82)
    assert cells[(0, 1)].plateau_score == pytest.approx(2.9 / 5)
    assert cells[(1, 1)].plateau_score == pytest.approx(5.1 / 8)
    assert cells[(0, 0)].verdict is CellVerdict.RECOMMENDED
    assert cells[(0, 2)] == CellPlateau((0, 2), CellVerdict.FAILED, None, None, None)


def test_window_selection_still_leaves_failed_cells_out() -> None:
    """창 선택(`pick_window_cell`)은 V4-03 에서 바꾸지 않는다 — 실패 칸을 빼고 이웃 평균을 낸다.

    그래서 같은 점수에서 창 선택과 지도 추천이 갈릴 수 있다(PR "남은 우려").
    """
    scores = {cell: score for cell, score in _SCORES.items() if cell != (0, 2)}
    rule = WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX

    assert pick_window_cell(scores, _GRID.shape, rule) == (1, 2)
    assert pick_window_cell(_SCORES, _GRID.shape, rule) == (0, 0)
    recommended = [
        cell.grid_index
        for cell in plateau_map(_GRID, scores, {(0, 2)})
        if cell.verdict is CellVerdict.RECOMMENDED
    ]
    assert recommended == [(0, 0)]


def test_unscored_cells_stay_out_of_the_neighbor_mean() -> None:
    # (2,) 는 아직 돌지 않았다. (3,) 은 점수 있는 이웃이 없어 자기 점수 0.9 로 순위를 매겨, 이웃
    # 평균 0.3·0.2 인 (0,)·(1,) 을 앞선다.
    cells = plateau_map(_axis(10, 20, 30, 40), {(0,): 0.2, (1,): 0.3, (3,): 0.9}, ())

    assert [(cell.verdict, cell.plateau_score) for cell in cells] == [
        (CellVerdict.SCORED, 0.3),
        (CellVerdict.SCORED, 0.2),
        (CellVerdict.UNSCORED, None),
        (CellVerdict.RECOMMENDED, None),
    ]


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
    cells = plateau_map(_axis(10, 20), {(0,): 1.0, (1,): neighbor}, ())

    assert [cell.verdict for cell in cells] == [verdict, CellVerdict.RECOMMENDED]


def test_a_top_cell_that_is_not_positive_is_not_a_peak() -> None:
    # 최고 칸 -0.1 의 이웃 평균 -2.0 은 제 점수의 절반보다 낮지만 점수가 0 이하라 봉우리가 아니다.
    cells = plateau_map(_axis(10, 20), {(0,): -0.1, (1,): -2.0}, ())

    assert [cell.verdict for cell in cells] == [CellVerdict.SCORED, CellVerdict.RECOMMENDED]


def test_no_cell_is_recommended_before_any_score() -> None:
    cells = plateau_map(_axis(10, 20), {}, {(0,)})

    assert [cell.verdict for cell in cells] == [CellVerdict.FAILED, CellVerdict.UNSCORED]


@pytest.mark.parametrize(
    ("cell", "sensitivity"),
    [
        # 10: 8 쪽 격자값이 없다(축 끝). 12 → 위쪽 가장 가까운 20(1.0) → (0.4-1.0)/0.4.
        ((0,), -1.5),
        # 20: 16 → 아래쪽 10(0.4) → 0.6. 24 → 위쪽 가장 가까운 30(0.7) → 0.3.
        ((1,), 0.6),
        # 30: 24 → 아래쪽 20(1.0), 36 → 위쪽 45(0.9) 모두 나아졌다. 덜 나아진 쪽 -0.2/0.7.
        ((2,), -0.2 / 0.7),
        # 45: 36 → 아래쪽 가장 가까운 30(0.7) → 0.2/0.9. 54 쪽은 축 끝.
        ((3,), 0.2 / 0.9),
    ],
)
def test_sensitivity_is_the_worst_drop_at_the_nearest_grid_value_on_each_side(
    cell: GridIndex, sensitivity: float
) -> None:
    scores: dict[GridIndex, float] = {(0,): 0.4, (1,): 1.0, (2,): 0.7, (3,): 0.9}

    assert _by_cell(plateau_map(_axis(10, 20, 30, 45), scores, ()))[
        cell
    ].sensitivity == pytest.approx(sensitivity)


def test_a_coarse_grid_moves_to_the_next_grid_value_instead_of_staying() -> None:
    # 20 의 -20% 16 에 가장 가까운 격자값은 20 자신이고 그다음은 반대쪽 21 이다. 줄인 쪽의 다음
    # 격자값 5(0.2) 를 쓴다 — 더 멀리 바꾼 값이라 하락이 크게 잡히는 보수 쪽이다. 24 쪽은 21(0.9).
    scores: dict[GridIndex, float] = {(0,): 0.2, (1,): 1.0, (2,): 0.9}

    assert _by_cell(plateau_map(_axis(5, 20, 21), scores, ()))[(1,)].sensitivity == pytest.approx(
        0.8
    )


def test_equally_near_grid_values_resolve_to_the_one_nearer_the_cell() -> None:
    # 20 의 -20% 16 은 14·18 에서 같은 거리다. 제 칸에 가까운 18(0.9) 을 쓴다.
    cells = _by_cell(plateau_map(_axis(14, 18, 20), {(0,): 0.2, (1,): 0.9, (2,): 1.0}, ()))

    assert cells[(2,)].sensitivity == pytest.approx(0.1)


def test_a_failed_moved_cell_drops_to_the_lowest_score_and_an_unscored_one_is_skipped() -> None:
    # 20(1.0) 의 16 쪽은 10 이 실패해 최하 점수 0.3, 24 쪽 30 은 아직 돌지 않았다.
    scores: dict[GridIndex, float] = {(1,): 1.0, (3,): 0.3}
    cells = _by_cell(plateau_map(_axis(10, 20, 30, 40), scores, {(0,)}))

    assert cells[(1,)].sensitivity == pytest.approx(0.7)
    # 40 의 32 쪽 30 은 점수가 없고 48 쪽은 축 끝이라 비교할 칸이 없다.
    assert cells[(3,)].sensitivity is None


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

    assert _by_cell(plateau_map(search, scores, ()))[cell].sensitivity is None


def test_only_numeric_axes_move_in_a_mixed_grid() -> None:
    # (lookback 10·20 × mode a·b). (1,0) 의 mode 를 바꾸면 0.1 로 떨어지지만 숫자 축만 본다.
    search = SearchSpec((SearchAxis("lookback", (10, 20)), SearchAxis("mode", ("a", "b"))))
    scores: dict[GridIndex, float] = {(0, 0): 0.8, (0, 1): 0.1, (1, 0): 1.0, (1, 1): 0.1}

    assert _by_cell(plateau_map(search, scores, ()))[(1, 0)].sensitivity == pytest.approx(0.2)


def test_cell_outcomes_average_completed_windows_and_mark_failed_cells() -> None:
    completed, failed, queued = TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.QUEUED
    scores, failed_cells = cell_outcomes(
        [
            ((0,), completed, False, 0.2),
            ((0,), completed, False, 0.4),
            # 창 하나가 실패(파산 포함)하면 칸이 실패다.
            ((1,), completed, False, 0.9),
            ((1,), failed, False, None),
            # 재시작으로 중단돼 복구를 기다리는 칸은 아직 결과가 없다.
            ((2,), failed, True, None),
            ((2,), completed, False, 0.5),
            # 창 하나가 대기·취소·점수 없음이면 칸 점수를 내지 않는다.
            ((3,), completed, False, 0.5),
            ((3,), queued, False, None),
            ((4,), completed, False, 0.5),
            ((4,), TrialStatus.CANCELLED, False, None),
            ((5,), completed, False, 0.5),
            ((5,), completed, False, None),
        ]
    )

    assert scores == pytest.approx({(0,): 0.3})
    assert failed_cells == {(1,)}
