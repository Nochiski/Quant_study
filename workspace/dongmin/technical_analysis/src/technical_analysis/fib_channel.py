"""피보나치 채널 — 세 점으로 그린 평행선 묶음(사전등록 3.3 절).

x 는 봉 위치(거래일 순번), y 는 가격(일반 눈금)이다. 기준선은 점 a·b 를 지나고, 폭 w 는 c 의 가격에서
c 위치의 기준선 값을 뺀 값이다. 레벨 r 선: y = y_a + m(x − x_a) + r·w.

- 주 세트(사용자 TradingView 설정): 폭의 0.5배 등간격 선 {0.5·k : k 정수}.
- 보조 세트: 피보나치 비율(`FIB_RATIOS`).

자동 모드는 유의 피봇으로 만든 후보를 '(세 번째 점 뒤 등간격 선에서 꺾인 수 − 같은 채널을 무작위로
옮겼을 때 평균 꺾인 수) ÷ 무작위 꺾인 수의 표준편차(최소 1)'로 순위를 매긴다(사전등록 v1.1). 그냥 세면
촘촘한 채널이 아무 흔들림에나 닿아 1등이 되고, 차이만 빼도 촘촘할수록 흔들림이 커서 1등을 독식한다.
0.5 간격이 그날 ATR 보다 좁은 채널은 후보에서 뺀다.
"""

from dataclasses import dataclass
from itertools import combinations

import numpy as np

from . import reaction as rx
from . import zigzag as zz

UP = 1
DOWN = -1
GRID_STEP = 0.5
FIB_RATIOS: tuple[float, ...] = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.618, 2.618, 3.618, 4.236)
MIN_GAP_BARS = 60
MIN_BARS_AFTER_C = 60
MAX_SCORE_BARS = 500
RECENT_PIVOTS = 8


@dataclass(frozen=True)
class Channel:
    xa: int
    ya: float
    xb: int
    yb: float
    xc: int
    yc: float

    @property
    def slope(self) -> float:
        return (self.yb - self.ya) / (self.xb - self.xa)

    @property
    def width(self) -> float:
        return self.yc - (self.ya + self.slope * (self.xc - self.xa))

    @property
    def direction(self) -> int:
        return UP if self.slope > 0 else DOWN

    def level(self, ratio: float, x: np.ndarray | int) -> np.ndarray:
        """레벨 ratio 선의 x 위치 값."""
        return self.ya + self.slope * (np.asarray(x, dtype=float) - self.xa) + ratio * self.width


@dataclass(frozen=True, eq=False)
class Grid:
    values: np.ndarray  # (x 개수, 선 개수)
    ratios: np.ndarray  # 각 선의 레벨(이동 전 기준). 이동한 선은 레벨 + offset/폭


def grid_lines(ch: Channel, x: np.ndarray, lo: float, hi: float, offset: float = 0.0) -> Grid:
    """가격 lo~hi 를 덮는 0.5 등간격 선(위아래로 한 칸씩 여유)."""
    x = np.asarray(x, dtype=float)
    base = ch.level(0.0, x) + offset
    step = GRID_STEP * ch.width
    span = abs(step)
    k_lo = int(np.floor((lo - base.max()) / span)) - 1
    k_hi = int(np.ceil((hi - base.min()) / span)) + 1
    k = np.arange(k_lo, k_hi + 1)
    values = base[:, None] + k[None, :] * span
    ratios = k * span / ch.width
    order = np.argsort(values[0]) if values.shape[0] else np.arange(k.shape[0])
    return Grid(values=values[:, order], ratios=ratios[order])


def ratio_lines(ch: Channel, x: np.ndarray, ratios: tuple[float, ...] = FIB_RATIOS, offset: float = 0.0) -> np.ndarray:
    """정해진 비율 선 행렬 (x 개수, 비율 개수)."""
    x = np.asarray(x, dtype=float)
    base = ch.level(0.0, x) + offset
    return base[:, None] + np.asarray(ratios, dtype=float)[None, :] * ch.width


def candidates(
    pivots: zz.Pivots,
    sig: np.ndarray,
    direction: int,
    min_gap: int = MIN_GAP_BARS,
    recent: int = RECENT_PIVOTS,
    min_spacing: float = 0.0,
) -> list[Channel]:
    """유의 피봇으로 만든 자동 후보.

    상승 = (저점 a, 저점 b, 고점 c), 기울기 > 0, 폭 > 0. 하락 = (고점, 고점, 저점), 기울기 < 0, 폭 < 0.
    x_b − x_a ≥ min_gap, x_c > x_a, 0.5 간격 ≥ min_spacing(기준일 ATR). 가장 최근 유의 저점·고점 각
    recent 개 안에서 고른다.
    """
    lows = [j for j in np.flatnonzero(sig) if pivots.kind[j] == zz.LOW]
    highs = [j for j in np.flatnonzero(sig) if pivots.kind[j] == zz.HIGH]
    lows = sorted(lows, key=lambda j: int(pivots.idx[j]))[-recent:]
    highs = sorted(highs, key=lambda j: int(pivots.idx[j]))[-recent:]
    base_pts, third = (lows, highs) if direction == UP else (highs, lows)
    out: list[Channel] = []
    for a, b in combinations(base_pts, 2):
        xa, xb = int(pivots.idx[a]), int(pivots.idx[b])
        if xa > xb:
            a, b, xa, xb = b, a, xb, xa
        if xb - xa < min_gap:
            continue
        ya, yb = float(pivots.price[a]), float(pivots.price[b])
        if (yb - ya) * direction <= 0:
            continue
        for c in third:
            xc = int(pivots.idx[c])
            if xc <= xa:
                continue
            ch = Channel(xa, ya, xb, yb, xc, float(pivots.price[c]))
            if ch.width * direction > 0 and abs(GRID_STEP * ch.width) >= min_spacing:
                out.append(ch)
    return out


@dataclass(frozen=True)
class Scored:
    channel: Channel
    turns: int
    expected_turns: float  # 무작위 위치로 옮긴 같은 채널의 평균 꺾임 수
    pierces: int
    last_turn_day: int  # 마지막 꺾임 날(없으면 −1)
    last_turn_ratio: float  # 그 선의 레벨
    expected_pierces: float = float("nan")  # 무작위 위치 평균 관통 수(꺾임률 비교용, 순위에는 안 씀)
    shifted_sd: float = 0.0  # 무작위 위치 꺾임 수의 표준편차(모집단)

    @property
    def score(self) -> float:
        """(실제 꺾임 − 무작위 평균) ÷ max(무작위 표준편차, 1)."""
        return (self.turns - self.expected_turns) / max(self.shifted_sd, 1.0)


def score(
    ch: Channel,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    as_of: int,
    n_shift: int = 20,
    seed: int = 0,
) -> Scored | None:
    """세 번째 점 뒤 ~ as_of(최근 500봉까지)의 등간격 선 꺾임과 무작위 대조. 60봉 미만이면 None(후보 제외)."""
    if as_of - ch.xc < MIN_BARS_AFTER_C:
        return None
    t0 = max(ch.xc + 1, as_of - MAX_SCORE_BARS + 1)
    s = slice(t0 - 1, as_of + 1)
    c, h, lo_, a = close[s], high[s], low[s], atr[s]
    x = np.arange(t0 - 1, as_of + 1)
    lo, hi = float(np.nanmin(lo_)), float(np.nanmax(h))
    last = x.shape[0] - 1

    real_grid = grid_lines(ch, x, lo, hi)
    ev = rx.reactions(c, h, lo_, a, real_grid.values, 1, last, last)
    cnt = ev.counts()
    turn_mask = ev.outcome == rx.TURN
    if turn_mask.any():
        k = int(np.flatnonzero(turn_mask)[-1])
        last_day = int(x[ev.day[k]])
        last_ratio = float(real_grid.ratios[ev.line[k]])
    else:
        last_day, last_ratio = -1, float("nan")

    rng = np.random.default_rng(seed)
    span = abs(GRID_STEP * ch.width)
    shifted_turns = []
    shifted_pierces = []
    for u in rng.uniform(0.0, span, n_shift):
        g = grid_lines(ch, x, lo, hi, offset=float(u))
        sc = rx.reactions(c, h, lo_, a, g.values, 1, last, last).counts()
        shifted_turns.append(sc.turn)
        shifted_pierces.append(sc.pierce)
    return Scored(
        channel=ch,
        turns=cnt.turn,
        expected_turns=float(np.mean(shifted_turns)) if shifted_turns else 0.0,
        pierces=cnt.pierce,
        last_turn_day=last_day,
        last_turn_ratio=last_ratio,
        expected_pierces=float(np.mean(shifted_pierces)) if shifted_pierces else float("nan"),
        shifted_sd=float(np.std(shifted_turns)) if shifted_turns else 0.0,
    )


def rank(scored: list[Scored]) -> list[Scored]:
    """점수 높은 순, 같으면 세 번째 점이 최근인 순."""
    return sorted(scored, key=lambda s: (-s.score, -s.channel.xc))


def auto_select(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    pivots: zz.Pivots,
    as_of: int,
    direction: int,
    n_shift: int = 20,
    seed: int = 0,
    sig_window: int = 42,
) -> list[Scored]:
    """as_of 까지 자료만으로 자동 채널 후보를 점수 순으로."""
    usable = pivots.usable(as_of)
    sig = zz.significant(usable, high, low, sig_window, as_of)
    scored = []
    for ch in candidates(usable, sig, direction, min_spacing=float(atr[as_of])):
        sc = score(ch, close, high, low, atr, as_of, n_shift, seed)
        if sc is not None:
            scored.append(sc)
    return rank(scored)
