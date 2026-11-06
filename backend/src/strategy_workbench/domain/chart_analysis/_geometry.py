"""피보나치 채널·되돌림의 기하(기준점 → 레벨 가격)의 owner(설계 문서 4.3·4.4·4.7).

자동 후보와 수동으로 그은 선이 같은 식을 쓴다. index는 선을 만든 주기 계열의 완성 봉 index다(좌표
계약, 문서 4.7). 다른 주기 화면에서는 이 가격을 기준점 날짜 축에 놓을 뿐 기준점을 그 주기 봉으로
옮겨 다시 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 되돌림·채널의 기본 레벨. 선마다 저장된 레벨이 있으면 그것이 우선이다.
DEFAULT_FIB_LEVELS: tuple[float, ...] = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
# 채널의 기본 레벨 — 되돌림 레벨에 바깥 확장 1.618을 더한다(설계 문서 6장 채널 표시). 2.618은
# 사용자가 고르는 선택 레벨이라 기본에 넣지 않는다.
DEFAULT_CHANNEL_LEVELS: tuple[float, ...] = (*DEFAULT_FIB_LEVELS, 1.618)
# 사용자가 켜고 끌 수 있는 레벨 칸(설계 문서 6장). 기본 레벨에 선택 레벨 2.618을 더한
# 목록이고, 화면의 레벨 체크 칸은 저장 선 목록 응답(`level_choices`)으로 이것을 받는다.
# 저장 레벨은 이 목록 밖의 값도 받는다.
LEVEL_CHOICES: tuple[float, ...] = (*DEFAULT_CHANNEL_LEVELS, 2.618)


@dataclass(frozen=True)
class Anchor:
    index: int
    price: float


def _require_order(first: Anchor, second: Anchor) -> None:
    if first.index >= second.index:
        raise ValueError(f"anchors must be in bar order — first={first} second={second}")


@dataclass(frozen=True)
class TrendLine:
    """두 기준점을 지나는 기준선 `L(t) = p_a + m(t − i_a)`(a = `first`, b = `second`)."""

    first: Anchor
    second: Anchor

    def __post_init__(self) -> None:
        _require_order(self.first, self.second)

    @property
    def slope(self) -> float:
        return (self.second.price - self.first.price) / (self.second.index - self.first.index)

    def at(self, index: int) -> float:
        return self.first.price + self.slope * (index - self.first.index)


@dataclass(frozen=True)
class ChannelGeometry:
    """기준선 `line`과 폭 기준점 w(`width`)로 정한 채널. 폭 `d = L(i_w) − p_w`, 레벨 r의 가격
    `C_r(t) = L(t) − r·d`.

    `d`는 상승 채널(w가 기준선보다 높음)에서 음수라 r이 클수록 위로 간다. w가 기준선 상의 점이면
    d = 0이라 모든 레벨이 기준선이다.
    """

    line: TrendLine
    width: Anchor

    @property
    def depth(self) -> float:
        return self.line.at(self.width.index) - self.width.price

    def price_at(self, index: int, level: float) -> float:
        return self.line.at(index) - level * self.depth


@dataclass(frozen=True)
class RetracementGeometry:
    """`first`(먼저 점, 레벨 1)와 `second`(나중 점, 레벨 0). `price(r) = P_0 + r·(P_1 − P_0)`."""

    first: Anchor
    second: Anchor

    def __post_init__(self) -> None:
        _require_order(self.first, self.second)

    def price_at(self, level: float) -> float:
        return self.second.price + level * (self.first.price - self.second.price)
