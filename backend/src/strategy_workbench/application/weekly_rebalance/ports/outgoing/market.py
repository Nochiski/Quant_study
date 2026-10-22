"""Outgoing port: 호가와 현재가(S07 실행기). 주문하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.rebalance.facade.rebalance import Quote

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
