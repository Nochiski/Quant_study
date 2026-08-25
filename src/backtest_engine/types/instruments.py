"""종목 식별자와 통화 금액 값 타입."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class AssetClass(Enum):
    EQUITY = "equity"


@dataclass(frozen=True)
class InstrumentId:
    """거래소까지 포함한 종목 식별자.

    같은 symbol이 다른 거래소·자산군에서 재사용될 수 있으므로
    symbol 문자열 하나로 종목을 식별하지 않는다.
    """

    venue: str
    symbol: str
    asset_class: AssetClass
    currency: str


@dataclass(frozen=True)
class Money:
    """통화가 붙은 금액. 통화가 다른 금액끼리의 연산 실수를 타입으로 막는다."""

    amount: Decimal
    currency: str
