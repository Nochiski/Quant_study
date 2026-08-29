"""포트폴리오 관찰 타입: Position, PortfolioSnapshot.

둘 다 FillEvent를 누적해 계산되는 파생 상태의 읽기 전용 뷰다.
Snapshot 자체를 수정하지 않는다 — 새 상태가 필요하면 다음 Snapshot을 새로 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from backtest_engine.types.instruments import InstrumentId


@dataclass(frozen=True)
class Position:
    """종목 하나에 대해 체결 내역을 누적한 현재 보유 상태.

    v1에서는 average_price에 수수료를 섞지 않고 수수료는 cash에서 별도로 차감한다.
    """

    instrument: InstrumentId
    quantity: Decimal
    average_price: float
    market_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    """특정 시점의 포트폴리오를 읽기 전용으로 고정한 관찰값.

    equity = cash + Σ market_value 가 항상 성립해야 한다.
    gross_exposure는 Σ|market_value| / equity 비율이다 (equity가 0이면 0.0).
    """

    ts: datetime
    cash: float
    positions: tuple[Position, ...]
    equity: float
    gross_exposure: float
    # 종목 → Position 인덱스. 값 동등성·해시·repr에서 제외 (조회 전용).
    _by_instrument: dict[InstrumentId, Position] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_instrument", {position.instrument: position for position in self.positions}
        )

    def position(self, instrument: InstrumentId) -> Position | None:
        return self._by_instrument.get(instrument)

    def position_qty(self, instrument: InstrumentId) -> Decimal:
        found = self.position(instrument)
        return found.quantity if found is not None else Decimal(0)

    def weight(self, instrument: InstrumentId) -> float:
        if self.equity == 0:
            return 0.0
        found = self.position(instrument)
        return found.market_value / self.equity if found is not None else 0.0
