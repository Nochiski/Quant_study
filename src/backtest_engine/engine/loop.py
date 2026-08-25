"""BacktestEngine: 이벤트 큐를 드레인하며 컴포넌트를 조립하는 메인 루프.

컴포넌트는 서로를 직접 부르지 않는다 — 이벤트 큐가 유일한 통로이며,
그 덕에 Run → Decision → Action → Order → Fill → Snapshot 추적 순서가
EventStore에 자동으로 남는다.

한 세션(ts)의 처리 순서는 EventPriority가 고정한다:
  MARKET(대기 주문을 시가에 체결 시도) → FILL(포트폴리오 반영)
  → SESSION_CLOSE(종가 평가, 스냅샷, 전략 호출) → ORDER(주문 등록)
따라서 T 세션 종가에 내린 판단은 T+1 세션 시가에 체결되고(look-ahead 차단),
전략이 T에 읽는 포트폴리오는 T 시가 체결분까지 반영된 상태다.
"""

from __future__ import annotations

from backtest_engine.capability import (
    EngineCapabilities,
    prepare_strategy,
    reference_engine_capabilities,
)
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine import calendar
from backtest_engine.engine.broker import BrokerSim, ExecutionStatus
from backtest_engine.engine.context import EngineStrategyContext, HistoryStore
from backtest_engine.engine.metrics import compute_metrics
from backtest_engine.engine.orders import OrderManager
from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.engine.queue import (
    EventPriority,
    EventQueue,
    FillOccurred,
    MarketArrived,
    OrderPlaced,
    SessionClose,
)
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.engine.store import DecisionRecord, EventStore, RecordKind
from backtest_engine.types.events import OrderEvent, OrderStatus, OrderUpdateEvent
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.requirements import HistoryRequest, Schedule
from backtest_engine.types.results import BacktestResult, RunConfig
from backtest_engine.types.strategy import Strategy


class BacktestEngine:
    def __init__(self, config: RunConfig, capabilities: EngineCapabilities | None = None) -> None:
        self._config = config
        self._capabilities = (
            capabilities if capabilities is not None else reference_engine_capabilities()
        )
        self._event_store: EventStore | None = None

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._capabilities

    @property
    def event_store(self) -> EventStore:
        """직전 run의 append-only 이벤트 로그. run() 전에 조회하면 오류."""
        if self._event_store is None:
            raise RuntimeError(
                f"event store is only available after run() — run_id={self._config.run_id}"
            )
        return self._event_store

    def run(self, strategy: Strategy, feed: DataFeed) -> BacktestResult:
        # 1. Capability 검증은 첫 Bar를 읽기 전, 전략 등록 직후 수행한다.
        validated = prepare_strategy(strategy, self._capabilities)
        requirements = validated.requirements
        warmup_sessions = max(
            (request.lookback for request in requirements.histories), default=0
        )
        declared = frozenset(requirements.histories)

        history_store = HistoryStore()
        portfolio = Portfolio(self._config.initial_cash)
        order_manager = OrderManager()
        broker = BrokerSim(self._config.fee_bps)
        router = DecisionRouter(requirements.actions, order_manager)
        store = EventStore()
        self._event_store = store
        queue = EventQueue()

        for snapshot in feed.snapshots():
            queue.push(snapshot.ts, EventPriority.MARKET, MarketArrived(snapshot))

        while queue:
            event = queue.pop()
            match event:
                case MarketArrived(snapshot=snapshot):
                    self._on_market(
                        snapshot, history_store, portfolio, order_manager, broker, store, queue
                    )
                case FillOccurred(fill=fill):
                    portfolio.apply(fill)
                    store.append(fill.ts, RecordKind.FILL, fill)
                case SessionClose(snapshot=snapshot):
                    self._on_session_close(
                        snapshot,
                        validated.strategy,
                        requirements.schedule,
                        warmup_sessions,
                        declared,
                        history_store,
                        portfolio,
                        order_manager,
                        router,
                        store,
                        queue,
                    )
                case OrderPlaced(order=order):
                    order_manager.place(order)
                    store.append(order.ts, RecordKind.ORDER, order)

        snapshots = store.snapshots()
        return BacktestResult(
            run_id=self._config.run_id,
            snapshots=snapshots,
            orders=store.orders(),
            fills=store.fills(),
            metrics=compute_metrics(snapshots, store.fills(), self._config.annualization_days),
        )

    def _on_market(
        self,
        snapshot: MarketSnapshot,
        history_store: HistoryStore,
        portfolio: Portfolio,
        order_manager: OrderManager,
        broker: BrokerSim,
        store: EventStore,
        queue: EventQueue,
    ) -> None:
        history_store.append(snapshot)
        store.append(snapshot.ts, RecordKind.MARKET, snapshot)

        pending = order_manager.pop_all()
        executable: list[OrderEvent] = []
        for order in pending:
            if snapshot.has(order.instrument):
                executable.append(order)
            else:
                # v1 주문은 전부 DAY: 이 세션에 거래할 수 없으면 취소된다.
                store.append(
                    snapshot.ts,
                    RecordKind.ORDER_UPDATE,
                    OrderUpdateEvent(
                        ts=snapshot.ts,
                        order_id=order.order_id,
                        status=OrderStatus.CANCELLED,
                        detail=(
                            f"no bar for instrument in session — "
                            f"instrument={order.instrument.symbol} ts={snapshot.ts}"
                        ),
                    ),
                )

        # 매도 먼저 처리해 매수가 쓸 수 있는 현금을 확정한다 (결정론적 규칙).
        executable.sort(key=lambda order: (order.side is not Side.SELL, order.order_id))
        remaining_cash = portfolio.cash
        for order in executable:
            outcome = broker.execute(
                order,
                snapshot.bar(order.instrument),
                remaining_cash,
                order_manager.next_fill_id(),
            )
            if outcome.fill is not None:
                fill = outcome.fill
                notional = float(fill.quantity) * fill.price
                if fill.side is Side.SELL:
                    remaining_cash += notional - fill.fee
                else:
                    remaining_cash -= notional + fill.fee
                queue.push(fill.ts, EventPriority.FILL, FillOccurred(fill))
            if outcome.status is not ExecutionStatus.FILLED:
                status = (
                    OrderStatus.PARTIALLY_FILLED
                    if outcome.status is ExecutionStatus.CASH_LIMITED
                    else OrderStatus.REJECTED
                )
                store.append(
                    snapshot.ts,
                    RecordKind.ORDER_UPDATE,
                    OrderUpdateEvent(
                        ts=snapshot.ts,
                        order_id=order.order_id,
                        status=status,
                        detail=outcome.detail,
                    ),
                )

        queue.push(snapshot.ts, EventPriority.SESSION_CLOSE, SessionClose(snapshot))

    def _on_session_close(
        self,
        snapshot: MarketSnapshot,
        strategy: Strategy,
        schedule: Schedule,
        warmup_sessions: int,
        declared: frozenset[HistoryRequest],
        history_store: HistoryStore,
        portfolio: Portfolio,
        order_manager: OrderManager,
        router: DecisionRouter,
        store: EventStore,
        queue: EventQueue,
    ) -> None:
        portfolio.mark(snapshot)
        portfolio_snapshot = portfolio.snapshot(snapshot.ts)
        store.append(snapshot.ts, RecordKind.SNAPSHOT, portfolio_snapshot)

        if history_store.session_count < warmup_sessions:
            return
        if not calendar.matches(schedule, snapshot.ts):
            return

        context = EngineStrategyContext(
            now=snapshot.ts,
            snapshot=portfolio_snapshot,
            history_store=history_store,
            declared=declared,
        )
        decision = strategy.on_event(context, snapshot)
        decision_id = order_manager.next_decision_id()
        store.append(snapshot.ts, RecordKind.DECISION, DecisionRecord(decision_id, decision))

        routing = router.route(decision, decision_id, portfolio_snapshot, snapshot)
        for cancelled_order in routing.cancelled:
            store.append(
                snapshot.ts,
                RecordKind.ORDER_UPDATE,
                OrderUpdateEvent(
                    ts=snapshot.ts,
                    order_id=cancelled_order.order_id,
                    status=OrderStatus.CANCELLED,
                    detail=f"cancelled by LiquidatePosition — decision_id={decision_id}",
                ),
            )
        for order in routing.orders:
            queue.push(order.ts, EventPriority.ORDER, OrderPlaced(order))
