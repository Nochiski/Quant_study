"""계열 좌표·ATR(Wilder)·확정 피벗·국면 판정과 룩어헤드 성질(설계 문서 4.1·4.2·4.7)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from strategy_workbench.domain.chart_analysis._regime import _verdict
from strategy_workbench.domain.chart_analysis._series import _find_pivots
from strategy_workbench.domain.chart_analysis.facade.bars import Candle
from strategy_workbench.domain.chart_analysis.facade.levels import (
    SwingChoice,
    TrendDirection,
    find_channels,
    find_zones,
    retracement_swing,
)
from strategy_workbench.domain.chart_analysis.facade.regime import (
    BOX_RATIO_MAX,
    RegimeReason,
    RegimeVerdict,
    Structure,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.series import (
    Pivot,
    PivotKind,
    SwingParams,
    Window,
    build_series,
)

from ._chart_analysis_fixtures import candles, candles_from_closes, series_with

H, L = PivotKind.HIGH, PivotKind.LOW


def _from_log_returns(signs: str, step: float, width: float) -> list[Candle]:
    closes = [100.0]
    for s in signs:
        closes.append(closes[-1] * math.exp(step if s == "+" else -step))
    return candles_from_closes(closes, width)


# --- 계열 좌표 ---------------------------------------------------------------------------------


def test_index_of_maps_dates_inside_a_completed_bar_period_and_bar_date_is_its_start() -> None:
    weekly = [
        Candle(date(2026, 11, 2), date(2026, 11, 6), 10, 11, 9, 10),
        Candle(date(2026, 11, 9), date(2026, 11, 13), 10, 11, 9, 10),
        Candle(date(2026, 11, 16), date(2026, 11, 17), 10, 11, 9, 10, in_progress=True),
    ]
    series = build_series(weekly)

    assert [series.index_of(date(2026, 11, d)) for d in (1, 2, 4, 7, 9, 13, 14, 16)] == [
        None,  # 첫 봉 전
        0,
        0,  # 주 중간
        0,  # 봉 사이 주말은 앞 봉
        1,
        1,
        None,  # 마지막 완성 봉의 끝 거래일 뒤
        None,  # 진행 중 봉
    ]
    assert series.bar_date(1) == date(2026, 11, 9)
    with pytest.raises(ValueError):
        series.bar_date(2)


# --- ATR -------------------------------------------------------------------------------------


def test_atr_uses_gaps_in_true_range_and_wilder_after_simple_head() -> None:
    rows = [
        (10, 11, 9, 10),  # TR = 2
        (10, 12, 10, 11),  # TR = max(2, |12−10|, |10−10|) = 2
        (14, 15, 13, 14),  # 갭: TR = max(2, |15−11|, |13−11|) = 4
        (14, 14, 8, 9),  # TR = max(6, 0, |8−14|) = 6
        (9, 10, 9, 9.5),  # TR = max(1, |10−9|, 0) = 1
    ]

    atr = build_series(candles(rows), SwingParams(atr_period=3)).atr

    # 처음 3봉은 단순 평균(2, 2, 8/3), 그 뒤는 (ATR·2 + TR)/3
    assert atr.tolist() == pytest.approx([2, 2, 8 / 3, 34 / 9, 77 / 27])


def test_build_series_drops_in_progress_candle_keeps_arrays_read_only_and_rejects_bad_prices() -> (
    None
):
    bars = candles_from_closes([100, 101, 102], width=2)
    bars[-1] = replace(bars[-1], in_progress=True)

    series = build_series(bars)

    assert len(series.candles) == 2
    with pytest.raises(ValueError):
        series.close[0] = 0.0
    with pytest.raises(ValueError, match="index=1"):
        build_series(candles([(10, 11, 9, 10), (10, 11, 10.5, 10)]))  # 저가 > 종가


# --- 피벗 ------------------------------------------------------------------------------------


def _pivots(highs: list[float], lows: list[float]) -> tuple[Pivot, ...]:
    # 문턱을 손으로 맞추려고 ATR을 1로 고정한다(build_series를 거치면 ATR이 봉마다 달라진다)
    return _find_pivots(highs, lows, [1.0] * len(highs), sensitivity=1.0, r=2)


def test_pivot_confirms_on_cumulative_opposite_extreme_only_after_r_bars() -> None:
    # 봉 2에서 고점 1의 누적 낙폭 1.5 ≥ 1이지만 t < i+R.
    # 봉 3 자체 낙폭(12−11.2)은 0.8이라 누적 최저로 확정된다.
    pivots = _pivots([10, 12, 11.5, 11.4], [9, 11, 10.5, 11.2])

    assert pivots == (Pivot(L, 0, 9, 2), Pivot(H, 1, 12, 3))


def test_opposite_tracking_restarts_from_extreme_inside_confirmed_span() -> None:
    # 고점 1이 봉 3에서 확정되면 저점 추적은 (1, 3]의 최저인 봉 2(10.5)에서 다시 시작한다
    pivots = _pivots([10, 12, 11.5, 11.4, 11.8], [9, 11, 10.5, 11.2, 11.3])

    assert pivots[-1] == Pivot(L, 2, 10.5, 4)


def test_new_extreme_replaces_candidate_and_restarts_r_count() -> None:
    highs = [10, 12, 11.5, 11.4, 11.6, 10.6, 10.9, 11.0]
    lows = [9, 11, 10.5, 11.2, 10.0, 9.8, 10.1, 10.4]

    pivots = _pivots(highs, lows)

    # 저점 후보 4 → 5로 바뀐다. 봉 6의 반등(10.9 − 9.8 = 1.1)은 t < 5+R라 봉 7에서 확정된다
    assert pivots[2:] == (Pivot(L, 5, 9.8, 7),)


def test_flat_series_has_no_pivots_and_is_degenerate() -> None:
    # ATR이 0이면 문턱도 0이라 막지 않으면 봉 2에서 "0 ≥ 0"으로 피벗이 생긴다
    series = build_series(candles([(100, 100, 100, 100)] * 25))

    assert (series.pivots, series.major_pivots) == ((), ())
    regime = judge_regime(series, Window(0, 24))
    assert (regime.verdict, regime.reason, regime.strength) == (
        RegimeVerdict.UNDECIDABLE,
        RegimeReason.DEGENERATE,
        None,
    )


# --- 국면 판정 -------------------------------------------------------------------------------

# 직선 ±2 걸음, ATR 2: 큰 스윙(2×ATR = 4) 피벗이 L0·H5·L7·H12·L14로 확정된다(손계산, 문서 4.1)
UP_CLOSES = [100, 102, 104, 106, 108, 110, 108, 106, 108, 110, 112, 114, 116, 114, 112]
UP_CLOSES += [114, 116, 118, 120, 122]
DOWN_CLOSES = [200 - c for c in UP_CLOSES]


def test_major_pivots_and_structure_up_and_down_recommend_channel_direction() -> None:
    up = build_series(candles_from_closes(UP_CLOSES, width=2))
    down = build_series(candles_from_closes(DOWN_CLOSES, width=2))

    assert [(p.kind, p.index, p.price, p.confirmed_index) for p in up.major_pivots] == [
        (L, 0, 98, 2),
        (H, 5, 110, 7),
        (L, 7, 106, 9),
        (H, 12, 116, 14),
        (L, 14, 112, 16),
    ]
    assert [(p.kind, p.index, p.price) for p in down.major_pivots] == [
        (H, 0, 100),
        (L, 5, 90),
        (H, 7, 94),
        (L, 12, 84),
        (H, 14, 88),
    ]
    up_regime = judge_regime(up, Window(0, 19))
    down_regime = judge_regime(down, Window(0, 19))
    assert (up_regime.verdict, up_regime.structure) == (RegimeVerdict.UP, Structure.UP)
    assert [p.index for p in up_regime.structure_highs] == [5, 12]
    assert [p.index for p in up_regime.structure_lows] == [7, 14]
    assert (down_regime.verdict, down_regime.structure) == (RegimeVerdict.DOWN, Structure.DOWN)
    assert (up_regime.channel_direction, down_regime.channel_direction) == (
        TrendDirection.UP,
        TrendDirection.DOWN,
    )


def _p(kind: PivotKind, index: int, price: float) -> Pivot:
    return Pivot(kind, index, price, index + 2)


@pytest.mark.parametrize(
    ("highs", "lows", "expected"),
    [
        ((10, 12), (8, 9), Structure.UP),
        ((12, 10), (9, 8), Structure.DOWN),
        ((10, 12), (9, 8), Structure.MIXED),
        ((10, 10), (8, 9), Structure.MIXED),  # 같은 고점은 HH도 LH도 아니다
        ((10,), (8, 9), Structure.INSUFFICIENT),
    ],
)
def test_structure_classification(
    highs: tuple[float, ...], lows: tuple[float, ...], expected: Structure
) -> None:
    major = [_p(H, 1 + 6 * i, v) for i, v in enumerate(highs)]
    major += [_p(L, 4 + 6 * i, v) for i, v in enumerate(lows)]
    series = series_with(
        [100.0, 101.0] * 10, [1.0] * 20, major=sorted(major, key=lambda p: p.index)
    )

    assert judge_regime(series, Window(0, 19)).structure is expected


def test_structure_includes_pivot_confirmed_exactly_on_window_end() -> None:
    # 고점 13은 봉 19에 확정된다: 끝 19에서는 구조에 들고, 끝 18에서는 아직 모른다
    major = [_p(H, 1, 10), _p(L, 4, 8), _p(H, 7, 11), _p(L, 10, 9), Pivot(H, 13, 12, 19)]
    series = series_with([100.0, 101.0] * 10, [1.0] * 20, major=major)

    assert [p.index for p in judge_regime(series, Window(0, 19)).structure_highs] == [7, 13]
    assert [p.index for p in judge_regime(series, Window(0, 18)).structure_highs] == [1, 7]


@pytest.mark.parametrize(
    ("strength", "structure", "expected"),
    [
        (math.nextafter(1.0, 0.0), Structure.UP, (RegimeVerdict.BOX, None)),
        (1.0, Structure.UP, (RegimeVerdict.HOLD, RegimeReason.WEAK_STRENGTH)),  # |S| = 1은 보류
        (-1.0, Structure.DOWN, (RegimeVerdict.HOLD, RegimeReason.WEAK_STRENGTH)),
        (math.nextafter(2.0, 0.0), Structure.UP, (RegimeVerdict.HOLD, RegimeReason.WEAK_STRENGTH)),
        (2.0, Structure.UP, (RegimeVerdict.UP, None)),  # |S| = 2는 추세
        (-2.0, Structure.DOWN, (RegimeVerdict.DOWN, None)),
        (2.0, Structure.DOWN, (RegimeVerdict.HOLD, RegimeReason.STRUCTURE_MISMATCH)),
    ],
)
def test_strength_thresholds_are_lower_inclusive(
    strength: float,
    structure: Structure,
    expected: tuple[RegimeVerdict, RegimeReason | None],
) -> None:
    # 실수 S를 정확히 1·2로 만드는 봉은 만들 수 없어 판정 식만 따로 본다
    assert _verdict(strength, 0.1, structure) == expected


# 끝 봉만 저가를 4 낮춰 TR 12 → ATR_끝 = (8·13 + 12)/14. 나머지 봉은 TR = ATR = 8
ATR_END = (8 * 13 + 12) / 14


@pytest.mark.parametrize(
    ("signs", "step", "verdict", "reason", "strength", "box"),
    [
        # 부호 개수만으로 S가 정해진다: +p·−q 이면 S = (p−q)/√m ÷ 표본표준편차/step
        # 고저폭: 최고 = 최고 종가, 최저 = 첫 봉 저가 92 또는 끝 봉 저가(직전 고가 − 12)
        ("+-" * 9 + "+", 0.01, RegimeVerdict.BOX, None, 1 / math.sqrt(20), 12.0),
        (
            "+" * 10 + "-" * 9,
            0.03,
            RegimeVerdict.UNCLEAR,
            None,
            1 / math.sqrt(20),
            100 * math.exp(0.3) - 92,
        ),
        (
            "+-+" * 6 + "+",
            0.01,
            RegimeVerdict.HOLD,
            RegimeReason.WEAK_STRENGTH,
            7 * math.sqrt(3 / 52),
            100 * math.exp(0.07) - 92,
        ),
    ],
)
def test_flat_and_weak_strength_branches_with_hand_computed_s_and_box_ratio(
    signs: str,
    step: float,
    verdict: RegimeVerdict,
    reason: RegimeReason | None,
    strength: float,
    box: float,
) -> None:
    bars = _from_log_returns(signs, step, width=8.0)
    bars[-1] = replace(bars[-1], low=bars[-1].low - 4)

    regime = judge_regime(build_series(bars), Window(0, 19))

    assert regime.strength == pytest.approx(strength)
    assert regime.box_ratio == pytest.approx(box / (ATR_END * math.sqrt(20)))
    assert (regime.verdict, regime.reason, regime.channel_direction) == (verdict, reason, None)


@pytest.mark.parametrize(
    ("top", "verdict"),
    [
        (5 * BOX_RATIO_MAX, RegimeVerdict.BOX),  # B = 5·B*/(1·√25) = B* 경계는 박스권
        (math.nextafter(5 * BOX_RATIO_MAX, math.inf), RegimeVerdict.UNCLEAR),
    ],
)
def test_box_ratio_threshold_is_inclusive(top: float, verdict: RegimeVerdict) -> None:
    close = [101.0, 102.0] * 12 + [101.0]  # S = 0
    high = [1.0] * 25
    high[5] = top
    series = series_with(close, [1.0] * 25, high=high, low=[0.0] * 25)

    assert judge_regime(series, Window(0, 24)).verdict is verdict


def test_zero_atr_at_window_end_is_degenerate() -> None:
    series = series_with([101.0, 102.0] * 10, [1.0] * 19 + [0.0])

    regime = judge_regime(series, Window(0, 19))

    assert (regime.verdict, regime.reason, regime.box_ratio) == (
        RegimeVerdict.UNDECIDABLE,
        RegimeReason.DEGENERATE,
        None,
    )


def test_strong_strength_without_confirmed_structure_is_hold() -> None:
    series = build_series(candles_from_closes([100 + 2 * i for i in range(20)], width=2))

    regime = judge_regime(series, Window(0, 19))

    assert regime.strength is not None and regime.strength >= 2
    assert regime.structure is Structure.INSUFFICIENT
    assert (regime.verdict, regime.reason) == (RegimeVerdict.HOLD, RegimeReason.STRUCTURE_MISMATCH)


def test_fewer_than_20_bars_is_undecidable_and_default_window_clamps_to_series_start() -> None:
    series = build_series(candles_from_closes([100 + 2 * i for i in range(20)], width=2))

    short = judge_regime(series, Window.ending_at(18, 120))
    exactly_20 = judge_regime(series, Window.ending_at(19, 20))

    assert short.window == Window(0, 18)
    assert (short.verdict, short.reason, short.strength, short.box_ratio) == (
        RegimeVerdict.UNDECIDABLE,
        RegimeReason.TOO_FEW_BARS,
        None,
        None,
    )
    assert exactly_20.verdict is not RegimeVerdict.UNDECIDABLE


def test_extreme_warning_uses_pivots_within_r_bars_confirmed_by_window_end() -> None:
    rising = build_series(candles_from_closes([100 + 2 * i for i in range(25)], width=2))  # L0만
    swings = build_series(candles_from_closes(UP_CLOSES, width=2))  # 후보 피벗 0·5·7·12·14

    assert judge_regime(rising, Window(2, 24)).starts_near_extreme is True  # |2 − 0| = R
    assert judge_regime(rising, Window(3, 24)).starts_near_extreme is False
    # 저점 14는 봉 16에 확정된다: 끝 15에서는 아직 모르는 극값이다
    assert judge_regime(swings, Window(0, 16)).ends_near_extreme is True
    assert judge_regime(swings, Window(0, 15)).ends_near_extreme is False


# --- 룩어헤드 ------------------------------------------------------------------------------


def _random_candles(seed: int, n: int) -> list[Candle]:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        spread = abs(rng.normal(0, 0.01, 2)) * c
        rows.append((o, max(o, c) + spread[0], min(o, c) - spread[1], c))
    return candles(rows)


@pytest.mark.parametrize("seed", [7, 11, 23])
def test_results_at_t_equal_results_from_input_cut_at_t(seed: int) -> None:
    bars = _random_candles(seed, n=160)
    full = build_series(bars)

    for t in range(30, 160, 4):
        cut = build_series(bars[: t + 1])
        assert cut.pivots == tuple(p for p in full.pivots if p.confirmed_index <= t)
        assert cut.major_pivots == tuple(p for p in full.major_pivots if p.confirmed_index <= t)
        window = Window(max(0, t - 120), t)
        assert find_zones(cut, window) == find_zones(full, window)
        assert judge_regime(cut, window) == judge_regime(full, window)
        for direction in TrendDirection:
            assert find_channels(cut, window, direction) == find_channels(full, window, direction)
        for choice in SwingChoice:
            assert retracement_swing(cut, window, choice) == retracement_swing(full, window, choice)
