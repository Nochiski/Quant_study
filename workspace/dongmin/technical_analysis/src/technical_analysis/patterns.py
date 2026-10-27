"""일봉 차트 패턴 검출(연구 4차 사전등록 PREREG4 2절).

극값 = 확정된 지그재그 피봇(ATR 2배). 패턴은 마지막 극값이 확정된 날부터, 그 극값 봉 + 20봉 안에서,
다음 피봇이 확정되기 전에 돌파 조건(종가)이 처음 참이 된 날 '완성'된다. 그날까지의 자료만 쓴다.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import indicators as ind
from . import zigzag as zz

ZZ_K = 2.0
TOL = 0.04  # '같은 수준' 허용(평균 대비)
MAX_SPAN = 63  # E1 봉 ~ 완성일
BREAK_WITHIN = 20  # 마지막 극값 봉 + 이 봉 수 안에 돌파
MIN_DEPTH = 0.03  # 머리·골 높이 하한
SHOULDER_RATIO = (0.25, 0.70)
DOUBLE_GAP = 22  # 이중 천장·바닥 두 극값 사이 봉 수 하한(초과)
BOX_HEIGHT = (0.05, 0.30)

NAMES = {
    "H1": "헤드앤숄더",
    "H2": "역헤드앤숄더",
    "H3": "이중 천장",
    "H4": "이중 바닥",
    "H5": "박스 상향 돌파",
    "H6": "삼각형 상향 돌파",
    "H7": "깃발",
    "C1": "상승 장악형",
    "C2": "장대양봉",
    "G1": "상승 갭",
    "G2": "하락 갭",
}
BULLISH = ("H2", "H4", "H5", "H6", "H7", "C1", "C2", "G1")


@dataclass(frozen=True)
class Pattern:
    kind: str  # NAMES 의 키
    complete: int  # 완성일(봉 위치)
    points: tuple[int, ...]  # 극값 봉 위치(E1, E2, …). 캔들·갭은 빈 튜플
    prices: tuple[float, ...]
    level: float  # 완성일에 넘은 선의 값(넥라인·돌파 가격). 캔들·갭은 NaN


def _close(a: float, b: float) -> bool:
    return abs(a - b) / ((a + b) / 2) <= TOL


def _line(x0: int, y0: float, x1: int, y1: float, x: int) -> float:
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _complete(
    close: np.ndarray, piv: zz.Pivots, first: int, last: int, cross: Callable[[int], float], up: bool
) -> tuple[int, float] | None:
    """마지막 피봇(last) 확정일부터 돌파일을 찾는다. cross(t) = t 일의 선 값, up = 위로 넘기."""
    start = int(piv.confirm_idx[last])
    stop = min(int(piv.idx[last]) + BREAK_WITHIN, int(piv.idx[first]) + MAX_SPAN, close.shape[0] - 1)
    if last + 1 < piv.idx.shape[0]:
        stop = min(stop, int(piv.confirm_idx[last + 1]) - 1)
    for t in range(start, stop + 1):
        lv = cross(t)
        if (close[t] > lv) if up else (close[t] < lv):
            return t, lv
    return None


def pivot_patterns(close: np.ndarray, piv: zz.Pivots) -> list[Pattern]:
    """H1~H6 — 연속한 확정 피봇으로 만든 패턴."""
    out: list[Pattern] = []
    k, ix, pr = piv.kind, piv.idx, piv.price
    n = k.shape[0]

    def add(kind: str, i0: int, i1: int, cross: Callable[[int], float], up: bool) -> None:
        hit = _complete(close, piv, i0, i1, cross, up)
        if hit is not None:
            pts = tuple(int(x) for x in ix[i0 : i1 + 1])
            out.append(Pattern(kind, hit[0], pts, tuple(float(x) for x in pr[i0 : i1 + 1]), hit[1]))

    for i in range(n):
        # 5개짜리: H1·H2·H6
        if i + 4 < n:
            e1, e2, e3, e4, e5 = pr[i : i + 5]
            x2, x3, x4, x5 = (int(v) for v in ix[i + 1 : i + 5])
            head = abs(e3 - (e2 + e4) / 2)
            if k[i] == zz.HIGH and e3 > e1 and e3 > e5 and _close(e1, e5) and _close(e2, e4) and head > 0:
                r = ((e1 - e2) + (e5 - e4)) / 2 / head
                if SHOULDER_RATIO[0] <= r <= SHOULDER_RATIO[1] and head / e3 >= MIN_DEPTH:
                    add("H1", i, i + 4, lambda t, a=(x2, e2, x4, e4): _line(*a, t), False)
            if k[i] == zz.LOW and e3 < e1 and e3 < e5 and _close(e1, e5) and _close(e2, e4) and head > 0:
                r = ((e2 - e1) + (e4 - e5)) / 2 / head
                if SHOULDER_RATIO[0] <= r <= SHOULDER_RATIO[1] and head / e3 >= MIN_DEPTH:
                    add("H2", i, i + 4, lambda t, a=(x2, e2, x4, e4): _line(*a, t), True)
            if k[i] == zz.HIGH and e1 > e3 > e5 and e2 < e4:
                add("H6", i, i + 4, lambda t, a=(x3, e3, x5, e5): _line(*a, t), True)
        # 3개짜리: H3·H4
        if i + 2 < n:
            e1, e2, e3 = pr[i : i + 3]
            top = (e1 + e3) / 2
            if ix[i + 2] - ix[i] > DOUBLE_GAP and _close(e1, e3):
                if k[i] == zz.HIGH and (top - e2) / top >= MIN_DEPTH:
                    add("H3", i, i + 2, lambda t, v=e2: v, False)
                if k[i] == zz.LOW and (e2 - top) / top >= MIN_DEPTH:
                    add("H4", i, i + 2, lambda t, v=e2: v, True)
        # 4개짜리: H5
        if i + 3 < n:
            seg = pr[i : i + 4]
            hi = seg[k[i : i + 4] == zz.HIGH]
            lo = seg[k[i : i + 4] == zz.LOW]
            if hi.shape[0] == 2 and lo.shape[0] == 2 and _close(hi[0], hi[1]) and _close(lo[0], lo[1]):
                height = hi.mean() / lo.mean() - 1
                if BOX_HEIGHT[0] <= height <= BOX_HEIGHT[1]:
                    add("H5", i, i + 3, lambda t, v=float(hi.max()): v, True)
    return out


def flags(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[Pattern]:
    """H7 상승 깃발 — 10봉 안 종가 +15% 깃대, 뒤 5~15봉 깃발, 깃발 고가 최댓값 위로 종가 돌파."""
    n = close.shape[0]
    hits: dict[int, Pattern] = {}
    for p in range(10, n):
        base = float(np.min(close[p - 10 : p]))
        if not close[p] / base - 1 >= 0.15:
            continue
        floor = close[p] - 0.5 * (close[p] - base)
        top = -np.inf
        for t in range(p + 1, min(p + 17, n)):
            m = t - 1 - p  # 깃발 길이(p+1 … t−1)
            if m >= 1:
                if high[t - 1] > high[p] or low[t - 1] < floor:
                    break
                top = max(top, float(high[t - 1]))
            if 5 <= m <= 15 and close[t] > top:
                if t not in hits:
                    hits[t] = Pattern("H7", t, (p,), (float(close[p]),), top)
                break
    return list(hits.values())


def candles_gaps(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray) -> list[Pattern]:
    """C1 상승 장악형(하락 뒤) · C2 장대양봉 · G1 상승 갭(안 메움) · G2 하락 갭(안 메움). 신호일 = 완성일."""
    out: list[Pattern] = []
    nan = float("nan")
    for t in range(1, close.shape[0]):
        a = atr[t - 1]
        body = close[t] - open_[t]
        if (
            t >= 6 and np.isfinite(a)
            and close[t - 1] < open_[t - 1] and body > 0
            and open_[t] <= close[t - 1] and close[t] >= open_[t - 1]
            and body >= 0.5 * a and close[t - 1] / close[t - 6] - 1 < 0
        ):
            out.append(Pattern("C1", t, (), (), nan))
        rng = high[t] - low[t]
        if np.isfinite(a) and body >= 2 * a and rng > 0 and (close[t] - low[t]) / rng >= 0.75:
            out.append(Pattern("C2", t, (), (), nan))
        if open_[t] >= close[t - 1] * 1.03 and low[t] > high[t - 1]:
            out.append(Pattern("G1", t, (), (), nan))
        if open_[t] <= close[t - 1] * 0.97 and high[t] < low[t - 1]:
            out.append(Pattern("G2", t, (), (), nan))
    return out


def detect(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[Pattern]:
    """한 종목 일봉(가격 있는 봉만)의 모든 패턴. 완성일 순."""
    atr = ind.atr(high, low, close, 14)
    piv = zz.zigzag(high, low, atr, ZZ_K)
    found = pivot_patterns(close, piv) + flags(high, low, close) + candles_gaps(open_, high, low, close, atr)
    return sorted(found, key=lambda p: (p.complete, p.kind))
