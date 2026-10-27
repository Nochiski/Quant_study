"""지표 계산식 — 손으로 계산한 작은 예제와 대조한다."""

import math
from collections.abc import Callable

import numpy as np
import pytest

from technical_analysis import indicators as ind

nan = math.nan


def close_to(actual: np.ndarray, expected: list[float]) -> None:
    np.testing.assert_allclose(actual, np.array(expected, dtype=float), rtol=0, atol=1e-9, equal_nan=True)


def test_sma_is_undefined_until_window_fills() -> None:
    close_to(ind.sma(np.array([1.0, 2, 3, 4, 5]), 3), [nan, nan, 2, 3, 4])


def test_ema_seeds_with_sma_then_smooths() -> None:
    # α = 2/(3+1) = 0.5, 첫 값 = (2+4+6)/3 = 4
    close_to(ind.ema(np.array([2.0, 4, 6, 8, 10, 6]), 3), [nan, nan, 4, 6, 8, 7])


def test_ema_skips_leading_nan_input() -> None:
    # MACD 시그널처럼 앞이 비어 있는 입력은 첫 유효값부터 n 개로 시작한다
    close_to(ind.ema(np.array([nan, nan, 2.0, 4, 6, 8]), 3), [nan, nan, nan, nan, 4, 6])


def test_true_range_first_bar_is_high_minus_low() -> None:
    high = np.array([10.0, 11, 12, 11])
    low = np.array([9.0, 10, 10, 9])
    close = np.array([9.5, 10.5, 11, 10])
    close_to(ind.true_range(high, low, close), [1, 1.5, 2, 2])


def test_atr_uses_wilder_smoothing() -> None:
    high = np.array([10.0, 11, 12, 11])
    low = np.array([9.0, 10, 10, 9])
    close = np.array([9.5, 10.5, 11, 10])
    # TR = 1, 1.5, 2, 2 → 첫 값 (1+1.5)/2, 이후 (전·1 + TR)/2
    close_to(ind.atr(high, low, close, 2), [nan, 1.25, 1.625, 1.8125])


def test_rsi_uses_wilder_averages() -> None:
    close = np.array([10.0, 11, 10.5, 12, 11])
    # 변화 +1, −0.5, +1.5, −1 → 평균 이익·손실 (0.5, 0.25) → (1.0, 0.125) → (0.5, 0.5625)
    close_to(ind.rsi(close, 2), [nan, nan, 100 - 100 / 3, 100 - 100 / 9, 100 - 100 / (1 + 0.5 / 0.5625)])


def test_rsi_is_100_when_no_losses() -> None:
    assert ind.rsi(np.array([1.0, 2, 3, 4]), 2)[-1] == 100.0


def test_macd_is_fast_minus_slow_ema_and_signal_is_ema_of_macd() -> None:
    close = np.array([10.0, 12, 11, 13, 15, 14, 16, 18])
    m = ind.macd(close, fast=2, slow=3, signal=2)
    expected_line = ind.ema(close, 2) - ind.ema(close, 3)
    close_to(m.line, list(expected_line))
    close_to(m.signal, list(ind.ema(expected_line, 2)))


def test_ichimoku_cloud_at_t_uses_spans_computed_displacement_bars_earlier() -> None:
    high = np.array([10.0, 12, 11, 13, 15, 14, 16])
    low = np.array([9.0, 10, 9, 11, 12, 12, 13])
    ich = ind.ichimoku(high, low, conversion=2, base=3, span_b=4, displacement=2)
    # 전환(2) idx1 = (12+9)/2 = 10.5, idx4 = (15+11)/2 = 13 … 손 계산
    close_to(ich.conversion, [nan, 10.5, 10.5, 11, 13, 13.5, 14])
    close_to(ich.base, [nan, nan, 10.5, 11, 12, 13, 14])
    close_to(ich.span_a, [nan, nan, 10.5, 11, 12.5, 13.25, 14])
    close_to(ich.span_b, [nan, nan, nan, 11, 12, 12, 13.5])
    # t 일 구름 = t−2 일 선행값, 두 선행값이 모두 있어야 정의된다
    close_to(ich.cloud_top, [nan, nan, nan, nan, nan, 11, 12.5])


def test_obv_accumulates_signed_volume_from_zero() -> None:
    close = np.array([10.0, 11, 11, 9])
    volume = np.array([100.0, 200, 300, 400])
    close_to(ind.obv(close, volume), [0, 200, 200, -200])


def test_bollinger_uses_population_std() -> None:
    bb = ind.bollinger(np.array([1.0, 2, 3, 4]), 3, 2.0)
    sd = math.sqrt(2 / 3)
    close_to(bb.middle, [nan, nan, 2, 3])
    close_to(bb.upper, [nan, nan, 2 + 2 * sd, 3 + 2 * sd])
    close_to(bb.lower, [nan, nan, 2 - 2 * sd, 3 - 2 * sd])


def test_slow_stochastic_smooths_fast_k_twice() -> None:
    high = np.array([10.0, 11, 12, 11, 13])
    low = np.array([8.0, 9, 10, 9, 10])
    close = np.array([9.0, 10, 11, 10, 12])
    st = ind.stochastic(high, low, close, k=2, smooth_k=2, d=2)
    # 빠른 %K(2) = [nan, 2/3, 2/3, 1/3, 3/4]·100
    close_to(st.k, [nan, nan, 200 / 3, 50, 650 / 12])
    close_to(st.d, [nan, nan, nan, 350 / 6, (50 + 650 / 12) / 2])


def test_rolling_max_and_min() -> None:
    x = np.array([3.0, 1, 4, 1, 5])
    close_to(ind.rolling_max(x, 2), [nan, 3, 4, 4, 5])
    close_to(ind.rolling_min(x, 2), [nan, 1, 1, 1, 1])


def test_slow_stochastic_is_undefined_when_range_is_zero() -> None:
    flat = np.array([5.0, 5, 5, 5])
    st = ind.stochastic(flat, flat, flat, k=2, smooth_k=1, d=1)
    assert np.isnan(st.k).all()


@pytest.mark.parametrize("fn", [ind.sma, ind.ema])
def test_window_longer_than_series_gives_all_nan(fn: Callable[[np.ndarray, int], np.ndarray]) -> None:
    assert np.isnan(fn(np.array([1.0, 2]), 5)).all()
