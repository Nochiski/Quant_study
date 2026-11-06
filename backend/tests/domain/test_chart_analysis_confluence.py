"""중첩(컨플루언스): 선 → 가격 구간 조립과 2종 이상이 덮는 조각 잇기(문서 4.6)."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.chart_analysis.facade.confluence import (
    PriceBand,
    bands_of,
    find_confluences,
)
from strategy_workbench.domain.chart_analysis.facade.geometry import (
    Anchor,
    ChannelGeometry,
    RetracementGeometry,
    TrendLine,
)
from strategy_workbench.domain.chart_analysis.facade.levels import HorizontalZone, ZoneSide
from strategy_workbench.domain.chart_analysis.facade.line_record import LineKind

from ._chart_analysis_fixtures import series_with

HL, RT, CH = LineKind.HLINE, LineKind.FIB_RETRACEMENT, LineKind.FIB_CHANNEL
TOL = 0.001  # 손계산을 쉽게 하려고 작게(넓힌 경계 = 값·(1 ∓ TOL))


def _band(kind: LineKind, low: float, high: float | None = None) -> PriceBand:
    return PriceBand(kind, low, low if high is None else high, ref=f"{kind.value}@{low}")


def _summary(
    bands: list[PriceBand], tolerance: float = TOL
) -> list[tuple[float, float, set[LineKind], list[str]]]:
    return [
        (c.low, c.high, set(c.kinds), [m.ref for m in c.members])
        for c in find_confluences(bands, tolerance)
    ]


# --- 선 → 가격 구간 ----------------------------------------------------------------------------

SERIES = series_with([100.0] * 21, [1.0] * 21)  # 기준 봉 = 마지막 완성 봉 20


def test_channel_bands_use_last_completed_bar_and_drop_non_positive_levels() -> None:
    # 가파른 하락 채널: 기준선 기울기 −5라 봉 20의 기준선 값 10.
    # 폭 기준점(봉 5, 65)으로 d = 85 − 65 = 20
    channel = ChannelGeometry(TrendLine(Anchor(0, 110), Anchor(10, 60)), Anchor(5, 65))

    bands = bands_of(SERIES, channel, ref="c")

    # 레벨 0.5부터는 10 − r·20 ≤ 0이라 빠진다
    assert [(b.kind, b.level, b.low) for b in bands] == [
        (CH, 0.0, pytest.approx(10.0)),
        (CH, 0.236, pytest.approx(5.28)),
        (CH, 0.382, pytest.approx(2.36)),
    ]


def test_retracement_bands_leave_out_anchor_levels_zero_and_one() -> None:
    swing = RetracementGeometry(Anchor(2, 120.0), Anchor(8, 100.0))

    bands = bands_of(SERIES, swing, ref="r", levels=(0.0, 0.5, 1.0, 1.618))

    assert [(b.kind, b.level, b.low) for b in bands] == [
        (RT, 0.5, 110.0),
        (RT, 1.618, pytest.approx(132.36)),
    ]


def test_horizontal_line_and_zone_bands() -> None:
    zone = HorizontalZone((), 99.0, 101.0, 2, 5, 0, 0, ZoneSide.STRADDLE)

    assert bands_of(SERIES, 100.0, ref="h") == (PriceBand(HL, 100.0, 100.0, "h"),)
    assert bands_of(SERIES, zone, ref="z") == (PriceBand(HL, 99.0, 101.0, "z"),)
    # 저장된 띠(기준점 둘)는 순서와 무관하게 [낮은 값, 높은 값]이다
    assert bands_of(SERIES, (101.0, 99.0), ref="b") == (PriceBand(HL, 99.0, 101.0, "b"),)


# --- 중첩 찾기 --------------------------------------------------------------------------------


def test_one_band_of_a_kind_spanning_two_others_yields_both_overlaps() -> None:
    # 리뷰 반례: 탐욕 묶기는 [101,102]에서 교집합을 좁힌 뒤 [103,106]을 놓쳤다
    bands = [_band(HL, 100, 102), _band(RT, 101, 105), _band(HL, 103, 106)]

    assert _summary(bands) == [
        (
            pytest.approx(101 * (1 - TOL)),
            pytest.approx(102 * (1 + TOL)),
            {HL, RT},
            ["hline@100", "fib_retracement@101"],
        ),
        (
            pytest.approx(103 * (1 - TOL)),
            pytest.approx(105 * (1 + TOL)),
            {HL, RT},
            ["fib_retracement@101", "hline@103"],
        ),
    ]


def test_wide_zone_overlapping_two_distant_lines_yields_two_confluences() -> None:
    bands = [_band(HL, 100, 110), _band(RT, 101), _band(CH, 109)]

    found = _summary(bands, tolerance=0.005)

    assert [(lo, hi, kinds) for lo, hi, kinds, _ in found] == [
        (pytest.approx(100.495), pytest.approx(101.505), {HL, RT}),
        (pytest.approx(108.455), pytest.approx(109.545), {HL, CH}),
    ]


def test_touching_covered_pieces_merge_into_one_confluence() -> None:
    bands = [_band(HL, 100, 104), _band(RT, 102, 106), _band(CH, 103, 105)]

    assert _summary(bands) == [
        (
            pytest.approx(102 * (1 - TOL)),
            pytest.approx(105 * (1 + TOL)),
            {HL, RT, CH},
            ["hline@100", "fib_retracement@102", "fib_channel@103"],
        )
    ]


def test_members_need_positive_length_overlap_with_the_confluence() -> None:
    # 폭 50%: 중첩 [50.5, 153](= 102·1.5)에 채널 [306·0.5, …] = [153, …]이 153 한 점에서만 닿는다
    bands = [_band(HL, 100, 102), _band(RT, 101, 102), _band(CH, 306, 400)]

    assert _summary(bands, tolerance=0.5) == [
        (50.5, 153.0, {HL, RT}, ["hline@100", "fib_retracement@101"])
    ]


def test_same_kind_overlap_is_not_confluence() -> None:
    assert find_confluences([_band(HL, 100, 102), _band(HL, 101, 103)]) == ()


@pytest.mark.parametrize(("tolerance", "found"), [(0.005, True), (0.004, False)])
def test_lines_widen_by_relative_tolerance(tolerance: float, found: bool) -> None:
    # 100·(1+0.005) = 100.5 ≥ 101·(1−0.005) = 100.495, 0.4%면 100.4 < 100.596
    bands = [_band(HL, 100), _band(CH, 101)]

    assert bool(find_confluences(bands, tolerance)) is found


def test_zero_tolerance_is_rejected() -> None:
    # 0이면 같은 가격의 두 레벨 선도 길이 0으로만 겹쳐 조용히 빠진다
    with pytest.raises(ValueError):
        find_confluences([_band(HL, 100), _band(CH, 100)], tolerance=0.0)
