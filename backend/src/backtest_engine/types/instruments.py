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

    def __post_init__(self) -> None:
        # 스냅샷 중복 검사·bar 인덱스·포지션 조회가 세션마다 종목 수만큼 이 값을 해시한다.
        # Enum 필드 해시가 Python 수준 호출이라 생성 시 한 번만 계산해 둔다. 필드가 아니므로
        # ==, repr, fields(), trace 직렬화에는 나타나지 않는다.
        object.__setattr__(
            self, "_hash", hash((self.venue, self.symbol, self.asset_class, self.currency))
        )

    def __hash__(self) -> int:
        return self._hash  # type: ignore[attr-defined]  # __post_init__에서 채운 캐시


@dataclass(frozen=True)
class Money:
    """통화가 붙은 금액. 통화가 다른 금액끼리의 연산 실수를 타입으로 막는다."""

    amount: Decimal
    currency: str
