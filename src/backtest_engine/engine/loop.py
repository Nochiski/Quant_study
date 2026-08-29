"""BacktestEngine: 이벤트 큐를 드레인하며 컴포넌트를 조립하는 메인 루프.

컴포넌트는 서로를 직접 부르지 않는다 — 이벤트 큐가 유일한 통로이며,
그 덕에 Run → Decision → Action → Order → Fill → Snapshot 추적 순서가
EventStore에 자동으로 남는다.

한 세션(ts)의 처리 순서는 EventPriority가 고정한다:
  MARKET(대기 주문을 체결 시도) → FILL(포트폴리오 반영)
  → NOTIFY(전략이 선언한 Fill/OrderUpdate 알림) → SESSION_CLOSE(종가 평가, 스냅샷, 전략 호출)
  → ORDER(주문 등록)
따라서 T 세션 종가에 내린 판단은 T+1 세션부터 체결되고(look-ahead 차단),
전략이 T에 읽는 포트폴리오는 T 세션 체결분까지 반영된 상태다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime

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
    StrategyNotify,
)
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.engine.store import DecisionRecord, EventStore, RecordKind
from backtest_engine.errors import CorporateActionWithoutBar
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    OrderStatus,
    OrderUpdateEvent,
    StrategyEvent,
)
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import OrderType, Side, TimeInForce
from backtest_engine.types.requirements import EventKind, HistoryRequest, StrategyRequirements
from backtest_engine.types.results import BacktestResult, RunConfig
from backtest_engine.types.strategy import Strategy


class _Run:
    """한 번의 run() 동안만 사는 컴포넌트 묶음. 엔진 인스턴스에 상태를 남기지 않는다."""

    def __init__(
        self,
        config: RunConfig,
        strategy: Strategy,
        requirements: StrategyRequirements,
        slippage: SlippageModel | None,
        max_participation: float | None,
    ) -> None:
        self.strategy = strategy
        self.requirements = requirements
        self.warmup_sessions = max(
            (request.lookback for request in requirements.histories), default=0
        )
        self.declared: frozenset[HistoryRequest] = frozenset(requirements.histories)
        self.history_store = HistoryStore()
        self.portfolio = Portfolio(config.initial_cash)
        self.order_manager = OrderManager()
        self.broker = BrokerSim(config.fee_bps, slippage, max_participation)
        self.router = DecisionRouter(
            requirements.actions, self.order_manager, requirements.features
        )
        self.store = EventStore()
        self.queue = EventQueue()
        self.corporate_actions: dict[datetime, list[CorporateActionEvent]] = defaultdict(list)

    def wants(self, kind: EventKind) -> bool:
        return kind in self.requirements.events


class BacktestEngine:
    def __init__(
        self,
        config: RunConfig,
        capabilities: EngineCapabilities | None = None,
        *,
        slippage: SlippageModel | None = None,
        max_participation: float | None = None,
    ) -> None:
        """
        Args:
            config: 재현에 필요한 실행 설정 (초기 현금, 수수료 등).
            capabilities: 엔진 구현 상태 표. 기본은 reference 엔진.
            slippage: 체결가 슬리피지 모델. 기본 NoSlippage.
            max_participation: 세션 거래량 대비 체결 상한 (0, 1]. None이면 무제한.
                Action의 ExecutionPolicy.max_participation이 있으면 그 값이 우선한다.
        """
        self._config = config
        self._capabilities = (
            capabilities if capabilities is not None else reference_engine_capabilities()
        )
        self._slippage = slippage
        self._max_participation = max_participation
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

    def run(
        self,
        strategy: Strategy,
        feed: DataFeed,
        *,
        corporate_actions: Iterable[CorporateActionEvent] = (),
    ) -> BacktestResult:
        """
        Args:
            strategy: 실행할 전략. requirements()가 먼저 Capability 검증을 통과해야 한다.
            feed: 세션순 MarketSnapshot 공급자.
            corporate_actions: 기간 안의 자본변동 사건. 확인된 분할·병합(SPLIT/REVERSE_SPLIT)은
                사건 세션 시작 시 보유 포지션에 적용되고, 모든 사건은 기록되며 선언한 전략에
                전달된다. 사건 세션에 해당 종목 Bar가 없으면 CorporateActionWithoutBar.
        """
        # 1. Capability 검증은 첫 Bar를 읽기 전, 전략 등록 직후 수행한다.
        validated = prepare_strategy(strategy, self._capabilities)
        run = _Run(
            self._config,
            validated.strategy,
            validated.requirements,
            self._slippage,
            self._max_participation,
        )
        self._event_store = run.store
        for action in corporate_actions:
            run.corporate_actions[action.ts].append(action)
        sessions = set(feed.sessions)
        for ts in sorted(run.corporate_actions):
            if ts not in sessions:
                symbols = [a.instrument.symbol for a in run.corporate_actions[ts]]
                raise CorporateActionWithoutBar(
                    f"corporate action ts is not a feed session — ts={ts} instruments={symbols} "
                    f"sessions={len(sessions)}"
                )

        for snapshot in feed.snapshots():
            run.queue.push(snapshot.ts, EventPriority.MARKET, MarketArrived(snapshot))

        while run.queue:
            event = run.queue.pop()
            match event:
                case MarketArrived(snapshot=snapshot):
                    self._on_market(run, snapshot)
                case FillOccurred(fill=fill, snapshot=snapshot):
                    run.portfolio.apply(fill)
                    run.store.append(fill.ts, RecordKind.FILL, fill)
                case StrategyNotify(event=strategy_event, snapshot=snapshot):
                    self._dispatch(run, strategy_event, snapshot)
                case SessionClose(snapshot=snapshot):
                    self._on_session_close(run, snapshot)
                case OrderPlaced(order=order):
                    run.order_manager.place(order)
                    run.store.append(order.ts, RecordKind.ORDER, order)

        # 남은 주문은 결과에서 조용히 사라지지 않도록 취소로 기록한다. 마지막 세션에 낸
        # DAY/IOC/FOK는 "당일 만료", GTC만 "run 종료"가 사유다.
        remaining_entries = run.order_manager.drain()
        if remaining_entries:
            last_ts = feed.sessions[-1]  # 주문이 있다면 세션도 최소 하나 있다
        for entry in remaining_entries:
            tif = entry.order.time_in_force
            reason = (
                "run ended with GTC order still open — "
                if tif is TimeInForce.GTC
                else f"{tif.value} order expired at last session — "
            )
            run.store.append(
                last_ts,
                RecordKind.ORDER_UPDATE,
                OrderUpdateEvent(
                    ts=last_ts,
                    order_id=entry.order_id,
                    status=OrderStatus.CANCELLED,
                    detail=(
                        f"{reason}instrument={entry.order.instrument.symbol} "
                        f"remaining={entry.remaining}"
                    ),
                ),
            )

        snapshots = run.store.snapshots()
        return BacktestResult(
            run_id=self._config.run_id,
            snapshots=snapshots,
            orders=run.store.orders(),
            fills=run.store.fills(),
            metrics=compute_metrics(snapshots, run.store.fills(), self._config.annualization_days),
        )

    # --- 세션 처리 -----------------------------------------------------------

    def _on_market(self, run: _Run, snapshot: MarketSnapshot) -> None:
        run.history_store.append(snapshot)
        run.store.append(snapshot.ts, RecordKind.MARKET, snapshot)
        order_manager = run.order_manager

        def update(order_id: str, status: OrderStatus, detail: str | None) -> None:
            self._record_update(
                run,
                OrderUpdateEvent(ts=snapshot.ts, order_id=order_id, status=status, detail=detail),
                snapshot,
            )

        # 자본변동은 이 세션의 어떤 체결보다 먼저 적용한다 — 분할 후 가격으로 체결되는
        # 주문이 분할 전 수량과 섞이면 안 된다.
        for action in run.corporate_actions.get(snapshot.ts, ()):
            self._apply_corporate_action(run, action, snapshot, update)

        # 매도 먼저 처리해 매수가 쓸 수 있는 현금을 확정한다 (결정론적 규칙).
        due = sorted(
            order_manager.due(snapshot),
            key=lambda entry: (entry.order.side is not Side.SELL, entry.order_id),
        )
        remaining_cash = run.portfolio.cash
        for entry in due:
            order = entry.order
            outcome = run.broker.execute(
                entry,
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
                run.queue.push(fill.ts, EventPriority.FILL, FillOccurred(fill, snapshot))
                if run.wants(EventKind.FILL):
                    # FILL 알림은 그 체결이 만든 ORDER_UPDATE 알림보다 먼저 큐에 실린다 (인과 순서).
                    # NOTIFY(25) > FILL(20)이라 알림 시점엔 포트폴리오에 이미 반영돼 있다.
                    run.queue.push(fill.ts, EventPriority.NOTIFY, StrategyNotify(fill, snapshot))
                left = order_manager.settle(order.order_id, fill.quantity)
                if left == 0:
                    update(order.order_id, OrderStatus.FILLED, None)
                else:
                    # STOP/STOP_LIMIT이 발동해 일부만 체결됐으면 잔량은 발동 상태를 유지한다.
                    if (
                        order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
                        and not entry.triggered
                    ):
                        order_manager.mark_triggered(order.order_id)
                    update(order.order_id, OrderStatus.PARTIALLY_FILLED, outcome.detail)
            elif outcome.status is ExecutionStatus.TRIGGERED_UNFILLED:
                order_manager.mark_triggered(order.order_id)
                update(
                    order.order_id,
                    OrderStatus.TRIGGERED,
                    f"stop triggered, limit not met — instrument={order.instrument.symbol} "
                    f"stop={order.stop_price} limit={order.limit_price} ts={snapshot.ts}",
                )
            elif (
                outcome.status
                in (
                    ExecutionStatus.REJECTED_NO_CASH,
                    ExecutionStatus.NOT_FILLED,
                )
                and outcome.detail is not None
            ):
                # 여력 0·유동성 0은 이 세션의 사정일 뿐이다 — 주문은 TIF대로 대기하고 사유만 남긴다.
                update(order.order_id, OrderStatus.OPEN, outcome.detail)
            elif outcome.status is ExecutionStatus.FOK_REJECTED:
                order_manager.remove(order.order_id)
                update(order.order_id, OrderStatus.CANCELLED, outcome.detail)

        # DAY/IOC/FOK 주문은 이 세션이 지나면 소멸한다 — bar가 없어 시도조차 못 한 경우 포함.
        for entry in order_manager.open_entries():
            order = entry.order
            if order.time_in_force is TimeInForce.GTC:
                continue
            order_manager.remove(order.order_id)
            if snapshot.has(order.instrument):
                reason = (
                    f"{order.time_in_force.value} order expired unfilled — "
                    f"instrument={order.instrument.symbol} remaining={entry.remaining} "
                    f"ts={snapshot.ts}"
                )
            else:
                reason = (
                    f"no bar for instrument in session — "
                    f"instrument={order.instrument.symbol} ts={snapshot.ts}"
                )
            update(order.order_id, OrderStatus.CANCELLED, reason)

        run.queue.push(snapshot.ts, EventPriority.SESSION_CLOSE, SessionClose(snapshot))

    def _apply_corporate_action(
        self,
        run: _Run,
        action: CorporateActionEvent,
        snapshot: MarketSnapshot,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        if not snapshot.has(action.instrument):
            raise CorporateActionWithoutBar(
                f"corporate action session has no bar for instrument — "
                f"instrument={action.instrument.symbol} ts={action.ts} "
                f"action={action.action_type.value} ratio={action.ratio}"
            )
        run.store.append(action.ts, RecordKind.CORPORATE_ACTION, action)
        for stale in run.order_manager.cancel_for_instrument(action.instrument):
            update(
                stale.order_id,
                OrderStatus.CANCELLED,
                f"cancelled by corporate action — instrument={action.instrument.symbol} "
                f"action={action.action_type.value} ratio={action.ratio} ts={action.ts}",
            )
        if action.action_type in (CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT):
            applied = run.portfolio.apply_corporate_action(
                action, snapshot.bar(action.instrument).open
            )
            if applied is not None:
                run.store.append(action.ts, RecordKind.CORPORATE_ACTION_APPLIED, applied)
        if run.wants(EventKind.CORPORATE_ACTION):
            run.queue.push(action.ts, EventPriority.NOTIFY, StrategyNotify(action, snapshot))

    def _on_session_close(self, run: _Run, snapshot: MarketSnapshot) -> None:
        run.portfolio.mark(snapshot)
        run.store.append(snapshot.ts, RecordKind.SNAPSHOT, run.portfolio.snapshot(snapshot.ts))

        if not calendar.matches(run.requirements.schedule, snapshot.ts):
            return
        self._dispatch(run, snapshot, snapshot)

    # --- 전략 호출 -----------------------------------------------------------

    def _dispatch(self, run: _Run, event: StrategyEvent, market: MarketSnapshot) -> None:
        """전략을 한 번 호출하고 Decision을 라우팅해 주문·상태 변경을 큐에 싣는다."""
        if run.history_store.session_count < run.warmup_sessions:
            return
        ts = market.ts
        portfolio_snapshot = run.portfolio.snapshot(ts)
        context = EngineStrategyContext(
            now=ts,
            snapshot=portfolio_snapshot,
            history_store=run.history_store,
            declared=run.declared,
            open_orders_snapshot=run.order_manager.open_orders(),
        )
        decision = run.strategy.on_event(context, event)
        decision_id = run.order_manager.next_decision_id()
        run.store.append(ts, RecordKind.DECISION, DecisionRecord(decision_id, decision))

        routing = run.router.route(decision, decision_id, portfolio_snapshot, market)
        for update in routing.updates:
            self._record_update(run, update, market)
        for order in routing.orders:
            run.queue.push(order.ts, EventPriority.ORDER, OrderPlaced(order))

    def _record_update(self, run: _Run, update: OrderUpdateEvent, market: MarketSnapshot) -> None:
        run.store.append(update.ts, RecordKind.ORDER_UPDATE, update)
        if run.wants(EventKind.ORDER_UPDATE):
            run.queue.push(update.ts, EventPriority.NOTIFY, StrategyNotify(update, market))
