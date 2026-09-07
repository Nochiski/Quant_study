from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.ports.universe import Membership, UniverseResult
from backtest_engine.types.actions import PositionTarget, QuantityTarget
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    FillEvent,
    StrategyEvent,
)
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.requirements import StrategyRequirements
from backtest_engine.types.strategy import StrategyContext
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestExecutionRequest,
    CancellationCheck,
    ProgressCallback,
    RunCancelledError,
)
from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    MetricRegistry,
    TradeOutcome,
    build_default_metric_registry,
    compute_analytics,
    unavailable_metric_values,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestSeries,
    RawArtifactBundle,
    RawCost,
    RawFill,
    RawOrder,
    RawPosition,
    RawSnapshot,
    RawTrade,
    RunManifest,
    backtest_run_fingerprint,
)
from strategy_workbench.domain.portfolio.facade.construction import TargetTape
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


def _instrument(security_id: str) -> InstrumentId:
    return InstrumentId("XKRX", security_id, AssetClass.EQUITY, "KRW")


class TargetTapeStrategy:
    def __init__(
        self,
        spec: StrategySpec,
        tape: TargetTape,
        portfolio_bridge: BacktestEnginePortfolioAdapter,
    ) -> None:
        self._spec = spec
        self._frames = {frame.signal_as_of: frame for frame in tape.frames}
        self._bridge = portfolio_bridge

    def requirements(self) -> StrategyRequirements:
        return self._bridge.requirements(self._spec)

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        if not isinstance(event, MarketSnapshot):
            return StrategyDecision.no_action(ctx.now, "target_tape_idle")
        frame = self._frames.get(event.ts.date())
        if frame is None:
            return StrategyDecision.no_action(ctx.now, "target_tape_idle")
        action = self._bridge.to_target_action(
            frame,
            max_participation=self._spec.execution.participation_rate,
        )
        targets, untradable = _tradable_targets(action.targets, event, ctx)
        reason = f"target_tape:{frame.signal_as_of.isoformat()}"
        if untradable:
            reason += f" no_bar={untradable}"
        return StrategyDecision.of(ctx.now, replace(action, targets=targets), reason)


def _tradable_targets(
    targets: tuple[PositionTarget, ...], snapshot: MarketSnapshot, ctx: StrategyContext
) -> tuple[tuple[PositionTarget, ...], tuple[str, ...]]:
    """The kernel sizes a weight target off this session's close, so a target whose instrument
    has no bar today (trading halt, reference-price session — equity feeds omit those rows)
    cannot be routed: it is held at its current quantity when held, and skipped when not. The
    skipped budget stays in cash until the next frame; the symbols are reported in the decision
    reason so the run manifest keeps the trace (KRX halts are routine, e.g. 005930 2018-04-30).
    """
    kept: list[PositionTarget] = []
    untradable: list[str] = []
    for target in targets:
        if snapshot.has(target.instrument):
            kept.append(target)
            continue
        untradable.append(target.instrument.symbol)
        held = ctx.position_qty(target.instrument)
        if held != 0:
            kept.append(QuantityTarget(instrument=target.instrument, quantity=held))
    return tuple(kept), tuple(untradable)


class BacktestEngineExecutorAdapter:
    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self._registry = registry or build_default_metric_registry()
        self._portfolio_bridge = BacktestEnginePortfolioAdapter()

    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult:
        if request.dataset.data_snapshot_id != request.target_tape.data_snapshot_id:
            raise ValueError("dataset snapshot does not match the compiled target tape")
        strategy = request.spec.strategy
        if strategy is None:
            raise ValueError(
                "execution request must carry a resolved strategy — "
                f"run_id={request.run_id} provenance={request.strategy_provenance.kind.value}"
            )
        self._check_cancelled(cancelled)
        started_at = datetime.now(UTC)
        progress(0.35, "engine.prepare", "Preparing market feed and strategy")
        bars = tuple(
            Bar(
                ts=datetime.combine(item.session, time(15, 30)),
                instrument=_instrument(item.security_id),
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                volume=item.volume,
            )
            for item in request.dataset.bars
        )
        universe = UniverseResult(
            memberships=tuple(
                Membership(
                    _instrument(item.security_id),
                    item.first_session,
                    item.last_session,
                )
                for item in request.dataset.memberships
            ),
            status=LoadStatus.OK,
        )
        corporate_actions = tuple(
            CorporateActionEvent(
                ts=datetime.combine(item.session, time(15, 30)),
                instrument=_instrument(item.security_id),
                action_type=CorporateActionType(item.action_type),
                ratio=Decimal(item.ratio),
                detail=item.detail,
            )
            for item in request.dataset.corporate_actions
        )
        engine = BacktestEngine(
            RunConfig(
                run_id=request.run_id,
                initial_cash=request.spec.initial_cash,
                fee_bps=strategy.execution.fee_bps,
                annualization_days=request.spec.annualization_days,
                max_gross_leverage=max(1.0, strategy.risk.gross_exposure),
            ),
            slippage=FixedBpsSlippage(strategy.execution.slippage_bps),
            max_participation=strategy.execution.participation_rate,
            core=request.spec.core.value,
        )
        result = engine.run(
            TargetTapeStrategy(strategy, request.target_tape, self._portfolio_bridge),
            DataFeed(bars),
            corporate_actions=corporate_actions,
            universe=universe,
        )
        self._check_cancelled(cancelled)
        progress(0.78, "analytics", "Calculating professional metrics")
        costs = engine.event_store.costs()
        artifacts, outcomes = _artifacts(result, costs)
        benchmark = _benchmark_values(request)
        points = tuple(
            AnalysisPoint(
                session=snapshot.ts.date(),
                equity=snapshot.equity,
                gross_exposure=snapshot.gross_exposure,
                net_exposure=(
                    sum(position.market_value for position in snapshot.positions) / snapshot.equity
                    if snapshot.equity != 0
                    else 0.0
                ),
                benchmark_equity=benchmark.get(snapshot.ts.date()),
            )
            for snapshot in result.snapshots
        )
        full_input = AnalyticsInput(
            points=points,
            traded_notional=sum(float(fill.quantity) * fill.price for fill in result.fills),
            trades=outcomes,
            total_fees=sum(fill.fee for fill in result.fills),
            total_slippage_cost=sum(
                float(fill.quantity) * abs(fill.slippage_per_share) for fill in result.fills
            ),
            total_carry_cost=sum(item.amount for item in costs),
        )
        full = compute_analytics(
            full_input,
            self._registry,
            annualization_days=request.spec.annualization_days,
        )
        _assert_legacy_metric_parity(result.metrics, full.metrics)
        metrics = list(full.metrics)
        for window in request.spec.metric_windows:
            window_input = _slice_analytics(full_input, artifacts, window.start, window.end)
            if window_input.points:
                metrics.extend(
                    compute_analytics(
                        window_input,
                        self._registry,
                        annualization_days=request.spec.annualization_days,
                        scope=window.scope,
                        scope_label=window.label,
                    ).metrics
                )
            else:
                metrics.extend(
                    unavailable_metric_values(
                        self._registry,
                        scope=window.scope,
                        scope_label=window.label,
                        reason="no_observations_in_scope",
                    )
                )
        progress(0.88, "artifacts", "Freezing raw run artifacts")
        engine_version = "backtest-engine-v1"
        return BacktestRunResult(
            manifest=RunManifest(
                run_id=request.run_id,
                created_at=started_at,
                completed_at=datetime.now(UTC),
                engine_core=request.spec.core,
                engine_version=engine_version,
                run_fingerprint=backtest_run_fingerprint(
                    request.spec,
                    data_snapshot_id=request.dataset.data_snapshot_id,
                    target_tape_hash=request.target_tape.tape_hash,
                    engine_version=engine_version,
                    metric_registry_version=self._registry.version,
                ),
                run_spec=request.spec,
                strategy_hash=request.target_tape.strategy_hash,
                data_snapshot_id=request.dataset.data_snapshot_id,
                target_tape_hash=request.target_tape.tape_hash,
                metric_registry_version=self._registry.version,
                initial_cash=request.spec.initial_cash,
                annualization_days=request.spec.annualization_days,
                fee_bps=strategy.execution.fee_bps,
                slippage_bps=strategy.execution.slippage_bps,
                participation_rate=strategy.execution.participation_rate,
                strategy_provenance=request.strategy_provenance,
                warnings=request.dataset.warnings,
            ),
            metric_definitions=self._registry.definitions(),
            metrics=tuple(metrics),
            series=BacktestSeries(
                equity=full.equity_curve,
                drawdown=full.drawdown_curve,
                monthly_returns=full.monthly_returns,
                rolling_sharpe=full.rolling_sharpe,
            ),
            artifacts=artifacts,
        )

    @staticmethod
    def _check_cancelled(cancelled: CancellationCheck) -> None:
        if cancelled():
            raise RunCancelledError("run cancelled")


def _benchmark_values(request: BacktestExecutionRequest) -> dict[date, float]:
    benchmark_id = request.dataset.benchmark_security_id
    if benchmark_id is None:
        return {}
    bars = tuple(item for item in request.dataset.bars if item.security_id == benchmark_id)
    if not bars:
        return {}
    first = bars[0].close
    return {item.session: request.spec.initial_cash * item.close / first for item in bars}


def _artifacts(result, costs) -> tuple[RawArtifactBundle, tuple[TradeOutcome, ...]]:
    snapshots = tuple(
        RawSnapshot(
            session=item.ts.date(),
            cash=item.cash,
            equity=item.equity,
            gross_exposure=item.gross_exposure,
            net_exposure=(
                sum(position.market_value for position in item.positions) / item.equity
                if item.equity != 0
                else 0.0
            ),
        )
        for item in result.snapshots
    )
    positions = tuple(
        RawPosition(
            session=snapshot.ts.date(),
            security_id=position.instrument.symbol,
            quantity=str(position.quantity),
            average_price=position.average_price,
            market_price=position.market_price,
            market_value=position.market_value,
            unrealized_pnl=position.unrealized_pnl,
        )
        for snapshot in result.snapshots
        for position in snapshot.positions
    )
    orders = tuple(
        RawOrder(
            order_id=item.order_id,
            decision_id=item.decision_id,
            session=item.ts.date(),
            security_id=item.instrument.symbol,
            side=item.side.value,
            quantity=str(item.quantity),
            order_type=item.order_type.value,
            time_in_force=item.time_in_force.value,
        )
        for item in result.orders
    )
    fills = tuple(
        RawFill(
            fill_id=item.fill_id,
            order_id=item.order_id,
            session=item.ts.date(),
            security_id=item.instrument.symbol,
            side=item.side.value,
            quantity=str(item.quantity),
            price=item.price,
            fee=item.fee,
            slippage_per_share=item.slippage_per_share,
        )
        for item in result.fills
    )
    raw_costs = tuple(
        RawCost(
            session=item.ts.date(),
            kind=item.kind.value,
            security_id=item.instrument.symbol if item.instrument is not None else None,
            amount=item.amount,
        )
        for item in costs
    )
    trades = _closed_trades(result.fills)
    outcomes = tuple(
        TradeOutcome(
            security_id=item.security_id,
            closed_on=item.closed_on,
            pnl=item.pnl,
            fees=item.fees,
            slippage_cost=item.slippage_cost,
        )
        for item in trades
    )
    return (
        RawArtifactBundle(snapshots, positions, orders, fills, raw_costs, trades),
        outcomes,
    )


@dataclass
class _OpenTrade:
    quantity: float
    average_price: float
    opened_on: date
    opening_fees: float
    opening_slippage: float


def _closed_trades(fills: tuple[FillEvent, ...]) -> tuple[RawTrade, ...]:
    states: dict[str, _OpenTrade] = {}
    trades: list[RawTrade] = []
    for fill in sorted(fills, key=lambda item: (item.ts, item.fill_id)):
        security_id = fill.instrument.symbol
        delta = float(fill.quantity) * (1 if fill.side is Side.BUY else -1)
        slip = float(fill.quantity) * abs(fill.slippage_per_share)
        state = states.get(security_id)
        if state is None or state.quantity == 0 or state.quantity * delta > 0:
            current_quantity = abs(state.quantity) if state is not None else 0.0
            added = abs(delta)
            total = current_quantity + added
            states[security_id] = _OpenTrade(
                quantity=(1 if delta > 0 else -1) * total,
                average_price=(
                    ((state.average_price * current_quantity) if state is not None else 0.0)
                    + fill.price * added
                )
                / total,
                opened_on=state.opened_on if state is not None else fill.ts.date(),
                opening_fees=(state.opening_fees if state is not None else 0.0) + fill.fee,
                opening_slippage=(state.opening_slippage if state is not None else 0.0) + slip,
            )
            continue
        open_quantity = abs(state.quantity)
        fill_quantity = abs(delta)
        closed_quantity = min(open_quantity, fill_quantity)
        open_fraction = closed_quantity / open_quantity
        exit_fraction = closed_quantity / fill_quantity
        fees = state.opening_fees * open_fraction + fill.fee * exit_fraction
        slippage_cost = state.opening_slippage * open_fraction + slip * exit_fraction
        gross_pnl = (
            (fill.price - state.average_price) * closed_quantity * (1 if state.quantity > 0 else -1)
        )
        trades.append(
            RawTrade(
                security_id=security_id,
                opened_on=state.opened_on,
                closed_on=fill.ts.date(),
                side="long" if state.quantity > 0 else "short",
                quantity=str(Decimal(str(closed_quantity)).normalize()),
                entry_price=state.average_price,
                exit_price=fill.price,
                pnl=gross_pnl - fees,
                fees=fees,
                slippage_cost=slippage_cost,
            )
        )
        remaining_open = open_quantity - closed_quantity
        remaining_fill = fill_quantity - closed_quantity
        if remaining_open > 0:
            states[security_id] = _OpenTrade(
                quantity=(1 if state.quantity > 0 else -1) * remaining_open,
                average_price=state.average_price,
                opened_on=state.opened_on,
                opening_fees=state.opening_fees * (1 - open_fraction),
                opening_slippage=state.opening_slippage * (1 - open_fraction),
            )
        elif remaining_fill > 0:
            states[security_id] = _OpenTrade(
                quantity=(1 if delta > 0 else -1) * remaining_fill,
                average_price=fill.price,
                opened_on=fill.ts.date(),
                opening_fees=fill.fee * (1 - exit_fraction),
                opening_slippage=slip * (1 - exit_fraction),
            )
        else:
            states.pop(security_id, None)
    return tuple(trades)


def _slice_analytics(
    data: AnalyticsInput,
    artifacts: RawArtifactBundle,
    start: date,
    end: date,
) -> AnalyticsInput:
    fills = tuple(item for item in artifacts.fills if start <= item.session <= end)
    trades = tuple(item for item in data.trades if start <= item.closed_on <= end)
    costs = tuple(item for item in artifacts.costs if start <= item.session <= end)
    return AnalyticsInput(
        points=tuple(item for item in data.points if start <= item.session <= end),
        traded_notional=sum(float(item.quantity) * item.price for item in fills),
        trades=trades,
        total_fees=sum(item.fee for item in fills),
        total_slippage_cost=sum(
            float(item.quantity) * abs(item.slippage_per_share) for item in fills
        ),
        total_carry_cost=sum(item.amount for item in costs),
    )


def _assert_legacy_metric_parity(legacy, metrics) -> None:
    current = {item.metric_id: item.value for item in metrics}
    for metric_id in (
        "total_return",
        "cagr",
        "volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "turnover",
    ):
        expected = getattr(legacy, metric_id)
        actual = current[metric_id]
        if expected is None or actual is None:
            if expected is not actual:
                raise RuntimeError(f"metric parity failed: {metric_id}")
        elif abs(expected - actual) > 1e-10:
            raise RuntimeError(
                f"metric parity failed: {metric_id} legacy={expected} registry={actual}"
            )
