"""지그재그 피봇(사전등록 3.2 절).

직전 극값에서 ATR 의 k 배 이상 되돌린 점만 피봇으로 확정한다. 각 봉에서 먼저 되돌림을 확인하고
그 다음 극값을 갱신하므로, 피봇과 확정일은 그 봉까지의 자료로만 정해진다(앞부분만 잘라 계산해도
같은 확정일까지는 결과가 같다).
"""

from dataclasses import dataclass

import numpy as np

from ._jit import jit

HIGH = 1
LOW = -1


@dataclass(frozen=True, eq=False)
class Pivots:
    kind: np.ndarray  # HIGH(1) / LOW(-1)
    idx: np.ndarray  # 피봇 봉 위치
    price: np.ndarray  # 고점은 고가, 저점은 저가
    confirm_idx: np.ndarray  # 되돌림이 k·ATR 에 닿은 봉 위치

    def usable(self, as_of: int) -> "Pivots":
        """as_of 봉까지 확정된 피봇만."""
        m = self.confirm_idx <= as_of
        return Pivots(self.kind[m], self.idx[m], self.price[m], self.confirm_idx[m])


@jit
def _zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, k: float) -> np.ndarray:
    n = high.shape[0]
    out = np.empty((n, 4))
    m = 0
    start = -1
    for i in range(n):
        if not np.isnan(atr[i]):
            start = i
            break
    if start < 0:
        return out[:0]
    direction = 0  # 0 미정, 1 고점 추적, -1 저점 추적
    hi = high[start]
    hi_i = start
    lo = low[start]
    lo_i = start
    for t in range(start + 1, n):
        a = atr[t]
        if np.isnan(a) or np.isnan(high[t]) or np.isnan(low[t]):
            continue
        if direction == 0:
            rise = high[t] >= lo + k * a
            fall = low[t] <= hi - k * a
            if rise and fall:
                # 한 봉이 양쪽을 다 넘으면 더 크게 움직인 쪽으로 정한다
                if high[t] - lo >= hi - low[t]:
                    fall = False
                else:
                    rise = False
            if rise:
                out[m, 0] = -1
                out[m, 1] = lo_i
                out[m, 2] = lo
                out[m, 3] = t
                m += 1
                direction = 1
                hi = high[lo_i + 1]
                hi_i = lo_i + 1
                for j in range(lo_i + 2, t + 1):
                    if high[j] > hi:
                        hi = high[j]
                        hi_i = j
            elif fall:
                out[m, 0] = 1
                out[m, 1] = hi_i
                out[m, 2] = hi
                out[m, 3] = t
                m += 1
                direction = -1
                lo = low[hi_i + 1]
                lo_i = hi_i + 1
                for j in range(hi_i + 2, t + 1):
                    if low[j] < lo:
                        lo = low[j]
                        lo_i = j
            else:
                if high[t] > hi:
                    hi = high[t]
                    hi_i = t
                if low[t] < lo:
                    lo = low[t]
                    lo_i = t
        elif direction == 1:
            if low[t] <= hi - k * a:
                out[m, 0] = 1
                out[m, 1] = hi_i
                out[m, 2] = hi
                out[m, 3] = t
                m += 1
                direction = -1
                lo = low[t]
                lo_i = t
            elif high[t] > hi:
                hi = high[t]
                hi_i = t
        else:
            if high[t] >= lo + k * a:
                out[m, 0] = -1
                out[m, 1] = lo_i
                out[m, 2] = lo
                out[m, 3] = t
                m += 1
                direction = 1
                hi = high[t]
                hi_i = t
            elif low[t] < lo:
                lo = low[t]
                lo_i = t
    return out[:m]


def zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, k: float = 3.0) -> Pivots:
    """지그재그 피봇을 확정 순서대로 돌려준다.

    Args:
        high, low: 고가·저가(같은 길이).
        atr: 같은 길이의 ATR. NaN 인 봉은 건너뛰고, 첫 유효 봉에서 시작한다.
        k: 되돌림 기준(ATR 배수). 사전등록 값은 3.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    atr = np.asarray(atr, dtype=float)
    if not (high.shape == low.shape == atr.shape):
        raise ValueError(f"zigzag 입력 길이가 다르다: high={high.shape} low={low.shape} atr={atr.shape}")
    raw = _zigzag(high, low, atr, float(k))
    return Pivots(
        kind=raw[:, 0].astype(np.int64),
        idx=raw[:, 1].astype(np.int64),
        price=raw[:, 2].copy(),
        confirm_idx=raw[:, 3].astype(np.int64),
    )


def significant(pivots: Pivots, high: np.ndarray, low: np.ndarray, window: int, as_of: int) -> np.ndarray:
    """앞뒤 window 봉 안에 더 극단적인 가격이 없는 피봇인지(as_of 봉까지 자료로).

    앞쪽 window 봉이 자료 안에 있어야 하고, 뒤쪽 window 봉이 as_of 까지 지나 있어야 하며,
    확정일도 as_of 이하여야 한다.
    """
    out = np.zeros(pivots.idx.shape[0], dtype=bool)
    for j in range(pivots.idx.shape[0]):
        p = int(pivots.idx[j])
        if p - window < 0 or p + window > as_of or pivots.confirm_idx[j] > as_of:
            continue
        lo_slice = slice(p - window, p)
        hi_slice = slice(p + 1, p + window + 1)
        if pivots.kind[j] == LOW:
            v = pivots.price[j]
            out[j] = bool((low[lo_slice] >= v).all() and (low[hi_slice] >= v).all())
        else:
            v = pivots.price[j]
            out[j] = bool((high[lo_slice] <= v).all() and (high[hi_slice] <= v).all())
    return out


@dataclass(frozen=True, eq=False)
class ExtremeSpan:
    back: np.ndarray  # 앞쪽으로 더 극단적인 가격이 나오기 전까지의 봉 수
    back_open: np.ndarray  # 자료 처음까지 갔으면 True
    forward: np.ndarray  # 뒤쪽(as_of 까지)
    forward_open: np.ndarray  # as_of 까지 깨지지 않았으면 True


def extreme_span(pivots: Pivots, high: np.ndarray, low: np.ndarray, as_of: int) -> ExtremeSpan:
    """피봇이 앞뒤로 몇 봉 동안 가장 낮았/높았던 점인가(도구의 '앞 N개월 · 뒤 M개월' 표시)."""
    n = pivots.idx.shape[0]
    back = np.zeros(n, dtype=np.int64)
    back_open = np.zeros(n, dtype=bool)
    fwd = np.zeros(n, dtype=np.int64)
    fwd_open = np.zeros(n, dtype=bool)
    for j in range(n):
        p = int(pivots.idx[j])
        series = low if pivots.kind[j] == LOW else high
        v = pivots.price[j]

        def beyond(x: float, kind: int = int(pivots.kind[j]), v: float = v) -> bool:
            return x < v if kind == LOW else x > v

        i = p - 1
        while i >= 0 and not beyond(float(series[i])):
            i -= 1
        back[j] = p - 1 - i
        back_open[j] = i < 0
        i = p + 1
        while i <= as_of and not beyond(float(series[i])):
            i += 1
        fwd[j] = i - p - 1
        fwd_open[j] = i > as_of
    return ExtremeSpan(back=back, back_open=back_open, forward=fwd, forward_open=fwd_open)
