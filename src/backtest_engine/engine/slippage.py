"""슬리피지 모델 구현. 전부 `ports.execution.SlippageModel`을 만족한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backtest_engine.types.events import OrderEvent
from backtest_engine.types.market import Bar


@dataclass(frozen=True)
class NoSlippage:
    """기본값. 규칙상 체결 가격 그대로."""

    def slippage_per_share(
        self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
    ) -> float:
        return 0.0


@dataclass(frozen=True)
class FixedBpsSlippage:
    """체결 가격의 고정 비율(basis point)."""

    bps: float

    def __post_init__(self) -> None:
        if self.bps < 0:
            raise ValueError(f"slippage bps must be >= 0 — bps={self.bps}")

    def slippage_per_share(
        self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
    ) -> float:
        return base_price * self.bps / 10_000.0


@dataclass(frozen=True)
class VolumeShareSlippage:
    """거래량 비중 제곱에 비례하는 가격 충격 (Zipline VolumeShareSlippage와 같은 식).

    share = min(quantity / bar.volume, volume_limit)
    slippage = share² × price_impact × base_price
    거래량 0인 bar는 비중을 volume_limit로 본다 (가장 불리한 가정).
    """

    volume_limit: float = 0.025
    price_impact: float = 0.1

    def __post_init__(self) -> None:
        if self.volume_limit <= 0:
            raise ValueError(f"volume_limit must be > 0 — volume_limit={self.volume_limit}")
        if self.price_impact < 0:
            raise ValueError(f"price_impact must be >= 0 — price_impact={self.price_impact}")

    def slippage_per_share(
        self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
    ) -> float:
        if bar.volume <= 0:
            share = self.volume_limit
        else:
            share = min(float(quantity) / bar.volume, self.volume_limit)
        return share * share * self.price_impact * base_price
