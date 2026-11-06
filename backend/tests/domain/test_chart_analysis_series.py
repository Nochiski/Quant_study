"""ATR(Wilder)·확정 피벗·국면 판정과 룩어헤드 성질(설계 문서 4.1·4.2)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from strategy_workbench.domain.chart_analysis._regime import _structure
from strategy_workbench.domain.chart_analysis._series import _find_pivots
from strategy_workbench.domain.chart_analysis.facade.bars import Candle
from strategy_workbench.domain.chart_analysis.facade.levels import find_zones
from strategy_workbench.domain.chart_analysis.facade.regime import (
    BOX_RATIO_MAX,
    HoldReason,
    RegimeVerdict,
    Structure,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.swings import (
    Pivot,
    PivotKind,
    SwingParams,
    Window,
    build_series,
)

H, L = PivotKind.HIGH, PivotKind.LOW
D0 = date(2020, 1, 1)


def _candles(rows: Sequence[tuple[float, float, float, float]]) -> list[Candle]:
    """(시가, 고가, 저가, 종가) 행을 날짜만 하루씩 늘린 봉으로 만든다."""
    return [
        Candle(D0 + timedelta(days=i), D0 + timedelta(days=i), o, h, low, c)
        for i, (o, h, low, c) in enumerate(rows)
    ]


def _from_closes(closes: Sequence[float], width: float) -> list[Candle]:
    """고가 = max(직전 종가, 종가), 저가 = 고가 − width.

    직전 종가가 늘 봉 안이라 TR = ATR = width다.
    """
    rows = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        if abs(c - prev) > width:
            raise AssertionError(f"step larger than width at {i}")
        high = max(prev, c)
        rows.append((prev, high, high - width, c))
    return _candles(rows)


def _from_log_returns(signs: str, step: float, width: float = 2.0) -> list[Candle]:
    closes = [100.0]
    for s in signs:
        closes.append(closes[-1] * math.exp(step if s == "+" else -step))
    return _from_closes(closes, width)


# --- ATR -------------------------------------------------------------------------------------


def test_atr_uses_gaps_in_true_range_and_wilder_after_simple_head() -> None:
    candles = _candles(
        [
            (10, 11, 9, 10),  # TR = 2
            (10, 12, 10, 11),  # TR = max(2, |12−10|, |10−10|) = 2
            (14, 15, 13, 14),  # 갭: TR = max(2, |15−11|, |13−11|) = 4
            (14, 14, 8, 9),  # TR = max(6, 0, |8−14|) = 6
            (9, 10, 9, 9.5),  # TR = max(1, |10−9|, 0) = 1
        ]
    )

    atr = build_series(candles, SwingParams(atr_period=3)).atr

    # 처음 3봉은 단순 평균(2, 2, 8/3), 그 뒤는 (ATR·2 + TR)/3
    assert atr.tolist() == pytest.approx([2, 2, 8 / 3, 34 / 9, 77 / 27])


def test_build_series_drops_in_progress_candle_and_rejects_inconsistent_prices() -> None:
    candles = _from_closes([100, 101, 102], width=2)
    last = candles[-1]
    candles[-1] = Candle(last.start, last.end, last.open, last.high, last.low, last.close, True)

    assert len(build_series(candles).candles) == 2
    with pytest.raises(ValueError, match="index=1"):
        build_series(_candles([(10, 11, 9, 10), (10, 11, 10.5, 10)]))  # 저가 > 종가


# --- 피벗 ------------------------------------------------------------------------------------


def _pivots(highs: list[float], lows: list[float]) -> tuple[Pivot, ...]:
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


# --- 국면 판정 -------------------------------------------------------------------------------

# 직선 ±2 걸음, ATR 2: 큰 스윙(2×ATR = 4) 피벗이 L0·H5·L7·H12·L14로 확정된다(손계산, 문서 4.1)
UP_CLOSES = [100, 102, 104, 106, 108, 110, 108, 106, 108, 110, 112, 114, 116, 114, 112]
UP_CLOSES += [114, 116, 118, 120, 122]
DOWN_CLOSES = [200 - c for c in UP_CLOSES]


def _expected_strength(closes: Sequence[float]) -> float:
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    return math.log(closes[-1] / closes[0]) / (statistics.stdev(returns) * math.sqrt(len(returns)))


def test_major_pivots_and_structure_up_and_down() -> None:
    up = build_series(_from_closes(UP_CLOSES, width=2))
    down = build_series(_from_closes(DOWN_CLOSES, width=2))

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
    assert up_regime.strength == pytest.approx(_expected_strength(UP_CLOSES))
    assert down_regime.strength == pytest.approx(_expected_strength(DOWN_CLOSES))


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
    hs = tuple(_p(H, 10 * i, v) for i, v in enumerate(highs))
    ls = tuple(_p(L, 10 * i + 5, v) for i, v in enumerate(lows))

    assert _structure(hs, ls) is expected


@pytest.mark.parametrize(
    ("signs", "step", "verdict", "hold", "strength"),
    [
        # 부호 개수만으로 S가 정해진다: +p·−q 이면 S = (p−q)/√m ÷ 표본표준편차/step
        ("+-" * 9 + "+", 0.01, RegimeVerdict.BOX, None, 1 / math.sqrt(20)),
        ("+" * 10 + "-" * 9, 0.03, RegimeVerdict.UNCLEAR, None, 1 / math.sqrt(20)),
        (
            "+-+" * 6 + "+",
            0.01,
            RegimeVerdict.HOLD,
            HoldReason.WEAK_STRENGTH,
            7 * math.sqrt(3 / 52),
        ),
    ],
)
def test_flat_and_weak_strength_branches_with_hand_computed_s(
    signs: str, step: float, verdict: RegimeVerdict, hold: HoldReason | None, strength: float
) -> None:
    candles = _from_log_returns(signs, step, width=8.0)
    candles[-1] = replace(candles[-1], low=candles[-1].low - 4)  # 끝 ATR을 시작 ATR과 다르게
    series = build_series(candles)

    regime = judge_regime(series, Window(0, 19))

    assert regime.strength == pytest.approx(strength)
    assert (regime.verdict, regime.hold_reason) == (verdict, hold)
    expected_box = (series.high.max() - series.low.min()) / (series.atr[19] * math.sqrt(20))
    assert regime.box_ratio == pytest.approx(expected_box)
    if abs(strength) < 1:
        assert bool(expected_box <= BOX_RATIO_MAX) is (verdict is RegimeVerdict.BOX)


def test_strong_strength_without_confirmed_structure_is_hold() -> None:
    series = build_series(_from_closes([100 + 2 * i for i in range(20)], width=2))

    regime = judge_regime(series, Window(0, 19))

    assert regime.strength is not None and regime.strength >= 2
    assert regime.structure is Structure.INSUFFICIENT
    assert (regime.verdict, regime.hold_reason) == (
        RegimeVerdict.HOLD,
        HoldReason.STRUCTURE_MISMATCH,
    )


def test_fewer_than_20_bars_is_undecidable_and_default_window_clamps_to_series_start() -> None:
    series = build_series(_from_closes([100 + 2 * i for i in range(20)], width=2))

    short = judge_regime(series, Window.ending_at(18, 120))
    exactly_20 = judge_regime(series, Window.ending_at(19, 20))

    assert short.window == Window(0, 18)
    assert (short.verdict, short.strength, short.box_ratio) == (
        RegimeVerdict.UNDECIDABLE,
        None,
        None,
    )
    assert exactly_20.verdict is not RegimeVerdict.UNDECIDABLE


def test_extreme_warning_uses_pivots_within_r_bars_confirmed_by_window_end() -> None:
    rising = build_series(_from_closes([100 + 2 * i for i in range(25)], width=2))  # 피벗 L0 하나
    swings = build_series(_from_closes(UP_CLOSES, width=2))  # 후보 피벗 0·5·7·12·14

    assert judge_regime(rising, Window(2, 24)).starts_near_extreme is True  # |2 − 0| = R
    assert judge_regime(rising, Window(3, 24)).starts_near_extreme is False
    # 저점 14는 봉 16에서 확정된다: 끝 15에서는 아직 모르는 극값이다
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
    return _candles(rows)


def test_pivots_and_zones_at_t_equal_results_from_input_cut_at_t() -> None:
    candles = _random_candles(seed=7, n=160)
    full = build_series(candles)

    for t in range(30, 160, 3):
        cut = build_series(candles[: t + 1])
        assert cut.pivots == tuple(p for p in full.pivots if p.confirmed_index <= t)
        assert cut.major_pivots == tuple(p for p in full.major_pivots if p.confirmed_index <= t)
        window = Window(max(0, t - 120), t)
        assert find_zones(cut, window) == find_zones(full, window)
        assert judge_regime(cut, window) == judge_regime(full, window)
