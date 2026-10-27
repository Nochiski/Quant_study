"""선 반응 — 가격이 선에 닿은 뒤 꺾였나 뚫었나(사전등록 3.4 절).

- 위에서 닿음: 전날 종가 > L + 0.25·A 이고 그날 저가 ≤ L + 0.25·A. 여러 선이면 위에서 처음 만나는 선.
  아래서 닿음은 대칭. 같은 날 양쪽이면 전날 종가에 더 가까운 선 하나(같으면 위에서 닿음).
- A = 닿은 날 전날의 ATR. L 은 그날(d)의 선 값이다.
- 결과(d ~ d+9 종가): 원래 쪽으로 L ± A 를 먼저 넘으면 꺾임, 반대로 넘으면 관통, 둘 다 없으면 미결.
  기운 선은 날마다 그날 선 값으로 잰다.
- 반응은 겹치지 않는다: 다음 닿음은 결과가 난 다음 날(미결이면 d+10)부터 찾는다.
- last 보다 뒤 자료가 필요한데 그 전에 결과가 안 난 반응은 버린다(창이 구간·기준일 밖으로 나감).
"""

from dataclasses import dataclass

import numpy as np

from ._jit import jit

FROM_ABOVE = 1
FROM_BELOW = -1
TURN = 1
PIERCE = -1
UNDECIDED = 0

TOUCH_ATR = 0.25
MOVE_ATR = 1.0
HORIZON = 10


@dataclass(frozen=True)
class Counts:
    turn: int
    pierce: int
    undecided: int

    @property
    def decided(self) -> int:
        return self.turn + self.pierce

    @property
    def turn_rate(self) -> float:
        return self.turn / self.decided if self.decided else float("nan")


@dataclass(frozen=True, eq=False)
class Events:
    day: np.ndarray  # 닿은 날
    line: np.ndarray  # 선 열 번호
    side: np.ndarray  # FROM_ABOVE / FROM_BELOW
    outcome: np.ndarray  # TURN / PIERCE / UNDECIDED
    resolved: np.ndarray  # 결과가 난 날(미결이면 d+9)

    def counts(self) -> Counts:
        return Counts(
            turn=int((self.outcome == TURN).sum()),
            pierce=int((self.outcome == PIERCE).sum()),
            undecided=int((self.outcome == UNDECIDED).sum()),
        )


@jit
def _reactions(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    lines: np.ndarray,
    t0: int,
    t1: int,
    last: int,
) -> np.ndarray:
    n_lines = lines.shape[1]
    out = np.empty((max(t1 - t0 + 1, 0), 5), dtype=np.int64)
    m = 0
    d = max(t0, 1)
    while d <= t1:
        a = atr[d - 1]
        pc = close[d - 1]
        if np.isnan(a) or np.isnan(pc) or np.isnan(low[d]) or np.isnan(high[d]):
            d += 1
            continue
        tol = TOUCH_ATR * a
        best_above = -1
        best_above_v = -np.inf
        best_below = -1
        best_below_v = np.inf
        for j in range(n_lines):
            v = lines[d, j]
            if np.isnan(v):
                continue
            if pc > v + tol and low[d] <= v + tol and v > best_above_v:
                best_above = j
                best_above_v = v
            if pc < v - tol and high[d] >= v - tol and v < best_below_v:
                best_below = j
                best_below_v = v
        if best_above < 0 and best_below < 0:
            d += 1
            continue
        if best_above >= 0 and best_below >= 0:
            if (best_below_v - pc) < (pc - best_above_v):
                best_above = -1
            else:
                best_below = -1
        if best_above >= 0:
            j = best_above
            side = FROM_ABOVE
        else:
            j = best_below
            side = FROM_BELOW
        move = MOVE_ATR * a
        outcome = 2  # 아직 모름
        res = -1
        for i in range(HORIZON):
            k = d + i
            if k > last:
                break
            v = lines[k, j]
            c = close[k]
            if np.isnan(v) or np.isnan(c):
                continue
            if side == FROM_ABOVE:
                if c >= v + move:
                    outcome = TURN
                elif c <= v - move:
                    outcome = PIERCE
            else:
                if c <= v - move:
                    outcome = TURN
                elif c >= v + move:
                    outcome = PIERCE
            if outcome != 2:
                res = k
                break
        if outcome == 2:
            if d + HORIZON - 1 > last:
                break  # 결과 창이 기준일을 넘는다 — 이 반응과 그 뒤는 셀 수 없다
            outcome = UNDECIDED
            res = d + HORIZON - 1
        out[m, 0] = d
        out[m, 1] = j
        out[m, 2] = side
        out[m, 3] = outcome
        out[m, 4] = res
        m += 1
        d = res + 1
    return out[:m]


def reactions(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    lines: np.ndarray,
    t0: int,
    t1: int,
    last: int,
) -> Events:
    """t0~t1 에 닿은 반응을 찾는다.

    Args:
        close, high, low, atr: 같은 길이(n) 일봉 배열.
        lines: (n, 선 개수) 행렬. 열 하나가 한 선의 날짜별 값이다. NaN 은 그날 그 선이 없다는 뜻.
        t0, t1: 닿음을 찾을 날 범위(양끝 포함).
        last: 결과 판정에 쓸 수 있는 마지막 날(구간 끝 또는 기준일).
    """
    close = np.asarray(close, dtype=float)
    lines = np.asarray(lines, dtype=float)
    if lines.ndim != 2 or lines.shape[0] != close.shape[0]:
        raise ValueError(f"lines 는 (날 {close.shape[0]}, 선 개수) 행렬이어야 한다: got {lines.shape}")
    raw = _reactions(
        close,
        np.asarray(high, dtype=float),
        np.asarray(low, dtype=float),
        np.asarray(atr, dtype=float),
        lines,
        int(t0),
        int(min(t1, close.shape[0] - 1)),
        int(min(last, close.shape[0] - 1)),
    )
    return Events(day=raw[:, 0], line=raw[:, 1], side=raw[:, 2], outcome=raw[:, 3], resolved=raw[:, 4])
