"""상태 요약 계산 — 가까운 지지·저항, 채널 안 위치, 피봇 기간 표기."""

import math
from dataclasses import dataclass

from ..fib_channel import GRID_STEP, Channel

BARS_PER_MONTH = 21


def span_label(bars: int, still_open: bool) -> str:
    """봉 수를 '5일'·'14개월' 꼴로. 아직 깨지지 않았으면 '+'."""
    text = f"{bars}일" if bars < BARS_PER_MONTH else f"{round(bars / BARS_PER_MONTH)}개월"
    return text + ("+" if still_open else "")


@dataclass(frozen=True)
class Level:
    value: float
    label: str


@dataclass(frozen=True)
class NearLevel:
    value: float
    labels: list[str]
    distance_pct: float  # (선 − 가격) / 가격 × 100, 아래면 음수
    distance_atr: float  # (선 − 가격) / ATR


@dataclass(frozen=True)
class Nearest:
    above: list[NearLevel]
    below: list[NearLevel]


def _group(levels: list[Level], price: float, merge_pct: float) -> list[list[Level]]:
    groups: list[list[Level]] = []
    for lv in levels:
        if groups and abs(lv.value - groups[-1][0].value) / price * 100 <= merge_pct:
            groups[-1].append(lv)
        else:
            groups.append([lv])
    return groups


def nearest(levels: list[Level], price: float, atr: float, k: int = 3, merge_pct: float = 0.2) -> Nearest:
    """가격 위·아래로 가까운 선 k 개씩. 서로 merge_pct% 안의 선은 한 줄로 합친다(가까운 쪽 값)."""
    valid = [lv for lv in levels if not math.isnan(lv.value)]
    above = sorted((lv for lv in valid if lv.value >= price), key=lambda lv: lv.value)
    below = sorted((lv for lv in valid if lv.value < price), key=lambda lv: -lv.value)

    def pack(groups: list[list[Level]]) -> list[NearLevel]:
        out = []
        for g in groups[:k]:
            v = g[0].value
            out.append(NearLevel(v, [lv.label for lv in g], (v - price) / price * 100, (v - price) / atr))
        return out

    return Nearest(above=pack(_group(above, price, merge_pct)), below=pack(_group(below, price, merge_pct)))


@dataclass(frozen=True)
class Position:
    ratio: float  # 채널 레벨(0 = 기준선, 1 = 세 번째 점 선)
    below_ratio: float
    above_ratio: float
    below_value: float
    above_value: float


def grid_neighbours(
    ch: Channel, x: int, price: float, k: int = 2
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """가격 바로 아래·위 0.5 등간격 선 k 개씩 (레벨, 값), 가까운 순. 하락 채널도 값 기준으로 가른다."""
    base = float(ch.level(0.0, x))
    ratio = (price - base) / ch.width
    r0 = math.floor(ratio / GRID_STEP) * GRID_STEP
    cands = [(r0 + j * GRID_STEP, float(ch.level(r0 + j * GRID_STEP, x))) for j in range(-k - 1, k + 2)]
    below = sorted((c for c in cands if c[1] < price), key=lambda c: price - c[1])[:k]
    above = sorted((c for c in cands if c[1] >= price), key=lambda c: c[1] - price)[:k]
    return below, above


def channel_position(ch: Channel, x: int, price: float) -> Position:
    """가격이 채널 몇 레벨에 있고, 바로 아래·위 0.5 등간격 선이 무엇인가."""
    base = float(ch.level(0.0, x))
    ratio = (price - base) / ch.width
    r1 = math.floor(ratio / GRID_STEP) * GRID_STEP
    r2 = r1 + GRID_STEP
    v1, v2 = float(ch.level(r1, x)), float(ch.level(r2, x))
    if v1 <= v2:
        return Position(ratio, r1, r2, v1, v2)
    return Position(ratio, r2, r1, v2, v1)
