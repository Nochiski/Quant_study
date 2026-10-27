"""고전 피봇 포인트(P·R1~R3·S1~S3). 직전 일·주·월의 고·저·종으로 계산한다."""

from dataclasses import dataclass
from typing import Literal

import numpy as np

Period = Literal["D", "W", "M"]


@dataclass(frozen=True, eq=False)
class Levels:
    p: np.ndarray
    r1: np.ndarray
    s1: np.ndarray
    r2: np.ndarray
    s2: np.ndarray
    r3: np.ndarray
    s3: np.ndarray
    range_: np.ndarray  # 직전 기간 고 − 저(무작위 대조선 이동 폭에 쓴다)

    def matrix(self) -> np.ndarray:
        """(날, 선 7개) 행렬. 열 순서 S3·S2·S1·P·R1·R2·R3(낮은 선부터)."""
        return np.column_stack([self.s3, self.s2, self.s1, self.p, self.r1, self.r2, self.r3])


def classic(high: np.ndarray | float, low: np.ndarray | float, close: np.ndarray | float) -> Levels:
    """한 기간의 고·저·종으로 피봇 포인트를 계산한다."""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    p = (h + lo + c) / 3.0
    return Levels(
        p=p,
        r1=2 * p - lo,
        s1=2 * p - h,
        r2=p + (h - lo),
        s2=p - (h - lo),
        r3=h + 2 * (p - lo),
        s3=lo - 2 * (h - p),
        range_=h - lo,
    )


def _period_key(dates: np.ndarray, period: Period) -> np.ndarray:
    days = dates.astype("datetime64[D]").astype(np.int64)
    if period == "D":
        return days
    if period == "W":
        # 1970-01-01 은 목요일이다. 월요일 = 0 이 되게 3 을 더해 주 시작일(월요일)을 키로 쓴다
        return days - (days + 3) % 7
    return dates.astype("datetime64[M]").astype(np.int64)


def period_levels(
    dates: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: Period
) -> Levels:
    """날마다 '직전 기간'의 피봇 포인트. 첫 기간은 직전 기간이 없어 NaN.

    D 는 직전 거래일, W 는 직전 달력 주(월~일), M 은 직전 달력 월.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    key = _period_key(np.asarray(dates), period)
    n = key.shape[0]
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    pc = np.full(n, np.nan)
    # 기간 경계마다 직전 기간의 고·저·종을 구해 다음 기간 전체에 채운다
    starts = np.flatnonzero(np.concatenate(([True], key[1:] != key[:-1])))
    ends = np.concatenate((starts[1:], [n]))
    for g in range(1, starts.shape[0]):
        a, b = starts[g - 1], ends[g - 1]
        ph[starts[g] : ends[g]] = np.nanmax(high[a:b])
        pl[starts[g] : ends[g]] = np.nanmin(low[a:b])
        pc[starts[g] : ends[g]] = close[b - 1]
    return classic(ph, pl, pc)
