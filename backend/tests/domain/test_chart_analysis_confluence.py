"""중첩(컨플루언스): 종류별 합집합 → 경계점 조각 → 2종 이상이 덮는 조각을 잇는다(문서 4.6)."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.chart_analysis.facade.confluence import (
    LineKind,
    PriceBand,
    find_confluences,
)

HL, RT, CH = LineKind.HLINE, LineKind.FIB_RETRACEMENT, LineKind.FIB_CHANNEL


def _band(
    kind: LineKind, low: float, high: float | None = None, level: float | None = None
) -> PriceBand:
    return PriceBand(
        kind, low, low if high is None else high, ref=f"{kind.value}@{low}", level=level
    )


def _summary(
    bands: list[PriceBand], tolerance: float
) -> list[tuple[float, float, set[LineKind], list[str]]]:
    return [
        (c.low, c.high, set(c.kinds), [m.ref for m in c.members])
        for c in find_confluences(bands, tolerance)
    ]


def test_one_band_of_a_kind_spanning_two_others_yields_both_overlaps() -> None:
    # 리뷰 반례: 탐욕 묶기는 [101,102]에서 교집합을 좁힌 뒤 [103,106]을 놓쳤다
    bands = [_band(HL, 100, 102), _band(RT, 101, 105), _band(HL, 103, 106)]

    assert _summary(bands, tolerance=0.0) == [
        (101, 102, {HL, RT}, ["hline@100", "fib_retracement@101"]),
        (103, 105, {HL, RT}, ["fib_retracement@101", "hline@103"]),
    ]


def test_wide_zone_overlapping_two_distant_lines_yields_two_confluences() -> None:
    bands = [_band(HL, 100, 110), _band(RT, 101, level=0.5), _band(CH, 109, level=1.0)]

    found = _summary(bands, tolerance=0.005)

    assert [(lo, hi, kinds) for lo, hi, kinds, _ in found] == [
        (pytest.approx(100.495), pytest.approx(101.505), {HL, RT}),
        (pytest.approx(108.455), pytest.approx(109.545), {HL, CH}),
    ]


def test_touching_covered_pieces_merge_into_one_confluence() -> None:
    bands = [_band(HL, 100, 104), _band(RT, 102, 106), _band(CH, 103, 105)]

    assert _summary(bands, tolerance=0.0) == [
        (102, 105, {HL, RT, CH}, ["hline@100", "fib_retracement@102", "fib_channel@103"])
    ]


def test_same_kind_overlap_is_not_confluence() -> None:
    assert find_confluences([_band(HL, 100, 102), _band(HL, 101, 103)]) == ()


def test_retracement_levels_zero_and_one_are_left_out() -> None:
    hline = _band(HL, 100)

    assert find_confluences([hline, _band(RT, 100, level=0.0), _band(RT, 100.2, level=1.0)]) == ()
    assert len(find_confluences([hline, _band(RT, 100, level=0.5)])) == 1


@pytest.mark.parametrize(("tolerance", "found"), [(0.005, True), (0.004, False)])
def test_lines_widen_by_relative_tolerance(tolerance: float, found: bool) -> None:
    # 100·(1+0.005) = 100.5 ≥ 101·(1−0.005) = 100.495, 0.4%면 100.4 < 100.596
    bands = [_band(HL, 100), _band(CH, 101, level=0.5)]

    assert bool(find_confluences(bands, tolerance)) is found
