"""기술적 지표 계산식(일봉 배열 → 같은 길이 배열).

정의는 사전등록 3.1 절과 같다. 값이 덜 쌓인 자리는 NaN(정의 안 됨)이다. 입력 앞쪽이 NaN 이면
첫 유효값부터 창을 쌓는다(MACD 시그널처럼 다른 지표를 다시 평균할 때).
"""

from dataclasses import dataclass

import numpy as np

from ._jit import jit

FloatArray = np.ndarray


@jit
def _rolling_mean(x: FloatArray, n: int) -> FloatArray:
    out = np.full(x.shape[0], np.nan)
    for i in range(n - 1, x.shape[0]):
        s = 0.0
        ok = True
        for j in range(i - n + 1, i + 1):
            v = x[j]
            if np.isnan(v):
                ok = False
                break
            s += v
        if ok:
            out[i] = s / n
    return out


@jit
def _rolling_std(x: FloatArray, n: int) -> FloatArray:
    out = np.full(x.shape[0], np.nan)
    for i in range(n - 1, x.shape[0]):
        w = x[i - n + 1 : i + 1]
        if np.isnan(w).any():
            continue
        m = w.mean()
        out[i] = np.sqrt(((w - m) ** 2).mean())
    return out


@jit
def _rolling_extreme(x: FloatArray, n: int, take_max: bool) -> FloatArray:
    out = np.full(x.shape[0], np.nan)
    for i in range(n - 1, x.shape[0]):
        w = x[i - n + 1 : i + 1]
        if np.isnan(w).any():
            continue
        out[i] = w.max() if take_max else w.min()
    return out


@jit
def _smooth(x: FloatArray, n: int, alpha: float) -> FloatArray:
    """첫 유효값부터 n 개 단순 평균으로 시작해 `전 + alpha·(값 − 전)` 으로 잇는다."""
    out = np.full(x.shape[0], np.nan)
    start = -1
    for i in range(x.shape[0]):
        if not np.isnan(x[i]):
            start = i
            break
    if start < 0 or start + n > x.shape[0]:
        return out
    seed = 0.0
    for j in range(start, start + n):
        if np.isnan(x[j]):
            return out
        seed += x[j]
    prev = seed / n
    out[start + n - 1] = prev
    for i in range(start + n, x.shape[0]):
        if np.isnan(x[i]):
            break
        prev = prev + alpha * (x[i] - prev)
        out[i] = prev
    return out


def sma(x: FloatArray, n: int) -> FloatArray:
    """단순 이동평균. 창 안에 NaN 이 있으면 NaN."""
    return _rolling_mean(np.asarray(x, dtype=float), n)


def ema(x: FloatArray, n: int) -> FloatArray:
    """지수 이동평균. α = 2/(n+1), 첫 값 = 처음 n 개 평균."""
    return _smooth(np.asarray(x, dtype=float), n, 2.0 / (n + 1))


def wilder(x: FloatArray, n: int) -> FloatArray:
    """Wilder 평활. α = 1/n, 첫 값 = 처음 n 개 평균."""
    return _smooth(np.asarray(x, dtype=float), n, 1.0 / n)


def rolling_max(x: FloatArray, n: int) -> FloatArray:
    """최근 n 개(당일 포함) 최댓값."""
    return _rolling_extreme(np.asarray(x, dtype=float), n, True)


def rolling_min(x: FloatArray, n: int) -> FloatArray:
    """최근 n 개(당일 포함) 최솟값."""
    return _rolling_extreme(np.asarray(x, dtype=float), n, False)


def true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    """TR = max(고−저, |고−전일종|, |저−전일종|). 첫 봉은 고−저."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    tr[0] = high[0] - low[0]
    return tr


def atr(high: FloatArray, low: FloatArray, close: FloatArray, n: int = 14) -> FloatArray:
    """평균 진폭(Wilder)."""
    return wilder(true_range(high, low, close), n)


def rsi(close: FloatArray, n: int = 14) -> FloatArray:
    """상대강도지수(Wilder). 손실 평균이 0 이면 100."""
    close = np.asarray(close, dtype=float)
    diff = np.diff(close)
    gain = wilder(np.where(diff > 0, diff, 0.0), n)
    loss = wilder(np.where(diff < 0, -diff, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        value = np.where(loss == 0, 100.0, 100.0 - 100.0 / (1.0 + gain / loss))
    value = np.where(np.isnan(gain) | np.isnan(loss), np.nan, value)
    return np.concatenate(([np.nan], value))


@dataclass(frozen=True, eq=False)
class Macd:
    line: FloatArray
    signal: FloatArray


def macd(close: FloatArray, fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    """MACD = EMA(fast) − EMA(slow), 시그널 = MACD 의 EMA(signal)."""
    line = ema(close, fast) - ema(close, slow)
    return Macd(line=line, signal=ema(line, signal))


@dataclass(frozen=True, eq=False)
class Ichimoku:
    conversion: FloatArray
    base: FloatArray
    span_a: FloatArray
    span_b: FloatArray
    # t 일의 구름 상단·하단 = t − displacement 일에 계산한 선행A·B 의 큰 값·작은 값
    cloud_top: FloatArray
    cloud_bottom: FloatArray


def _midpoint(high: FloatArray, low: FloatArray, n: int) -> FloatArray:
    return (rolling_max(high, n) + rolling_min(low, n)) / 2.0


def _shift_forward(x: FloatArray, k: int) -> FloatArray:
    out = np.full(x.shape[0], np.nan)
    if k < x.shape[0]:
        out[k:] = x[: x.shape[0] - k]
    return out


def ichimoku(
    high: FloatArray,
    low: FloatArray,
    conversion: int = 9,
    base: int = 26,
    span_b: int = 52,
    displacement: int = 26,
) -> Ichimoku:
    """일목균형표. 구름은 두 선행값이 모두 있을 때만 정의된다."""
    conv = _midpoint(high, low, conversion)
    base_line = _midpoint(high, low, base)
    a = (conv + base_line) / 2.0
    b = _midpoint(high, low, span_b)
    a_shift = _shift_forward(a, displacement)
    b_shift = _shift_forward(b, displacement)
    both = ~(np.isnan(a_shift) | np.isnan(b_shift))
    top = np.where(both, np.fmax(a_shift, b_shift), np.nan)
    bottom = np.where(both, np.fmin(a_shift, b_shift), np.nan)
    return Ichimoku(conversion=conv, base=base_line, span_a=a, span_b=b, cloud_top=top, cloud_bottom=bottom)


def obv(close: FloatArray, volume: FloatArray) -> FloatArray:
    """OBV. 첫 봉 0, 종가가 오르면 +거래량, 내리면 −거래량."""
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume, dtype=float)
    step = np.sign(np.diff(close)) * volume[1:]
    return np.concatenate(([0.0], np.cumsum(step)))


@dataclass(frozen=True, eq=False)
class Bollinger:
    middle: FloatArray
    upper: FloatArray
    lower: FloatArray


def bollinger(close: FloatArray, n: int = 20, k: float = 2.0) -> Bollinger:
    """볼린저 밴드. 표준편차는 모집단(ddof=0)."""
    close = np.asarray(close, dtype=float)
    mid = sma(close, n)
    sd = _rolling_std(close, n)
    return Bollinger(middle=mid, upper=mid + k * sd, lower=mid - k * sd)


@dataclass(frozen=True, eq=False)
class Stochastic:
    k: FloatArray  # 느린 %K
    d: FloatArray


def stochastic(
    high: FloatArray, low: FloatArray, close: FloatArray, k: int = 14, smooth_k: int = 3, d: int = 3
) -> Stochastic:
    """Slow 스토캐스틱. 최고−최저가 0 이면 그날 빠른 %K 는 정의 안 됨."""
    hh = rolling_max(high, k)
    ll = rolling_min(low, k)
    rng = hh - ll
    with np.errstate(divide="ignore", invalid="ignore"):
        fast = np.where(rng > 0, 100.0 * (np.asarray(close, dtype=float) - ll) / rng, np.nan)
    slow_k = sma(fast, smooth_k)
    return Stochastic(k=slow_k, d=sma(slow_k, d))
