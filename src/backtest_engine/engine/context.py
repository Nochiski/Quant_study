"""전략에 건네는 읽기 전용 조회 객체와 그 뒤의 HistoryStore.

ctx는 어디서나 접근하는 전역 변수가 아니라 엔진이 매 호출마다
함수 인자로 명시적으로 건네는 값이다. 전략은 여기서 읽기만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import numpy as np

from backtest_engine.errors import InsufficientHistoryError, UndeclaredDataAccess
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceField, PriceWindow
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import HistoryRequest


class HistoryStore:
    """세션 축에 정렬된 가격 기록. 엔진이 세션마다 append하며 전략은 window로만 읽는다.

    현재 시점까지 도착한 스냅샷만 담고 있고, window()도 end 이하의 행만
    반환하므로 미래 데이터 유입(look-ahead)이 이중으로 차단된다.
    """

    def __init__(self) -> None:
        self._sessions: list[datetime] = []
        self._values: dict[tuple[InstrumentId, PriceField], list[float]] = {}

    def append(self, snapshot: MarketSnapshot) -> None:
        self._sessions.append(snapshot.ts)
        row_index = len(self._sessions) - 1
        for bar in snapshot.bars:
            for price_field, value in (
                (PriceField.OPEN, bar.open),
                (PriceField.HIGH, bar.high),
                (PriceField.LOW, bar.low),
                (PriceField.CLOSE, bar.close),
                (PriceField.VOLUME, float(bar.volume)),
            ):
                series = self._values.setdefault((bar.instrument, price_field), [])
                # 이 종목이 빠졌던 세션은 결측(NaN)으로 채워 세션 축을 정렬한다.
                while len(series) < row_index:
                    series.append(float("nan"))
                series.append(value)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def window(self, request: HistoryRequest, end: datetime) -> PriceWindow:
        """end 이하 세션 중 마지막 lookback개를 time × symbol 배열로 반환한다."""
        usable = [index for index, ts in enumerate(self._sessions) if ts <= end]
        if len(usable) < request.lookback:
            raise InsufficientHistoryError(
                f"not enough history — requested lookback={request.lookback} "
                f"available={len(usable)} end={end} "
                f"instruments={[i.symbol for i in request.instruments]}"
            )
        selected = usable[-request.lookback :]
        timestamps = tuple(self._sessions[index] for index in selected)
        matrix = np.full((len(selected), len(request.instruments)), np.nan, dtype=np.float64)
        for column, instrument in enumerate(request.instruments):
            series = self._values.get((instrument, request.field), [])
            for row, index in enumerate(selected):
                if index < len(series):
                    matrix[row, column] = series[index]
        return PriceWindow(timestamps=timestamps, instruments=request.instruments, values=matrix)


@dataclass(frozen=True, eq=False)  # HistoryStore 필드 → eq 비교 무의미
class EngineStrategyContext:
    """선언한 데이터만 현재 시점까지 잘라서 돌려주는 엔진 소유 Context 구현체.

    전략별 차이는 Context 클래스가 아니라 declared 데이터와 schedule 값이다.
    """

    now: datetime
    snapshot: PortfolioSnapshot
    history_store: HistoryStore
    declared: frozenset[HistoryRequest] = field(default_factory=frozenset)
    open_orders_snapshot: tuple[OrderEvent, ...] = ()

    def history(self, request: HistoryRequest) -> PriceWindow:
        if request not in self.declared:
            raise UndeclaredDataAccess(
                f"history request was not declared in requirements() — "
                f"requested field={request.field.value} lookback={request.lookback} "
                f"instruments={[i.symbol for i in request.instruments]} "
                f"declared_count={len(self.declared)}"
            )
        return self.history_store.window(request=request, end=self.now)

    def current_weight(self, instrument: InstrumentId) -> float:
        return self.snapshot.weight(instrument)

    def position_qty(self, instrument: InstrumentId) -> Decimal:
        return self.snapshot.position_qty(instrument)

    def cash(self) -> float:
        return self.snapshot.cash

    def portfolio_value(self) -> float:
        return self.snapshot.equity

    def open_orders(self, instrument: InstrumentId | None = None) -> tuple[OrderEvent, ...]:
        if instrument is None:
            return self.open_orders_snapshot
        return tuple(o for o in self.open_orders_snapshot if o.instrument == instrument)
