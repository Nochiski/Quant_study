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

import importlib
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from backtest_engine.capability import (
    EngineCapabilities,
    prepare_strategy,
    reference_engine_capabilities,
)
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine import calendar
from backtest_engine.engine.broker import BrokerSim, ExecutionStatus, Quote, participation_of
from backtest_engine.engine.compact import CompactFill, CompactOrder, CompactOrderUpdate
from backtest_engine.engine.context import (
    EngineStrategyContext,
    HistoryStore,
    PersistentHistoryStore,
    RustStrategyContext,
)
from backtest_engine.engine.core import (
    PERSISTENT_RUST_CORES,
    RUST_CORES,
    BuyingPowerTracker,
    PersistentOrderManager,
    PersistentPortfolio,
    PortfolioLedger,
    RustBuyingPower,
    instrument_key,
    make_buying_power,
    make_persistent_runtime,
    make_portfolio,
    make_pricing,
    make_quote_core,
    slippage_config,
)
from backtest_engine.engine.costs import session_costs
from backtest_engine.engine.metrics import compute_metrics, compute_metrics_from_values
from backtest_engine.engine.orders import BasketGroup, OpenOrder, OrderManager
from backtest_engine.engine.queue import (
    CompactFillOccurred,
    CompactOrderPlaced,
    EventPriority,
    EventQueue,
    FillOccurred,
    MarketArrived,
    OrderPlaced,
    PersistentEventQueue,
    SessionClose,
    StrategyNotify,
)
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.engine.slippage import NoSlippage
from backtest_engine.engine.store import (
    DecisionRecord,
    EventStore,
    PersistentEventStore,
    RecordKind,
)
from backtest_engine.engine.wire import submit_basic_decision, supports_basic_decision
from backtest_engine.errors import (
    CoreUnavailable,
    CorporateActionsNotProvided,
    CorporateActionWithoutBar,
    EquityWipedOut,
    RustCorePanic,
    UndeclaredFeatureUsed,
)
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.ports.universe import UniverseResult
from backtest_engine.types.actions import GroupPolicy
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    FillEvent,
    OrderStatus,
    OrderUpdateEvent,
    StrategyEvent,
)
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import OrderType, Side, TimeInForce
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    HistoryRequest,
    StrategyRequirements,
)
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
        core: str,
    ) -> None:
        self.strategy = strategy
        self.requirements = requirements
        self.warmup_sessions = max(
            (request.lookback for request in requirements.histories), default=0
        )
        self.declared: frozenset[HistoryRequest] = frozenset(requirements.histories)
        self.history_store = HistoryStore()
        self.config = config
        allow_short = EngineFeature.SHORT_SELLING in requirements.features
        allow_margin = EngineFeature.MARGIN in requirements.features
        self.persistent_runtime: Any | None = None
        self.persistent_session_indices: dict[datetime, int] = {}
        if core in PERSISTENT_RUST_CORES:
            persistent_runtime = make_persistent_runtime(
                config.initial_cash,
                allow_short=allow_short,
                allow_margin=allow_margin,
                leverage=config.max_gross_leverage,
            )
            persistent_runtime.configure_router(
                [action.value for action in requirements.actions],
                [feature.value for feature in requirements.features],
            )
            self.persistent_runtime = persistent_runtime
            self.history_store = PersistentHistoryStore(persistent_runtime)
            self.portfolio = PersistentPortfolio(persistent_runtime)
            self.order_manager = PersistentOrderManager(persistent_runtime)
        else:
            self.portfolio: PortfolioLedger = make_portfolio(
                core,
                config.initial_cash,
                allow_short=allow_short,
                allow_margin=allow_margin,
            )
            self.order_manager = OrderManager()
        self.core = core
        self.slippage_model = slippage if slippage is not None else NoSlippage()
        self.max_participation = max_participation
        # Rust 코어는 내장 슬리피지만 지원한다 — 첫 세션이 아니라 run 시작에 거절한다.
        self.rust_slippage = (
            slippage_config(self.slippage_model)
            if core in RUST_CORES
            else None
        )
        self.broker = BrokerSim(
            config.fee_bps,
            slippage,
            max_participation,
            pricing=make_pricing(core),
            quote_core=make_quote_core(core),
        )
        self.router = (
            None
            if core in PERSISTENT_RUST_CORES
            else DecisionRouter(
                requirements.actions, self.order_manager, requirements.features
            )
        )
        self.store = (
            PersistentEventStore(self.persistent_runtime)
            if self.persistent_runtime is not None
            else EventStore()
        )
        self.queue = (
            PersistentEventQueue(self.persistent_runtime)
            if self.persistent_runtime is not None
            else EventQueue()
        )
        self.corporate_actions: dict[datetime, list[CorporateActionEvent]] = defaultdict(list)
        self.universe: UniverseResult | None = None

    def wants(self, kind: EventKind) -> bool:
        return kind in self.requirements.events

    def wants_feature(self, feature: EngineFeature) -> bool:
        return feature in self.requirements.features


class BacktestEngine:
    def __init__(
        self,
        config: RunConfig,
        capabilities: EngineCapabilities | None = None,
        *,
        slippage: SlippageModel | None = None,
        max_participation: float | None = None,
        core: str = "python",
    ) -> None:
        """
        Args:
            config: 재현에 필요한 실행 설정 (초기 현금, 수수료 등).
            capabilities: 엔진 구현 상태 표. 기본은 reference 엔진.
            slippage: 체결가 슬리피지 모델. 기본 NoSlippage.
            max_participation: 세션 거래량 대비 체결 상한 (0, 1]. None이면 무제한.
                Action의 ExecutionPolicy.max_participation이 있으면 그 값이 우선한다.
            core: "python"(기본) 또는 persistent 엔진인 "rust". 전환 호환 alias는
                "rust_persistent", 구 세션 코어는 deprecated "rust_legacy"다.
        """
        self._config = config
        self._capabilities = (
            capabilities if capabilities is not None else reference_engine_capabilities()
        )
        self._slippage = slippage
        self._max_participation = max_participation
        self._core = core
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
        corporate_actions: Iterable[CorporateActionEvent] | None = None,
        universe: UniverseResult | None = None,
    ) -> BacktestResult:
        """
        Args:
            strategy: 실행할 전략. requirements()가 먼저 Capability 검증을 통과해야 한다.
            feed: 세션순 MarketSnapshot 공급자.
            corporate_actions: 기간 안의 자본변동 사건. 확인된 분할·병합(SPLIT/REVERSE_SPLIT)은
                사건 세션 시작 시 보유 포지션에 적용되고, 모든 사건은 기록되며 선언한 전략에
                전달된다. 사건 세션에 해당 종목 Bar가 없으면 CorporateActionWithoutBar.
            universe: 세션별 상장 종목 구간. 주면 ctx.universe()가 그 세션 구성을 돌려준다.
        """
        if self._core == "rust_legacy":
            warnings.warn(
                'core="rust_legacy" and backtest_core.process_market() are deprecated; '
                'use core="rust"',
                DeprecationWarning,
                stacklevel=2,
            )
        # 1. Capability 검증은 첫 Bar를 읽기 전, 전략 등록 직후 수행한다.
        validated = prepare_strategy(strategy, self._capabilities)
        try:
            run = _Run(
                self._config,
                validated.strategy,
                validated.requirements,
                self._slippage,
                self._max_participation,
                self._core,
            )
        except BaseException as error:
            self._raise_if_rust_panic(error, None)
            raise
        self._event_store = run.store
        try:
            return self._execute(run, feed, corporate_actions, universe)
        except BaseException as error:
            self._raise_if_rust_panic(error, run.persistent_runtime)
            raise

    @staticmethod
    def _raise_if_rust_panic(error: BaseException, runtime: Any | None) -> None:
        error_type = type(error)
        if error_type.__name__ != "PanicException" or error_type.__module__ != "pyo3_runtime":
            return
        detail = f"Rust core panic — {error}"
        if runtime is not None:
            try:
                runtime.poison(detail)
            except BaseException:
                # 원래 panic을 안정적인 엔진 예외로 바꾸는 것이 우선이다.
                pass
        raise RustCorePanic(detail) from error

    def _execute(
        self,
        run: _Run,
        feed: DataFeed,
        corporate_actions: Iterable[CorporateActionEvent] | None,
        universe: UniverseResult | None,
    ) -> BacktestResult:
        run.universe = universe
        if run.persistent_runtime is not None:
            self._load_persistent_feed(run, feed)
        if self._config.max_gross_leverage > 1.0 and not run.wants_feature(EngineFeature.MARGIN):
            raise UndeclaredFeatureUsed(
                f"max_gross_leverage={self._config.max_gross_leverage} requires MARGIN feature "
                f"declared in requirements() — run_id={self._config.run_id} "
                f"declared={sorted(f.value for f in run.requirements.features)}"
            )
        if corporate_actions is None:
            if run.wants(EventKind.CORPORATE_ACTION):
                raise CorporateActionsNotProvided(
                    f"strategy declared EventKind.CORPORATE_ACTION but run() got no "
                    f"corporate_actions — run_id={self._config.run_id}; pass the port result "
                    f"(possibly an empty tuple) explicitly"
                )
            corporate_actions = ()
        # 사건은 해당 종목이 실제로 거래되는 첫 세션(사건 세션 이후)에 적용한다 — 원장의
        # 분할 세션이 거래정지 행이라 feed에서 빠지는 경우 다음 거래일 시가로 정산한다.
        for action in corporate_actions:
            if run.persistent_runtime is None:
                settle_ts = self._settlement_session(feed, action)
            else:
                session_index = run.persistent_runtime.settlement_session_index(
                    instrument_key(action.instrument), str(action.ts)
                )
                if session_index is None:
                    raise CorporateActionWithoutBar(
                        f"no traded session for instrument at or after corporate action — "
                        f"instrument={action.instrument.symbol} ts={action.ts} "
                        f"action={action.action_type.value} ratio={action.ratio} "
                        f"feed_sessions={len(feed)} "
                        f"last_session={feed.sessions[-1] if len(feed) else None}"
                    )
                settle_ts = feed.sessions[session_index]
            run.corporate_actions[settle_ts].append(action)

        for snapshot in feed.snapshots():
            run.queue.push(snapshot.ts, EventPriority.MARKET, MarketArrived(snapshot))

        while run.queue:
            event = run.queue.pop()
            match event:
                case MarketArrived(snapshot=snapshot):
                    self._on_market(run, snapshot)
                case FillOccurred(fill=fill, snapshot=snapshot):
                    if run.persistent_runtime is None:
                        run.portfolio.apply(fill)
                    run.store.append(fill.ts, RecordKind.FILL, fill)
                case CompactFillOccurred(fill=fill, snapshot=_snapshot):
                    if not isinstance(run.store, PersistentEventStore):
                        raise RuntimeError("compact fill reached a non-persistent event store")
                    run.store.append(fill.ts, RecordKind.FILL, fill)
                case StrategyNotify(event=strategy_event, snapshot=snapshot):
                    self._dispatch(run, strategy_event, snapshot)
                case SessionClose(snapshot=snapshot):
                    self._on_session_close(run, snapshot)
                case OrderPlaced(order=order):
                    run.order_manager.place(order)
                    run.store.append(order.ts, RecordKind.ORDER, order)
                case CompactOrderPlaced(order=order):
                    if not isinstance(run.order_manager, PersistentOrderManager) or not isinstance(
                        run.store, PersistentEventStore
                    ):
                        raise RuntimeError("compact order reached a non-persistent runtime")
                    run.order_manager.place_compact(order)
                    run.store.append(order.ts, RecordKind.ORDER, order)

        # 남은 주문은 결과에서 조용히 사라지지 않도록 취소로 기록한다. 마지막 세션에 낸
        # DAY/IOC/FOK는 "당일 만료", GTC만 "run 종료"가 사유다.
        if isinstance(run.order_manager, PersistentOrderManager):
            if not isinstance(run.store, PersistentEventStore):
                raise RuntimeError("persistent order manager requires its compact event store")
            remaining_compact = run.order_manager.drain_compact()
            if remaining_compact:
                last_ts = feed.sessions[-1]  # 주문이 있다면 세션도 최소 하나 있다
            for order, remaining, _triggered in remaining_compact:
                tif = order.time_in_force
                reason = (
                    "run ended with GTC order still open — "
                    if tif is TimeInForce.GTC
                    else f"{tif.value} order expired at last session — "
                )
                run.store.append(
                    last_ts,
                    RecordKind.ORDER_UPDATE,
                    CompactOrderUpdate(
                        ts=last_ts,
                        order_id=order.order_id,
                        status=OrderStatus.CANCELLED,
                        detail=(
                            f"{reason}instrument={order.instrument.symbol} "
                            f"remaining={remaining}"
                        ),
                    ),
                )
        else:
            remaining_entries = run.order_manager.drain()
            if remaining_entries:
                last_ts = feed.sessions[-1]
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

        if isinstance(run.store, PersistentEventStore):
            run.store.finish()

        snapshots = run.store.snapshots()
        if isinstance(run.store, PersistentEventStore):
            return BacktestResult.lazy(
                run_id=self._config.run_id,
                snapshots=snapshots,
                orders=run.store.orders,
                fills=run.store.fills,
                metrics=compute_metrics_from_values(
                    tuple(snapshot.equity for snapshot in snapshots),
                    run.store.traded_notional(),
                    self._config.annualization_days,
                ),
            )
        return BacktestResult(
            run_id=self._config.run_id,
            snapshots=snapshots,
            orders=run.store.orders(),
            fills=run.store.fills(),
            metrics=compute_metrics(snapshots, run.store.fills(), self._config.annualization_days),
        )

    # --- 세션 처리 -----------------------------------------------------------

    @staticmethod
    def _load_persistent_feed(run: _Run, feed: DataFeed) -> None:
        runtime = run.persistent_runtime
        portfolio = run.portfolio
        if runtime is None or not isinstance(portfolio, PersistentPortfolio):
            raise CoreUnavailable("persistent Rust feed requires its runtime and portfolio")
        registry: dict[object, int] = {}
        instruments = []
        keys: list[str] = []
        symbols: list[str] = []
        sessions: list[str] = []
        offsets = [0]
        instrument_ids: list[int] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        volumes: list[int] = []
        for session_index, snapshot in enumerate(feed.snapshots()):
            run.persistent_session_indices[snapshot.ts] = session_index
            sessions.append(str(snapshot.ts))
            for bar in snapshot.bars:
                instrument_id = registry.get(bar.instrument)
                if instrument_id is None:
                    instrument_id = len(instruments)
                    registry[bar.instrument] = instrument_id
                    instruments.append(bar.instrument)
                    keys.append(instrument_key(bar.instrument))
                    symbols.append(bar.instrument.symbol)
                instrument_ids.append(instrument_id)
                opens.append(bar.open)
                highs.append(bar.high)
                lows.append(bar.low)
                closes.append(bar.close)
                volumes.append(bar.volume)
            offsets.append(len(instrument_ids))
        runtime.load_feed(
            keys,
            symbols,
            sessions,
            offsets,
            instrument_ids,
            opens,
            highs,
            lows,
            closes,
            volumes,
        )
        portfolio.register_instruments(tuple(instruments))

    def _on_market(self, run: _Run, snapshot: MarketSnapshot) -> None:
        run.history_store.append(snapshot)
        run.store.append(snapshot.ts, RecordKind.MARKET, snapshot)
        order_manager = run.order_manager

        def update(order_id: str, status: OrderStatus, detail: str | None) -> None:
            if isinstance(run.store, PersistentEventStore):
                self._record_compact_update(
                    run,
                    CompactOrderUpdate(
                        ts=snapshot.ts, order_id=order_id, status=status, detail=detail
                    ),
                    snapshot,
                )
            else:
                self._record_update(
                    run,
                    OrderUpdateEvent(
                        ts=snapshot.ts, order_id=order_id, status=status, detail=detail
                    ),
                    snapshot,
                )

        # 자본변동은 이 세션의 어떤 체결보다 먼저 적용한다 — 분할 후 가격으로 체결되는
        # 주문이 분할 전 수량과 섞이면 안 된다.
        if isinstance(order_manager, PersistentOrderManager):
            # 이전 세션 ORDER priority에서 공개된 주문을 일괄 활성화한다. 자본변동 취소가
            # 해당 주문까지 볼 수 있어야 하므로 corporate action 처리보다 앞선다.
            order_manager.activate_pending()
        for action in run.corporate_actions.get(snapshot.ts, ()):
            self._apply_corporate_action(run, action, snapshot, update)

        if run.persistent_runtime is not None:
            self._on_market_persistent(run, snapshot, update)
            run.queue.push(snapshot.ts, EventPriority.SESSION_CLOSE, SessionClose(snapshot))
            return

        if run.core == "rust_legacy":
            self._on_market_rust(run, snapshot, update)
            run.queue.push(snapshot.ts, EventPriority.SESSION_CLOSE, SessionClose(snapshot))
            return

        # 매도 먼저 처리해 매수가 쓸 수 있는 현금을 확정한다 (결정론적 규칙).
        due = sorted(
            order_manager.due(snapshot),
            key=lambda entry: (entry.order.side is not Side.SELL, entry.order_id),
        )
        power = make_buying_power(
            run.core, run.portfolio.snapshot(snapshot.ts), run.config.max_gross_leverage
        )
        # 바스켓 그룹은 leg를 함께 견적해 정책을 판정한 뒤 체결한다 (단일 주문보다 먼저).
        for group in order_manager.open_groups():
            self._process_group(run, group, snapshot, power, update)
        for entry in due:
            order = entry.order
            quote = run.broker.quote(
                entry,
                snapshot.bar(order.instrument),
                power.available,
                held=power.quantity_of(order.instrument),
            )
            if quote.quantity > 0:
                self._apply_quote(run, entry, quote, quote.quantity, snapshot, power, update)
            elif quote.status is ExecutionStatus.TRIGGERED_UNFILLED:
                order_manager.mark_triggered(order.order_id)
                update(
                    order.order_id,
                    OrderStatus.TRIGGERED,
                    f"stop triggered, limit not met — instrument={order.instrument.symbol} "
                    f"stop={order.stop_price} limit={order.limit_price} ts={snapshot.ts}",
                )
            elif (
                quote.status
                in (
                    ExecutionStatus.REJECTED_NO_CASH,
                    ExecutionStatus.NOT_FILLED,
                )
                and quote.detail is not None
            ):
                # 여력 0·유동성 0은 이 세션의 사정일 뿐이다 — 주문은 TIF대로 대기하고 사유만 남긴다.
                update(order.order_id, OrderStatus.OPEN, quote.detail)
            elif quote.status is ExecutionStatus.FOK_REJECTED:
                order_manager.remove(order.order_id)
                update(order.order_id, OrderStatus.CANCELLED, quote.detail)

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

    def _on_market_rust(
        self,
        run: _Run,
        snapshot: MarketSnapshot,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        """세션 MARKET 처리를 Rust `process_market`에 맡기고 계획(ops)을 순서대로 적용한다."""
        core = importlib.import_module("backtest_core")
        order_manager = run.order_manager
        power = make_buying_power(
            run.core, run.portfolio.snapshot(snapshot.ts), run.config.max_gross_leverage
        )
        if not isinstance(power, RustBuyingPower):  # 코어가 rust면 항상 Rust 누산기다
            raise CoreUnavailable("rust session core requires the rust buying-power tracker")
        entries = []
        for entry in order_manager.open_entries():
            order = entry.order
            override = participation_of(order)
            entries.append(
                (
                    order.order_id,
                    instrument_key(order.instrument),
                    order.instrument.symbol,
                    order.side.value,
                    order.order_type.value,
                    (
                        None if order.limit_price is None else float(order.limit_price),
                        None if order.stop_price is None else float(order.stop_price),
                        None if order.limit_price is None else str(order.limit_price),
                        None if order.stop_price is None else str(order.stop_price),
                    ),
                    order.time_in_force.value,
                    int(entry.remaining),
                    entry.triggered,
                    order.group_id,
                    None if override is None else str(override),
                )
            )
        groups = [
            (g.group_id, g.policy.value, list(g.order_ids)) for g in order_manager.open_groups()
        ]
        bars = {
            instrument_key(bar.instrument): (bar.open, bar.high, bar.low, bar.volume)
            for bar in snapshot.bars
        }
        default = None if run.max_participation is None else str(run.max_participation)
        ops = core.process_market(
            str(snapshot.ts),
            entries,
            groups,
            bars,
            power.inner,
            run.broker.fee_rate,
            default,
            run.rust_slippage,
        )
        for kind, order_id, quantity, price, slip, fee, payload in ops:
            match kind:
                case "fill":
                    entry = order_manager.get(order_id)
                    if entry is None:
                        raise RuntimeError(
                            f"rust core filled an order that is not open — order_id={order_id}"
                        )
                    fill = FillEvent(
                        fill_id=order_manager.next_fill_id(),
                        order_id=order_id,
                        ts=snapshot.ts,
                        instrument=entry.order.instrument,
                        quantity=Decimal(quantity),
                        side=entry.order.side,
                        price=price,
                        fee=fee,
                        slippage_per_share=slip,
                    )
                    run.queue.push(fill.ts, EventPriority.FILL, FillOccurred(fill, snapshot))
                    if run.wants(EventKind.FILL):
                        run.queue.push(
                            fill.ts, EventPriority.NOTIFY, StrategyNotify(fill, snapshot)
                        )
                    order_manager.settle(order_id, fill.quantity)
                case "update":
                    status_text, _, detail = payload.partition("|")
                    update(order_id, OrderStatus(status_text), detail or None)
                case "trigger":
                    order_manager.mark_triggered(order_id)
                case "remove":
                    order_manager.remove(order_id)
                case "drop_group":
                    order_manager.drop_group(order_id)
                case _:
                    raise RuntimeError(f"unknown op from rust core — kind={kind!r}")

    def _on_market_persistent(
        self,
        run: _Run,
        snapshot: MarketSnapshot,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        """Rust runtime 내부의 persistent 주문·그룹 상태로 한 세션을 처리한다."""
        runtime = run.persistent_runtime
        order_manager = run.order_manager
        if runtime is None or not isinstance(order_manager, PersistentOrderManager):
            raise CoreUnavailable("persistent rust core requires its runtime and order manager")
        default = None if run.max_participation is None else str(run.max_participation)
        ops = runtime.process_market_index(
            run.persistent_session_indices[snapshot.ts],
            run.broker.fee_rate,
            default,
            run.rust_slippage,
        )
        for kind, order_id, quantity, price, slip, fee, payload in ops:
            match kind:
                case "fill":
                    order = order_manager.order_compact(order_id)
                    fill = CompactFill(
                        fill_id=payload,
                        order_id=order_id,
                        ts=snapshot.ts,
                        instrument=order.instrument,
                        quantity=quantity,
                        side=order.side,
                        price=price,
                        fee=fee,
                        slippage_per_share=slip,
                    )
                    run.queue.push(
                        fill.ts, EventPriority.FILL, CompactFillOccurred(fill, snapshot)
                    )
                    if run.wants(EventKind.FILL):
                        run.queue.push(
                            fill.ts,
                            EventPriority.NOTIFY,
                            StrategyNotify(fill.materialize(), snapshot),
                        )
                case "update":
                    status_text, _, detail = payload.partition("|")
                    update(order_id, OrderStatus(status_text), detail or None)
                case "trigger" | "remove" | "drop_group":
                    # mutable 상태는 process_market 안에서 이미 Rust runtime에 적용됐다.
                    pass
                case _:
                    raise RuntimeError(
                        f"unknown op from persistent rust core — kind={kind!r}"
                    )

    @staticmethod
    def _settlement_session(feed: DataFeed, action: CorporateActionEvent) -> datetime:
        for snapshot in feed.snapshots():
            if snapshot.ts >= action.ts and snapshot.has(action.instrument):
                return snapshot.ts
        raise CorporateActionWithoutBar(
            f"no traded session for instrument at or after corporate action — "
            f"instrument={action.instrument.symbol} ts={action.ts} "
            f"action={action.action_type.value} ratio={action.ratio} "
            f"feed_sessions={len(feed)} last_session={feed.sessions[-1] if len(feed) else None}"
        )

    def _apply_quote(
        self,
        run: _Run,
        entry: OpenOrder,
        quote: Quote,
        quantity: Decimal,
        snapshot: MarketSnapshot,
        power: BuyingPowerTracker,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        """견적을 실제 체결로 확정한다: Fill 큐 적재, 잔량 갱신, 상태 기록, 여력 소모."""
        order = entry.order
        fill = run.broker.fill(
            quote,
            entry,
            snapshot.bar(order.instrument),
            run.order_manager.next_fill_id(),
            quantity,
        )
        power.consume(fill)
        run.queue.push(fill.ts, EventPriority.FILL, FillOccurred(fill, snapshot))
        if run.wants(EventKind.FILL):
            # FILL 알림은 그 체결이 만든 ORDER_UPDATE 알림보다 먼저 큐에 실린다 (인과 순서).
            # NOTIFY(25) > FILL(20)이라 알림 시점엔 포트폴리오에 이미 반영돼 있다.
            run.queue.push(fill.ts, EventPriority.NOTIFY, StrategyNotify(fill, snapshot))
        left = run.order_manager.settle(order.order_id, fill.quantity)
        if left == 0:
            update(order.order_id, OrderStatus.FILLED, None)
        else:
            # STOP/STOP_LIMIT이 발동해 일부만 체결됐으면 잔량은 발동 상태를 유지한다.
            if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and not entry.triggered:
                run.order_manager.mark_triggered(order.order_id)
            update(order.order_id, OrderStatus.PARTIALLY_FILLED, quote.detail)

    def _process_group(
        self,
        run: _Run,
        group: BasketGroup,
        snapshot: MarketSnapshot,
        power: BuyingPowerTracker,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        order_manager = run.order_manager
        entries = order_manager.group_entries(group.group_id)
        policy = group.policy

        def cancel_all(reason: str) -> None:
            for entry in entries:
                order_manager.remove(entry.order_id)
                update(
                    entry.order_id,
                    OrderStatus.CANCELLED,
                    f"basket {policy.value} — {reason} group_id={group.group_id} "
                    f"remaining={entry.remaining} ts={snapshot.ts}",
                )
            order_manager.drop_group(group.group_id)

        missing = [
            e.order.instrument.symbol for e in entries if not snapshot.has(e.order.instrument)
        ]
        if missing and policy is not GroupPolicy.BEST_EFFORT:
            cancel_all(f"leg without bar in session instruments={missing}")
            return
        if policy is not GroupPolicy.BEST_EFFORT and len(entries) < len(group.order_ids):
            # 라우팅 이후 leg가 취소·정정·자본변동으로 사라졌다 — 남은 leg만으로 판정하면
            # "한쪽만 체결" 사고가 된다.
            gone = sorted(set(group.order_ids) - {e.order_id for e in entries})
            cancel_all(f"leg(s) no longer open before group execution missing={gone}")
            return

        # 1) 견적 패스: 매도 leg 먼저, 매도 대금이 매수 leg 여력에 반영되도록 순차 소모.
        legs = sorted(
            (e for e in entries if snapshot.has(e.order.instrument)),
            key=lambda e: (e.order.side is not Side.SELL, e.order_id),
        )
        checkpoint = power.checkpoint()
        quotes: list[tuple[OpenOrder, Quote]] = []
        for entry in legs:
            quote = run.broker.quote(
                entry,
                snapshot.bar(entry.order.instrument),
                power.available,
                held=power.quantity_of(entry.order.instrument),
            )
            quotes.append((entry, quote))
            if quote.quantity > 0:
                order = entry.order
                notional = float(quote.quantity) * quote.price
                power.consume_quantity(
                    order.instrument,
                    order.side,
                    quote.quantity,
                    quote.price,
                    run.broker.fee_for(notional),
                )
        power.restore(checkpoint)

        # 2) 정책 판정 후 실제 체결 (여력은 실제 체결로 다시 소모).
        if policy is GroupPolicy.BEST_EFFORT:
            for entry, quote in quotes:
                if quote.quantity > 0:
                    self._apply_quote(run, entry, quote, quote.quantity, snapshot, power, update)
            # 잔량이 남은 leg(GTC)는 다음 세션에도 그룹 경로로 재시도한다 — 그룹을 버리면
            # due()에서도 open_groups()에서도 보이지 않아 영영 체결되지 않는다.
            if not order_manager.group_entries(group.group_id):
                order_manager.drop_group(group.group_id)
            return

        if policy is GroupPolicy.ALL_OR_NONE:
            short = [
                f"{e.order.instrument.symbol}:{q.quantity}/{e.remaining}"
                for e, q in quotes
                if q.quantity < e.remaining
            ]
            if short:
                cancel_all(f"not fillable in full legs={short}")
                return
            for entry, quote in quotes:
                self._apply_quote(run, entry, quote, quote.quantity, snapshot, power, update)
            order_manager.drop_group(group.group_id)
            return

        # PROPORTIONAL: 가장 낮은 체결 비율 leg(j)에 맞춰 전 leg 축소, 잔량은 취소.
        # 비율을 Decimal로 굴리면 1/3 같은 값이 제약 leg 자신을 0주로 만든다 — 정수 교차곱으로
        # floor(remaining_i × q_j / remaining_j)를 정확히 계산한다.
        tight_entry, tight_quote = min(
            quotes, key=lambda item: item[1].quantity / item[0].remaining
        )
        if tight_quote.quantity <= 0:
            cancel_all("no leg fillable")
            return
        scale = tight_quote.quantity / tight_entry.remaining
        tight_remaining = tight_entry.remaining
        # 체결이 remaining을 갱신하므로 수량은 전부 먼저 확정한다.
        planned = [
            (entry, quote, (entry.remaining * tight_quote.quantity) // tight_remaining)
            for entry, quote in quotes
        ]
        for entry, quote, quantity in planned:
            if quantity > 0:
                self._apply_quote(run, entry, quote, quantity, snapshot, power, update)
        for entry in order_manager.group_entries(group.group_id):
            order_manager.remove(entry.order_id)
            update(
                entry.order_id,
                OrderStatus.CANCELLED,
                f"basket proportional remainder cancelled — scale={scale:.6f} "
                f"group_id={group.group_id} remaining={entry.remaining} ts={snapshot.ts}",
            )
        order_manager.drop_group(group.group_id)

    def _apply_corporate_action(
        self,
        run: _Run,
        action: CorporateActionEvent,
        snapshot: MarketSnapshot,
        update: Callable[[str, OrderStatus, str | None], None],
    ) -> None:
        # _settlement_session이 bar 존재를 보장한다. 기록·적용 시각은 정산 세션이다.
        run.store.append(snapshot.ts, RecordKind.CORPORATE_ACTION, action)
        confirmed = action.action_type in (
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
        )
        if isinstance(run.order_manager, PersistentOrderManager):
            remaining_by_id = {
                order.order_id: Decimal(remaining)
                for order, remaining, _triggered in run.order_manager.compact_open_entries()
            }
        else:
            remaining_by_id = {
                entry.order_id: entry.remaining for entry in run.order_manager.open_entries()
            }
        # 가격 수준이 무의미해지는 확인된 분할·병합만 대기 주문을 취소한다 (스펙 결정 3).
        if isinstance(run.order_manager, PersistentOrderManager):
            stale_orders = (
                run.order_manager.cancel_compact_for_instrument(action.instrument)
                if confirmed
                else ()
            )
        else:
            stale_orders = (
                run.order_manager.cancel_for_instrument(action.instrument) if confirmed else ()
            )
        for stale in stale_orders:
            stale_remaining = remaining_by_id[stale.order_id]
            update(
                stale.order_id,
                OrderStatus.CANCELLED,
                f"cancelled by corporate action — instrument={action.instrument.symbol} "
                f"action={action.action_type.value} ratio={action.ratio} "
                f"event_ts={action.ts} settled_at={snapshot.ts} remaining={stale_remaining}",
            )
        if confirmed:
            applied = run.portfolio.apply_corporate_action(
                action, snapshot.bar(action.instrument).open, settled_at=snapshot.ts
            )
            if applied is not None:
                run.store.append(snapshot.ts, RecordKind.CORPORATE_ACTION_APPLIED, applied)
        if run.wants(EventKind.CORPORATE_ACTION):
            run.queue.push(snapshot.ts, EventPriority.NOTIFY, StrategyNotify(action, snapshot))

    def _on_session_close(self, run: _Run, snapshot: MarketSnapshot) -> None:
        if isinstance(run.portfolio, PersistentPortfolio):
            should_dispatch, costs, marked = run.portfolio.close_session(
                snapshot.ts, run.config, run.requirements.schedule
            )
        else:
            run.portfolio.mark(snapshot)
            # 세션 종료 평가 상태에서 차입·이자 비용을 발생시킨 뒤 자본 잠식을 검사한다.
            costs = session_costs(run.portfolio.snapshot(snapshot.ts), run.config)
            for cost in costs:
                run.portfolio.charge(cost)
            marked = run.portfolio.snapshot(snapshot.ts)
            should_dispatch = calendar.matches(run.requirements.schedule, snapshot.ts)
        for cost in costs:
            run.store.append(cost.ts, RecordKind.COST, cost)
        if marked.equity < 0:
            raise EquityWipedOut(
                f"equity fell below zero at session close — ts={snapshot.ts} "
                f"equity={marked.equity} cash={marked.cash} "
                f"positions={[(p.instrument.symbol, str(p.quantity)) for p in marked.positions]}"
            )
        run.store.append(snapshot.ts, RecordKind.SNAPSHOT, marked)

        if not should_dispatch:
            return
        self._dispatch(run, snapshot, snapshot, portfolio_snapshot=marked)

    # --- 전략 호출 -----------------------------------------------------------

    def _dispatch(
        self,
        run: _Run,
        event: StrategyEvent,
        market: MarketSnapshot,
        *,
        portfolio_snapshot: PortfolioSnapshot | None = None,
    ) -> None:
        """전략을 한 번 호출하고 Decision을 라우팅해 주문·상태 변경을 큐에 싣는다."""
        if run.history_store.session_count < run.warmup_sessions:
            return
        ts = market.ts
        if portfolio_snapshot is None:
            portfolio_snapshot = run.portfolio.snapshot(ts)
        if isinstance(run.order_manager, PersistentOrderManager):
            open_orders_snapshot = ()
            compact_open_orders = run.order_manager.compact_open_orders()
        else:
            open_orders_snapshot = run.order_manager.open_orders()
            compact_open_orders = ()
        callback_frame = None
        if run.persistent_runtime is None:
            context = EngineStrategyContext(
                now=ts,
                snapshot=portfolio_snapshot,
                history_store=run.history_store,
                declared=run.declared,
                open_orders_snapshot=open_orders_snapshot,
                universe_source=run.universe,
            )
        else:
            callback_frame = run.persistent_runtime.run_until_callback(
                self._callback_kind(event), run.persistent_session_indices[ts]
            )
            context = RustStrategyContext(
                now=ts,
                snapshot=portfolio_snapshot,
                history_store=run.history_store,
                declared=run.declared,
                open_orders_snapshot=open_orders_snapshot,
                universe_source=run.universe,
                callback_token=callback_frame.token,
                compact_open_orders=compact_open_orders,
            )
        try:
            decision = run.strategy.on_event(context, event)
        except BaseException as error:
            if callback_frame is not None:
                assert run.persistent_runtime is not None
                run.persistent_runtime.fail_callback(
                    callback_frame.token, f"{type(error).__name__}: {error}"
                )
            raise
        persistent_route = None
        if run.persistent_runtime is not None:
            assert callback_frame is not None
            if not supports_basic_decision(decision):
                run.persistent_runtime.fail_callback(
                    callback_frame.token,
                    f"unsupported strategy decision — type={type(decision).__name__}",
                )
                raise RuntimeError(
                    "persistent Rust router does not support a returned strategy action"
                )
            persistent_route = submit_basic_decision(
                run.persistent_runtime,
                callback_frame.token,
                decision,
                portfolio_snapshot,
                market,
            )
            decision_id = persistent_route.decision_id
        else:
            decision_id = run.order_manager.next_decision_id()
        run.store.record_callback(event, decision_id, decision)
        run.store.append(ts, RecordKind.DECISION, DecisionRecord(decision_id, decision))

        if persistent_route is not None:
            if persistent_route.error is not None:
                raise persistent_route.error
            routing = persistent_route.routing
        else:
            if run.router is None:
                raise RuntimeError(
                    "persistent Rust router does not support a returned strategy action"
                )
            routing = run.router.route(decision, decision_id, portfolio_snapshot, market)
        for update in routing.updates:
            if isinstance(update, CompactOrderUpdate):
                self._record_compact_update(run, update, market)
            else:
                self._record_update(run, update, market)
        for group in routing.groups:
            run.order_manager.register_group(group)
        for order in routing.orders:
            if isinstance(order, CompactOrder):
                run.queue.push(order.ts, EventPriority.ORDER, CompactOrderPlaced(order))
            else:
                run.queue.push(order.ts, EventPriority.ORDER, OrderPlaced(order))

    @staticmethod
    def _callback_kind(event: StrategyEvent) -> str:
        if isinstance(event, MarketSnapshot):
            return "market"
        if isinstance(event, FillEvent):
            return "fill"
        if isinstance(event, OrderUpdateEvent):
            return "order_update"
        if isinstance(event, CorporateActionEvent):
            return "corporate_action"
        raise TypeError(f"unsupported strategy callback event — got {type(event).__name__}")

    def _record_update(self, run: _Run, update: OrderUpdateEvent, market: MarketSnapshot) -> None:
        run.store.append(update.ts, RecordKind.ORDER_UPDATE, update)
        if run.wants(EventKind.ORDER_UPDATE):
            run.queue.push(update.ts, EventPriority.NOTIFY, StrategyNotify(update, market))

    def _record_compact_update(
        self, run: _Run, update: CompactOrderUpdate, market: MarketSnapshot
    ) -> None:
        if not isinstance(run.store, PersistentEventStore):
            raise RuntimeError("compact update reached a non-persistent event store")
        run.store.append(update.ts, RecordKind.ORDER_UPDATE, update)
        if run.wants(EventKind.ORDER_UPDATE):
            run.queue.push(
                update.ts,
                EventPriority.NOTIFY,
                StrategyNotify(update.materialize(), market),
            )
