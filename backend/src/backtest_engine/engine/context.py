"""전략에 건네는 읽기 전용 조회 객체와 그 뒤의 HistoryStore.

ctx는 어디서나 접근하는 전역 변수가 아니라 엔진이 매 호출마다
함수 인자로 명시적으로 건네는 값이다. 전략은 여기서 읽기만 한다.
"""

from __future__ import annotations

import abc
import functools
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np

from backtest_engine.errors import (
    InsufficientHistoryError,
    UndeclaredDataAccess,
    UniverseNotProvided,
)
from backtest_engine.ports.universe import UniverseResult
from backtest_engine.types.events import OpenOrderSnapshot
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceField, PriceWindow
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import HistoryRequest

if TYPE_CHECKING:
    from backtest_engine.engine.store import PersistentEventStore


class HistoryStore:
    """세션 축에 정렬된 가격 기록. 엔진이 세션마다 append하며 전략은 window로만 읽는다.

    현재 시점까지 도착한 스냅샷만 담고 있고, window()도 end 이하의 행만
    반환하므로 미래 데이터 유입(look-ahead)이 이중으로 차단된다.
    """

    def __init__(self) -> None:
        self._sessions: list[datetime] = []
        # 종목별로 필드 시리즈를 묶는다 — 세션마다 종목 수 × 5번 튜플 해시를 피한다.
        self._values: dict[InstrumentId, dict[PriceField, list[float]]] = {}

    def append(self, snapshot: MarketSnapshot) -> None:
        self._sessions.append(snapshot.ts)
        row_index = len(self._sessions) - 1
        for bar in snapshot.bars:
            by_field = self._values.get(bar.instrument)
            if by_field is None:
                by_field = {price_field: [] for price_field in PriceField}
                self._values[bar.instrument] = by_field
            for price_field, value in (
                (PriceField.OPEN, bar.open),
                (PriceField.HIGH, bar.high),
                (PriceField.LOW, bar.low),
                (PriceField.CLOSE, bar.close),
                (PriceField.VOLUME, float(bar.volume)),
            ):
                series = by_field[price_field]
                # 이 종목이 빠졌던 세션은 결측(NaN)으로 채워 세션 축을 정렬한다.
                missing = row_index - len(series)
                if missing > 0:
                    series.extend([float("nan")] * missing)
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
            by_field = self._values.get(instrument)
            series = by_field[request.field] if by_field is not None else []
            for row, index in enumerate(selected):
                if index < len(series):
                    matrix[row, column] = series[index]
        return PriceWindow(timestamps=timestamps, instruments=request.instruments, values=matrix)


class PersistentHistoryStore(HistoryStore):
    """HistoryStore-compatible view backed by the Rust columnar feed."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def append(self, snapshot: MarketSnapshot) -> None:
        # process_market_index() advances the authoritative Rust session cursor later in MARKET.
        del snapshot

    @property
    def session_count(self) -> int:
        return int(self._runtime.current_session_count())

    def window(self, request: HistoryRequest, end: datetime) -> PriceWindow:
        keys = [
            f"{instrument.venue}:{instrument.symbol}:{instrument.asset_class.value}:"
            f"{instrument.currency}"
            for instrument in request.instruments
        ]
        try:
            timestamp_texts, flat_values = self._runtime.history_window(
                keys, request.field.value, request.lookback, str(end)
            )
        except ValueError as error:
            message = str(error)
            if message.startswith("insufficient_history:"):
                detail = message.removeprefix("insufficient_history: ")
                raise InsufficientHistoryError(
                    f"not enough history — {detail} "
                    f"instruments={[i.symbol for i in request.instruments]}"
                ) from error
            raise
        timestamps = tuple(datetime.fromisoformat(text) for text in timestamp_texts)
        matrix = np.asarray(flat_values, dtype=np.float64).reshape(
            len(timestamps), len(request.instruments)
        )
        return PriceWindow(timestamps=timestamps, instruments=request.instruments, values=matrix)


class _DeclaredContextMethods(abc.ABC):
    """두 Context 구현이 공유하는 읽기 전용 조회 메서드.

    미선언 history 요청과 universe 미제공의 거절 메시지는 이 클래스가 단일 정본이다.
    전에는 엔진 경로와 Rust 경로가 메시지 문자열까지 복사해 한쪽만 고치면 같은 전략이
    코어에 따라 다른 진단을 받았다.

    서브클래스는 아래 속성을 dataclass 필드나 property로 제공하고 `_open_orders_view()`를
    구현한다. `snapshot`은 한쪽이 필드, 다른 쪽이 `cached_property`라 여기서는 값을 두지
    않고 계약만 선언한다.
    """

    now: datetime
    snapshot: PortfolioSnapshot
    history_store: HistoryStore
    declared: frozenset[HistoryRequest]
    universe_source: UniverseResult | None

    @abc.abstractmethod
    def _open_orders_view(self) -> tuple[OpenOrderSnapshot, ...]:
        """이 컨텍스트 시점에 대기 중인 주문 전체. 필터링은 `open_orders()`가 한다."""

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

    def universe(self) -> frozenset[InstrumentId]:
        if self.universe_source is None:
            raise UniverseNotProvided(
                f"ctx.universe() requires BacktestEngine.run(..., universe=...) — now={self.now}"
            )
        # 호출한 전략만 비용을 낸다 (세션당 O(memberships)).
        return self.universe_source.members(self.now.date())

    def open_orders(self, instrument: InstrumentId | None = None) -> tuple[OpenOrderSnapshot, ...]:
        orders = self._open_orders_view()
        if instrument is None:
            return orders
        return tuple(order for order in orders if order.instrument == instrument)


@dataclass(frozen=True, eq=False)  # HistoryStore 필드 → eq 비교 무의미
class EngineStrategyContext(_DeclaredContextMethods):
    """선언한 데이터만 현재 시점까지 잘라서 돌려주는 엔진 소유 Context 구현체.

    전략별 차이는 Context 클래스가 아니라 declared 데이터와 schedule 값이다.
    """

    now: datetime
    snapshot: PortfolioSnapshot
    history_store: HistoryStore
    declared: frozenset[HistoryRequest] = field(default_factory=frozenset)
    open_orders_snapshot: tuple[OpenOrderSnapshot, ...] = ()
    universe_source: UniverseResult | None = None  # None = 제공 안 됨; 조회 시점에 계산

    def _open_orders_view(self) -> tuple[OpenOrderSnapshot, ...]:
        return self.open_orders_snapshot


@dataclass(frozen=True, eq=False)
class RustStrategyContext(_DeclaredContextMethods):
    """Rust callback frame에 묶인 전략 조회 뷰.

    포트폴리오·대기 주문은 Rust가 콜백 시점에 고정한 wire이며 전략이 실제로 읽을 때만 공개
    객체로 만든다. history는 `now`를 end로 사용하므로 context를 보관했다가 나중에 읽어도
    미래 상태가 섞이지 않는다.
    """

    now: datetime
    frame: Any  # reason: pyo3 CallbackFrame — 확장 모듈 stub 부재, getter는 store가 해석
    store: PersistentEventStore
    history_store: HistoryStore
    declared: frozenset[HistoryRequest] = field(default_factory=frozenset)
    universe_source: UniverseResult | None = None

    @functools.cached_property
    def snapshot(self) -> PortfolioSnapshot:
        return self.store.snapshot_from_wire(self.now, self.frame.snapshot)

    @functools.cached_property
    def _open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        return self.store.open_orders_from_wire(self.now, self.frame.open_orders)

    def _open_orders_view(self) -> tuple[OpenOrderSnapshot, ...]:
        return self._open_orders
