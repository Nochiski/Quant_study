"""종목 신호(A1~A15)와 오늘 점검표 상태(D1~D11) — 사전등록 4.1·4.2 절.

도구('오늘 켜진 신호'·'오늘 점검표')와 연구가 이 한 곳의 정의를 쓴다. 모든 값은 그날 종가까지의
자료로만 정해진다(테스트가 앞부분만 잘라 계산한 값과 대조한다).

- 신호는 '처음 참'이다: 그날 참, 전날 거짓, 두 날 모두 정의됨. A14·A15 는 전날 기준 수준을 넘는 교차.
- 상태는 1.0(양호)·0.0(아님)·NaN(정의 안 됨). D11 은 D1~D10 이 모두 정의된 날의 양호 개수.
"""

from dataclasses import dataclass

import numpy as np

from . import indicators as ind
from . import volume_profile as vp
from . import zigzag as zz

EVENT_IDS: tuple[str, ...] = tuple(f"A{i}" for i in range(1, 16))
STATE_IDS: tuple[str, ...] = tuple(f"D{i}" for i in range(1, 12))
ZIGZAG_ATR = 3.0
PROFILE_WINDOW = 120
PROFILE_BINS = 50


@dataclass(frozen=True, eq=False)
class Bars:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray


@dataclass(frozen=True, eq=False)
class Computed:
    bars: Bars
    atr14: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    sma50: np.ndarray
    sma200: np.ndarray
    rsi14: np.ndarray
    macd: ind.Macd
    ichimoku: ind.Ichimoku
    obv: np.ndarray
    obv_sma20: np.ndarray
    obv_max20_prev: np.ndarray  # t−20 ~ t−1 OBV 최고
    close_max20_prev: np.ndarray  # t−20 ~ t−1 종가 최고
    bollinger: ind.Bollinger
    stochastic: ind.Stochastic
    poc_prev120: np.ndarray  # t−120 ~ t−1 매물대 POC(당일 제외)
    share_above120: np.ndarray  # t−119 ~ t 매물대에서 종가보다 비싼 칸 비중(당일 포함)
    pivots: zz.Pivots
    last_high: np.ndarray  # t 일까지 확정된 마지막 지그재그 고점 가격
    last_low: np.ndarray


def _lag(x: np.ndarray) -> np.ndarray:
    return np.concatenate(([np.nan], x[:-1]))


def last_confirmed(pivots: zz.Pivots, kind: int, n: int) -> np.ndarray:
    """날마다 그날까지 확정된 마지막 kind 피봇의 가격."""
    out = np.full(n, np.nan)
    for j in np.argsort(pivots.confirm_idx, kind="stable"):
        if pivots.kind[j] == kind and pivots.confirm_idx[j] < n:
            out[int(pivots.confirm_idx[j]) :] = pivots.price[j]
    return out


def compute(bars: Bars) -> Computed:
    """신호·상태에 필요한 지표를 한 번에 계산한다."""
    h, lo, c, v = bars.high, bars.low, bars.close, bars.volume
    atr14 = ind.atr(h, lo, c, 14)
    obv = ind.obv(c, v)
    pivots = zz.zigzag(h, lo, atr14, ZIGZAG_ATR)
    return Computed(
        bars=bars,
        atr14=atr14,
        ema20=ind.ema(c, 20),
        ema50=ind.ema(c, 50),
        sma50=ind.sma(c, 50),
        sma200=ind.sma(c, 200),
        rsi14=ind.rsi(c, 14),
        macd=ind.macd(c, 12, 26, 9),
        ichimoku=ind.ichimoku(h, lo, 9, 26, 52, 26),
        obv=obv,
        obv_sma20=ind.sma(obv, 20),
        obv_max20_prev=_lag(ind.rolling_max(obv, 20)),
        close_max20_prev=_lag(ind.rolling_max(c, 20)),
        bollinger=ind.bollinger(c, 20, 2.0),
        stochastic=ind.stochastic(h, lo, c, 14, 3, 3),
        poc_prev120=vp.rolling_poc(h, lo, v, PROFILE_WINDOW, PROFILE_BINS, include_today=False),
        share_above120=vp.rolling_share_above(h, lo, c, v, PROFILE_WINDOW, PROFILE_BINS),
        pivots=pivots,
        last_high=last_confirmed(pivots, zz.HIGH, c.shape[0]),
        last_low=last_confirmed(pivots, zz.LOW, c.shape[0]),
    )


def cond(mask: np.ndarray, *inputs: np.ndarray) -> np.ndarray:
    """조건을 1.0/0.0 으로, 입력 중 하나라도 NaN 이면 NaN 으로."""
    out = np.where(mask, 1.0, 0.0)
    for x in inputs:
        out = np.where(np.isnan(x), np.nan, out)
    return out


def fresh(c: np.ndarray) -> np.ndarray:
    """그날 참·전날 거짓·두 날 모두 정의됨."""
    prev = _lag(c)
    return (c == 1.0) & (prev == 0.0)


def cross_above_prev_level(close: np.ndarray, level: np.ndarray) -> np.ndarray:
    """종가_t > 수준_{t−1} 이고 종가_{t−1} ≤ 수준_{t−1}."""
    lv = _lag(level)
    pc = _lag(close)
    return ~np.isnan(lv) & (close > lv) & (pc <= lv)


def cross_below_prev_level(close: np.ndarray, level: np.ndarray) -> np.ndarray:
    """종가_t < 수준_{t−1} 이고 종가_{t−1} ≥ 수준_{t−1}."""
    lv = _lag(level)
    pc = _lag(close)
    return ~np.isnan(lv) & (close < lv) & (pc >= lv)


def events(k: Computed) -> dict[str, np.ndarray]:
    """A1~A15 — 날마다 그 신호가 처음 켜졌는지(bool)."""
    c = k.bars.close
    rsi, m, ich, bb, st = k.rsi14, k.macd, k.ichimoku, k.bollinger, k.stochastic
    a10 = fresh(cond(st.k > st.d, st.k, st.d)) & (st.d < 20)
    return {
        "A1": fresh(cond(rsi < 30, rsi)),
        "A2": fresh(cond(rsi > 70, rsi)),
        "A3": fresh(cond(m.line > m.signal, m.line, m.signal)),
        "A4": fresh(cond(m.line < m.signal, m.line, m.signal)),
        "A5": fresh(cond(c > ich.cloud_top, ich.cloud_top)),
        "A6": fresh(cond(ich.conversion > ich.base, ich.conversion, ich.base)),
        "A7": fresh(
            cond((k.obv > k.obv_max20_prev) & (c <= k.close_max20_prev), k.obv_max20_prev, k.close_max20_prev)
        ),
        "A8": fresh(cond(c < bb.lower, bb.lower)),
        "A9": fresh(cond(c > bb.upper, bb.upper)),
        "A10": a10,
        "A11": fresh(cond(k.ema20 > k.ema50, k.ema20, k.ema50)),
        "A12": fresh(cond(k.sma50 > k.sma200, k.sma50, k.sma200)),
        "A13": fresh(cond(c > k.poc_prev120, k.poc_prev120)),
        "A14": cross_above_prev_level(c, k.last_high),
        "A15": cross_below_prev_level(c, k.last_low),
    }


def states(k: Computed) -> dict[str, np.ndarray]:
    """D1~D11 — 날마다 점검표 상태(1.0 양호·0.0 아님·NaN 정의 안 됨, D11 은 개수)."""
    c = k.bars.close
    rsi, m, ich, bb, st = k.rsi14, k.macd, k.ichimoku, k.bollinger, k.stochastic
    d = {
        "D1": cond(c > k.ema20, k.ema20),
        "D2": cond(k.ema20 > k.ema50, k.ema20, k.ema50),
        "D3": cond(c > k.sma200, k.sma200),
        "D4": cond(m.line > m.signal, m.line, m.signal),
        "D5": cond(c > ich.cloud_top, ich.cloud_top),
        "D6": cond((rsi >= 40) & (rsi <= 70), rsi),
        "D7": cond(k.obv > k.obv_sma20, k.obv_sma20),
        "D8": cond((c > bb.middle) & (c <= bb.upper), bb.middle, bb.upper),
        "D9": cond((st.k > st.d) & (st.k < 80), st.k, st.d),
        "D10": cond(k.share_above120 < 0.30, k.share_above120),
    }
    stack = np.vstack([d[f"D{i}"] for i in range(1, 11)])
    d["D11"] = np.where(np.isnan(stack).any(axis=0), np.nan, stack.sum(axis=0))
    return d
