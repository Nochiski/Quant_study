"""채널·되돌림 기하와 채널 후보·되돌림 스윙·수평 지지·저항 구간(설계 문서 4.3~4.5).

피벗 검출과 떼어 규칙만 보려고 피벗·종가·ATR을 직접 넣은 계열로 손계산한다.
"""

from __future__ import annotations

import math

import pytest

from strategy_workbench.domain.chart_analysis.facade.geometry import (
    Anchor,
    ChannelGeometry,
    RetracementGeometry,
    TrendLine,
)
from strategy_workbench.domain.chart_analysis.facade.levels import (
    AnchorIssue,
    SwingChoice,
    TrendDirection,
    ZoneParams,
    ZoneSide,
    find_channels,
    find_zones,
    retracement_swing,
)
from strategy_workbench.domain.chart_analysis.facade.series import Pivot, PivotKind, Window

from ._chart_analysis_fixtures import series_with

H, L = PivotKind.HIGH, PivotKind.LOW


# --- 기하 -------------------------------------------------------------------------------------


def test_channel_geometry_levels_from_three_anchors_up_and_down() -> None:
    # 기준선 기울기 1, 봉 5의 기준선 값 105. 폭 기준점이 위(112)면 d = −7, 아래(98)면 d = 7
    up = ChannelGeometry(TrendLine(Anchor(0, 100), Anchor(10, 110)), Anchor(5, 112))
    down = ChannelGeometry(TrendLine(Anchor(0, 110), Anchor(10, 100)), Anchor(5, 98))

    assert (up.line.slope, up.line.at(5), up.depth) == (1.0, 105.0, -7.0)
    assert [up.price_at(5, 1.0), up.price_at(10, 0.5), up.price_at(0, 0.0)] == [112, 113.5, 100]
    assert (down.depth, down.price_at(5, 1.0), down.price_at(10, 0.5)) == (7.0, 98.0, 96.5)
    with pytest.raises(ValueError):
        TrendLine(Anchor(10, 110), Anchor(0, 100))


def test_retracement_levels_run_from_later_point_zero_to_earlier_point_one() -> None:
    swing = RetracementGeometry(Anchor(5, 120.0), Anchor(10, 100.0))

    assert [swing.price_at(r) for r in (0.0, 0.618, 1.0)] == pytest.approx([100, 112.36, 120])
    with pytest.raises(ValueError):
        RetracementGeometry(Anchor(10, 100.0), Anchor(5, 120.0))


# --- 채널 후보 --------------------------------------------------------------------------------

A, B, C = Pivot(L, 2, 100, 4), Pivot(L, 12, 105, 14), Pivot(L, 22, 110.1, 24)
# 봉 1 고점(115)은 기준선에서 가장 멀지만 모든 첫 점보다 앞이라 폭이 될 수 없다
HIGHS = (Pivot(H, 1, 115, 3), Pivot(H, 7, 106, 9), Pivot(H, 17, 112, 19))
# 종가는 기준선 AB + 2. 봉 3은 AC 채널만 위로 벗어나고(104.95 > 100.505 + 4.425), 구간 끝 봉
# 30(119)은 세 채널 모두 위로 벗어난다. 봉 31은 모든 채널 안이다
CLOSES = [100 + 0.5 * (t - 2) + 2 for t in range(32)]
CLOSES[3], CLOSES[30] = 104.95, 119.0


def _mirror(p: Pivot) -> Pivot:
    return Pivot(L if p.kind is H else H, p.index, 200 - p.price, p.confirmed_index)


def _pairs(found_candidates: tuple) -> list[tuple[int, int]]:
    return [(c.first.index, c.second.index) for c in found_candidates]


def test_up_channel_candidates_rank_by_outside_ratio_then_width_with_short_last() -> None:
    series = series_with(CLOSES, [1.0] * 32, (A, B, C, *HIGHS))

    found = find_channels(series, Window(0, 30), TrendDirection.UP)

    assert found.anchor_issue is None
    summary = [
        (c.first.index, c.second.index, c.width_pivot.index, c.outside_closes, c.span_bars)
        for c in found.candidates
    ]
    # AB(밖 1/29, 폭 4.5) → AC(밖 2/29, 폭 4.425) → BC(19봉 < 20이라 뒤)
    assert summary == [(2, 12, 17, 1, 29), (2, 22, 17, 2, 29), (12, 22, 17, 1, 19)]
    ab, ac, bc = found.candidates
    assert [c.width_atr for c in found.candidates] == pytest.approx([4.5, 4.425, 4.45])
    assert (ab.geometry.line.slope, ab.geometry.depth, ab.created_index) == (0.5, -4.5, 19)
    assert (ac.created_index, bc.created_index) == (24, 24)
    # C_r(t) = L(t) − r·d: 레벨 1은 폭 기준점(봉 17 고점 112)을 지난다
    assert ab.geometry.price_at(17, 1.0) == pytest.approx(112)


def test_twenty_bar_span_is_not_sent_to_the_back() -> None:
    series = series_with(CLOSES, [1.0] * 32, (A, B, C, *HIGHS))

    found = find_channels(series, Window(0, 31), TrendDirection.UP)

    # 끝 31에서 BC는 정확히 20봉: 밖 비율 AB 1/30 < BC 1/20 < AC 2/30 순서 그대로
    assert _pairs(found.candidates) == [(2, 12), (12, 22), (2, 22)]


def test_down_channel_is_the_mirror_of_up() -> None:
    series = series_with(
        [200 - c for c in CLOSES], [1.0] * 32, [_mirror(p) for p in (A, B, C, *HIGHS)]
    )

    found = find_channels(series, Window(0, 30), TrendDirection.DOWN)

    assert _pairs(found.candidates) == [(2, 12), (2, 22), (12, 22)]
    assert found.candidates[0].geometry.depth == pytest.approx(4.5)
    assert found.candidates[0].geometry.price_at(17, 1.0) == pytest.approx(88)


@pytest.mark.parametrize(("b_price", "kept"), [(100.9, False), (101.0, True)])
def test_channel_dropped_when_anchor_move_is_below_one_atr(b_price: float, kept: bool) -> None:
    series = series_with([101.0] * 32, [1.0] * 32, (A, Pivot(L, 12, b_price, 14), *HIGHS))

    found = find_channels(series, Window(0, 30), TrendDirection.UP)

    assert bool(found.candidates) is kept


@pytest.mark.parametrize(("d_price", "kept"), [(106.7, False), (106.75, True)])
def test_channel_dropped_when_later_same_side_pivot_breaks_line_by_over_quarter_atr(
    d_price: float, kept: bool
) -> None:
    # 기준선 AB의 봉 16 값은 107. 106.7은 0.3 아래(버림), 106.75는 정확히 0.25 아래(남김)
    series = series_with(CLOSES, [1.0] * 32, (A, B, Pivot(L, 16, d_price, 18), *HIGHS))

    found = find_channels(series, Window(0, 30), TrendDirection.UP)

    assert ((2, 12) in _pairs(found.candidates)) is kept


@pytest.mark.parametrize(
    ("window", "first", "issue", "pairs"),
    [
        (Window(0, 30), 12, None, [(12, 22)]),
        (Window(0, 30), 22, None, []),  # 뒤에 둘째 점이 없다
        (Window(0, 30), 7, AnchorIssue.NOT_A_PIVOT, []),  # 고점은 상승 채널의 첫 점이 아니다
        (Window(0, 23), 22, AnchorIssue.NOT_A_PIVOT, []),  # 봉 24에 확정 — 구간 끝에선 모른다
        (Window(10, 30), 2, AnchorIssue.OUTSIDE_WINDOW, []),
    ],
)
def test_first_anchor_fixes_a_or_explains_zero_candidates(
    window: Window, first: int, issue: AnchorIssue | None, pairs: list[tuple[int, int]]
) -> None:
    series = series_with(CLOSES, [1.0] * 32, (A, B, C, *HIGHS))

    found = find_channels(series, window, TrendDirection.UP, first_anchor=first)

    assert found.anchor_issue is issue
    assert _pairs(found.candidates) == pairs


# --- 되돌림 스윙 ------------------------------------------------------------------------------

SWING_PIVOTS = (
    Pivot(L, 2, 90, 4),
    Pivot(H, 5, 130, 7),
    Pivot(L, 8, 95, 10),
    Pivot(H, 11, 125, 13),
    Pivot(L, 13, 80, 16),  # 구간 끝 15 뒤에 확정 — 빠진다
)


@pytest.mark.parametrize(
    ("pivots", "choice", "expected"),
    [
        (SWING_PIVOTS, SwingChoice.RANGE_EXTREMES, (Anchor(2, 90), Anchor(5, 130))),
        (SWING_PIVOTS, SwingChoice.LATEST_CONFIRMED, (Anchor(8, 95), Anchor(11, 125))),
        (SWING_PIVOTS, SwingChoice.LATEST_MAJOR, (Anchor(2, 90), Anchor(5, 130))),
        # 최고 고점이 같은 값이면 먼저 나온 피벗
        (
            (*SWING_PIVOTS[:3], Pivot(H, 11, 130, 13)),
            SwingChoice.RANGE_EXTREMES,
            (Anchor(2, 90), Anchor(5, 130)),
        ),
    ],
)
def test_retracement_swing_choices_use_pivots_confirmed_by_window_end(
    pivots: tuple[Pivot, ...], choice: SwingChoice, expected: tuple[Anchor, Anchor]
) -> None:
    series = series_with([100.0] * 20, [1.0] * 20, pivots, major=SWING_PIVOTS[:2])

    swing = retracement_swing(series, Window(0, 15), choice)

    assert swing is not None and (swing.first, swing.second) == expected


def test_retracement_swing_is_none_without_two_confirmed_pivots() -> None:
    series = series_with([100.0] * 20, [1.0] * 20, SWING_PIVOTS, major=SWING_PIVOTS[:2])

    assert retracement_swing(series, Window(6, 15), SwingChoice.LATEST_MAJOR) is None
    assert retracement_swing(series, Window(9, 15), SwingChoice.RANGE_EXTREMES) is None


# --- 수평 구간 --------------------------------------------------------------------------------


def test_zones_group_by_complete_linkage_merge_near_touches_and_sort_top_down() -> None:
    pivots = (
        Pivot(H, 3, 100.0, 5),
        Pivot(L, 8, 100.4, 10),  # ln(100.4/100) = 0.0040 ≤ tol 0.00497
        Pivot(H, 12, 100.6, 14),  # 첫 점 100 대비 0.0060 > tol → 새 묶음(직전 점 대비로는 0.002)
        Pivot(L, 15, 100.9, 17),  # 봉 12와 3봉 떨어져 접촉 2회
        Pivot(H, 22, 103.0, 24),
        Pivot(L, 24, 103.1, 26),  # 봉 22와 2봉 안이라 접촉 1회 → 최소 접촉 미달
        Pivot(H, 21, 105.0, 23),
        Pivot(H, 27, 105.2, 29),
    )
    close = [100.7] * 31
    series = series_with(
        close, [1.0] * 31, pivots, high=[c + 0.05 for c in close], low=[c - 0.05 for c in close]
    )

    zones = find_zones(series, Window(0, 30))

    assert [(z.low, z.high, z.touches, z.formed_index, z.side) for z in zones] == [
        (105.0, 105.2, 2, 29, ZoneSide.RESISTANCE),
        (100.6, 100.9, 2, 17, ZoneSide.STRADDLE),
        (100.0, 100.4, 2, 10, ZoneSide.SUPPORT),
    ]


def test_zone_width_tolerance_is_inclusive() -> None:
    # 종가·ATR이 1이라 tol = k_z 그대로다. 폭이 정확히 tol이면 한 구간, 그보다 조금 좁은 tol이면
    # 둘로 갈라져 접촉 1회씩이라 버린다
    width = math.log(100.4 / 100.0)
    pivots = (Pivot(H, 3, 100.0, 5), Pivot(H, 9, 100.4, 11))
    series = series_with([1.0] * 15, [1.0] * 15, pivots)

    (zone,) = find_zones(series, Window(0, 14), ZoneParams(width_atr=width))
    narrower = ZoneParams(width_atr=math.nextafter(width, 0))

    assert (zone.low, zone.high, zone.formed_index) == (100.0, 100.4, 11)
    assert find_zones(series, Window(0, 14), narrower) == ()


@pytest.mark.parametrize(
    ("current", "side"),
    [
        (99.99, ZoneSide.RESISTANCE),
        (100.0, ZoneSide.STRADDLE),  # 현재가 = lo
        (100.4, ZoneSide.STRADDLE),  # 현재가 = hi
        (100.41, ZoneSide.SUPPORT),
    ],
)
def test_zone_side_boundaries_count_as_straddle(current: float, side: ZoneSide) -> None:
    close = [101.0] * 14 + [current]
    series = series_with(close, [1.0] * 15, (Pivot(L, 2, 100.0, 4), Pivot(L, 6, 100.4, 8)))

    (zone,) = find_zones(series, Window(0, 14))

    assert zone.side is side


# (고가, 저가, 종가). 구간 [100, 100.4]는 봉 12에 생기고, 그 뒤 지킴 1·뚫림 1
ZONE_BARS = [
    (101.2, 100.8, 101.0),
    (101.2, 100.8, 101.0),
    (101.0, 100.0, 100.5),  # 저점 피벗 100
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.0, 100.4, 100.8),  # 저점 피벗 100.4
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.0, 100.2, 100.9),  # 11: 피벗은 다 확정됐지만 생긴 날 전 접촉 — 세지 않는다
    # 12: 생긴 날(ATR 0.6 → 1.0, 폭 0.0040 ≤ 0.5·1/100.35). 이날 접촉은 안 센다
    (101.0, 100.3, 100.35),
    (101.0, 100.3, 100.9),  # 13~14: 닿기 전 종가(봉 12)가 구간 안이라 세지 않는다
    (100.8, 100.2, 100.6),
    (101.8, 101.2, 101.5),
    (101.8, 101.2, 101.5),
    (101.0, 99.5, 99.8),  # 17: 위에서 닿고
    (99.8, 98.8, 99.0),  # 18: 아래로 → 뚫림
    (99.3, 98.8, 99.0),
    (100.2, 99.0, 99.5),  # 20: 아래에서 닿고
    (99.3, 98.8, 99.0),  # 21: 아래로 돌아감 → 지킴
    (99.3, 98.8, 99.0),
    (100.1, 99.0, 99.5),  # 23~24: 끝까지 닿아 있어 세지 않는다
    (100.2, 99.2, 99.6),
]


def test_zone_formed_date_waits_for_that_days_width_and_counts_only_later_retouches() -> None:
    high, low, close = (list(col) for col in zip(*ZONE_BARS, strict=True))
    atr = [0.6] * 12 + [1.0] * 13
    series = series_with(
        close, atr, (Pivot(L, 2, 100.0, 4), Pivot(L, 6, 100.4, 8)), high=high, low=low
    )

    (zone,) = find_zones(series, Window(0, 24))

    assert (zone.formed_index, zone.held, zone.broken) == (12, 1, 1)
    assert zone.side is ZoneSide.RESISTANCE  # 현재가 99.6 < 100
