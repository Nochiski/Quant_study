"""Outgoing port: 호가·현재가·종목 상태(S07 실행기). 주문하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.rebalance.facade.rebalance import Quote
from strategy_workbench.domain.risk.facade.risk import TradeStatus

__all__ = ["MarketError", "MarketSource", "PriceInfo"]


class MarketError(Exception):
    """호가·현재가를 읽지 못했다."""


@dataclass(frozen=True)
class PriceInfo:
    symbol: str
    name: str
    last_price_krw: int


class MarketSource(Protocol):
    def quote(self, symbol: str) -> Quote: ...

    def price(self, symbol: str) -> PriceInfo: ...

    def trade_status(self, symbol: str) -> TradeStatus:
        """주문 직전 리스크 판정용 종목 표시·상하한가·그날 VI. 읽지 못하면 `MarketError`."""
        ...
