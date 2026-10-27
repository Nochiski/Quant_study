"""매물대(일봉 근사, 사전등록 3.1 절).

분봉이 없어 하루 거래량을 그날 저가~고가 사이에 고르게 퍼뜨린다. 창의 최저 저가~최고 고가를 같은 폭
칸으로 나누고, 하루 거래량을 그날 범위와 겹치는 길이 비율로 칸에 나눈다. 고가 = 저가인 날은 그 가격이
든 칸에 전부 넣는다(칸 경계면 위 칸, 맨 위 경계면 마지막 칸).
"""

from dataclasses import dataclass

import numpy as np

from ._jit import jit


@dataclass(frozen=True, eq=False)
class Profile:
    edges: np.ndarray  # 칸 경계(bins + 1)
    volume: np.ndarray  # 칸별 거래량

    @property
    def centers(self) -> np.ndarray:
        return (self.edges[:-1] + self.edges[1:]) / 2.0

    @property
    def poc(self) -> float:
        """거래량이 가장 많은 칸의 가운데 가격(동률이면 낮은 칸)."""
        return float(self.centers[int(np.argmax(self.volume))])

    def share_above(self, price: float) -> float:
        """칸 가운데 가격이 price 보다 높은 칸의 거래량 비중."""
        total = float(self.volume.sum())
        if total <= 0:
            return float("nan")
        return float(self.volume[self.centers > price].sum()) / total


@jit
def _accumulate(high: np.ndarray, low: np.ndarray, vol: np.ndarray, lo: float, hi: float, bins: int) -> np.ndarray:
    out = np.zeros(bins)
    width = (hi - lo) / bins
    if width <= 0:
        out[0] = np.nansum(vol)
        return out
    for i in range(high.shape[0]):
        h = high[i]
        l = low[i]  # noqa: E741  # reason: 저가(low) 한 글자 이름이 식과 맞다
        v = vol[i]
        if np.isnan(h) or np.isnan(l) or np.isnan(v) or v <= 0:
            continue
        if h <= l:
            b = int((l - lo) / width)
            if b >= bins:
                b = bins - 1
            if b < 0:
                b = 0
            out[b] += v
            continue
        b0 = int((l - lo) / width)
        b1 = int((h - lo) / width)
        if b1 >= bins:
            b1 = bins - 1
        for b in range(max(b0, 0), b1 + 1):
            e0 = lo + b * width
            e1 = e0 + width
            overlap = min(h, e1) - max(l, e0)
            if overlap > 0:
                out[b] += v * overlap / (h - l)
    return out


def profile(high: np.ndarray, low: np.ndarray, volume: np.ndarray, bins: int = 50) -> Profile:
    """창 전체의 매물대."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    volume = np.asarray(volume, dtype=float)
    lo = float(np.nanmin(low))
    hi = float(np.nanmax(high))
    return Profile(edges=np.linspace(lo, hi, bins + 1), volume=_accumulate(high, low, volume, lo, hi, bins))


def rolling_poc(
    high: np.ndarray, low: np.ndarray, volume: np.ndarray, window: int = 120, bins: int = 50, include_today: bool = False
) -> np.ndarray:
    """날마다의 POC. include_today=False 면 t 일 값은 t−window ~ t−1 창(당일 제외)."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    volume = np.asarray(volume, dtype=float)
    n = high.shape[0]
    out = np.full(n, np.nan)
    shift = 0 if include_today else 1
    for t in range(window - 1 + shift, n):
        s = slice(t - shift - window + 1, t - shift + 1)
        if np.isnan(high[s]).any() or np.isnan(low[s]).any():
            continue
        out[t] = profile(high[s], low[s], volume[s], bins).poc
    return out


def rolling_share_above(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, window: int = 120, bins: int = 50
) -> np.ndarray:
    """t 일 종가보다 비싼 칸의 거래량 비중(t−window+1 ~ t 창, 당일 포함 — 점검표 D10)."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = high.shape[0]
    out = np.full(n, np.nan)
    for t in range(window - 1, n):
        s = slice(t - window + 1, t + 1)
        if np.isnan(high[s]).any() or np.isnan(low[s]).any() or np.isnan(close[t]):
            continue
        out[t] = profile(high[s], low[s], volume[s], bins).share_above(float(close[t]))
    return out
