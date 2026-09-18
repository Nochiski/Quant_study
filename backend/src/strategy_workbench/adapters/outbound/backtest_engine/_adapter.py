from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.ports.universe import Membership, UniverseResult
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    StrategyEvent,
)
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.requirements import StrategyRequirements
from backtest_engine.types.result_tables import ResultTables
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import DeclarativeTapeStrategy, TapeFrame
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestExecutionRequest,
    CancellationCheck,
    MarketBarRecord,
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


def _columnar_feed(rows: Sequence[MarketBarRecord]) -> DataFeed:
    """dataset 행을 열로 펴 DataFeed를 만든다 — 중간에 `Bar` 객체를 만들지 않는다.

    세션은 오름차순, 한 세션 안 종목은 dataset 입력 순서다. 같은 행 묶음을 `Bar`로 만들어
    `DataFeed(bars)`에 넣었을 때와 같은 스냅샷 순서다. 가격·거래량 불변식과 세션 단조는
    `DataFeed.from_columns`가 검사한다 — 어댑터는 모양만 바꾼다.

    Args:
        rows: dataset이 답한 시장 bar 행. 순서는 세션 기준으로만 쓰인다.

    Returns:
        열을 그대로 보관하는 DataFeed. persistent Rust 경로는 이 열을 바로 FFI로 넘긴다.
    """
    rows_by_session: dict[date, list[MarketBarRecord]] = {}
    for row in rows:
        rows_by_session.setdefault(row.session, []).append(row)

    sessions: list[datetime] = []
    instruments: list[InstrumentId] = []
    instrument_index: dict[str, int] = {}
    offsets = [0]
    instrument_ids: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[int] = []
    for session in sorted(rows_by_session):
        sessions.append(datetime.combine(session, time(15, 30)))
        for row in rows_by_session[session]:
            index = instrument_index.get(row.security_id)
            if index is None:
                # `_instrument`는 종목당 한 번만 부른다 — 행마다 부르면 유니버스 크기가
                # 아니라 행 수만큼 InstrumentId를 만든다.
                index = len(instruments)
                instrument_index[row.security_id] = index
                instruments.append(_instrument(row.security_id))
            instrument_ids.append(index)
            opens.append(row.open)
            highs.append(row.high)
            lows.append(row.low)
            closes.append(row.close)
            volumes.append(row.volume)
        offsets.append(len(instrument_ids))
    return DataFeed.from_columns(
        sessions=sessions,
        instruments=instruments,
        offsets=offsets,
        instrument_ids=instrument_ids,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )


def _instrument(security_id: str) -> InstrumentId:
    return InstrumentId("XKRX", security_id, AssetClass.EQUITY, "KRW")


class TargetTapeStrategy(DeclarativeTapeStrategy):
    """컴파일된 TargetTape를 엔진 전략으로 노출한다.

    선언형 tape(`DeclarativeTapeStrategy`)를 상속하므로 persistent Rust 경로는
    `tape_frames()`를 적재해 콜백 없이 실행하고, Python 경로는 `on_event()`가 같은
    규칙(`evaluate_tape`)을 적용한다.
    bar 없는 종목 처리(거래정지·기준가 세션은 equity 피드에 행이 없다)는 `evaluate_tape`가
    단일 정본이다.
    """

    @property
    def idle_reason(self) -> str:
        return "target_tape_idle"

    def __init__(
        self,
        spec: StrategySpec,
        tape: TargetTape,
        portfolio_bridge: BacktestEnginePortfolioAdapter,
    ) -> None:
        self._spec = spec
        self._bridge = portfolio_bridge
        self._frames: dict[date, TapeFrame] = {
            frame.signal_as_of: TapeFrame(
                action=portfolio_bridge.to_target_action(
                    frame, max_participation=spec.execution.participation_rate
                ),
                reason=f"target_tape:{frame.signal_as_of.isoformat()}",
            )
            for frame in tape.frames
        }

    def requirements(self) -> StrategyRequirements:
        return self._bridge.requirements(self._spec)

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


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
            _columnar_feed(request.dataset.bars),
            corporate_actions=corporate_actions,
            universe=universe,
        )
        self._check_cancelled(cancelled)
        progress(0.78, "analytics", "Calculating professional metrics")
        # 엔진 결과는 columnar 테이블로 받는다 — 공개 Event 객체는 여기서 곧바로 raw
        # artifact로 다시 옮겨질 중간 산물일 뿐이라 만들 이유가 없다.
        tables = engine.event_store.result_tables()
        artifacts, outcomes = _artifacts(tables)
        benchmark = _benchmark_values(request)
        # net exposure 공식은 `_artifacts`가 단일 정본이다 — 여기서 다시 계산하지 않는다.
        points = tuple(
            AnalysisPoint(
                session=item.session,
                equity=item.equity,
                gross_exposure=item.gross_exposure,
                net_exposure=item.net_exposure,
                benchmark_equity=benchmark.get(item.session),
            )
            for item in artifacts.snapshots
        )
        full_input = AnalyticsInput(
            points=points,
            traded_notional=tables.fill_totals.traded_notional,
            trades=outcomes,
            total_fees=tables.fill_totals.total_fees,
            total_slippage_cost=tables.fill_totals.total_slippage_cost,
            total_carry_cost=sum(amount for _session, _kind, _security, amount in tables.costs),
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


def _artifacts(tables: ResultTables) -> tuple[RawArtifactBundle, tuple[TradeOutcome, ...]]:
    """엔진 결과 테이블을 워크벤치 raw artifact로 옮긴다.

    세션 날짜와 종목 코드는 행마다 다시 만들지 않는다 — position 행이 세션 × 보유 종목 수라
    행당 `.date()` 한 번이 그대로 유니버스 크기의 비용이 된다.
    """
    session_dates = tuple(ts.date() for ts in tables.sessions)
    security_ids = tuple(instrument.symbol for instrument in tables.instruments)
    snapshots = tuple(
        RawSnapshot(
            session=session_dates[session_index],
            cash=cash,
            equity=equity,
            gross_exposure=gross_exposure,
            net_exposure=positions_value / equity if equity != 0 else 0.0,
        )
        for session_index, cash, equity, gross_exposure, positions_value in tables.snapshots
    )
    positions = tuple(
        RawPosition(
            session=session_dates[session_index],
            security_id=security_ids[instrument_index],
            quantity=str(quantity),
            average_price=average_price,
            market_price=market_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
        )
        for (
            session_index,
            instrument_index,
            quantity,
            average_price,
            market_price,
            market_value,
            unrealized_pnl,
        ) in tables.positions
    )
    orders = tuple(
        RawOrder(
            order_id=order_id,
            decision_id=decision_id,
            session=session_dates[session_index],
            security_id=security_ids[instrument_index],
            side=side,
            quantity=str(quantity),
            order_type=order_type,
            time_in_force=time_in_force,
        )
        for (
            order_id,
            decision_id,
            session_index,
            instrument_index,
            side,
            quantity,
            order_type,
            time_in_force,
        ) in tables.orders
    )
    fills = tuple(
        RawFill(
            fill_id=fill_id,
            order_id=order_id,
            session=session_dates[session_index],
            security_id=security_ids[instrument_index],
            side=side,
            quantity=str(quantity),
            price=price,
            fee=fee,
            slippage_per_share=slippage_per_share,
        )
        for (
            fill_id,
            order_id,
            session_index,
            instrument_index,
            side,
            quantity,
            price,
            fee,
            slippage_per_share,
        ) in tables.fills
    )
    raw_costs = tuple(
        RawCost(
            session=session_dates[session_index],
            kind=kind,
            security_id=None if instrument_index is None else security_ids[instrument_index],
            amount=amount,
        )
        for session_index, kind, instrument_index, amount in tables.costs
    )
    trades = _closed_trades(tables, session_dates, security_ids)
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


def _closed_trades(
    tables: ResultTables,
    session_dates: tuple[date, ...],
    security_ids: tuple[str, ...],
) -> tuple[RawTrade, ...]:
    sessions = tables.sessions
    states: dict[str, _OpenTrade] = {}
    trades: list[RawTrade] = []
    # `(세션 ts, fill_id)` 순서 — 세션 index는 feed 정렬 순서라 ts 정렬과 결과가 같다.
    for row in sorted(tables.fills, key=lambda item: (sessions[item[2]], item[0])):
        (
            _fill_id,
            _order_id,
            session_index,
            instrument_index,
            side,
            quantity,
            price,
            fee,
            slippage_per_share,
        ) = row
        security_id = security_ids[instrument_index]
        session = session_dates[session_index]
        delta = float(quantity) * (1 if side == "buy" else -1)
        slip = float(quantity) * abs(slippage_per_share)
        state = states.get(security_id)
        if state is None or state.quantity == 0 or state.quantity * delta > 0:
            current_quantity = abs(state.quantity) if state is not None else 0.0
            added = abs(delta)
            total = current_quantity + added
            states[security_id] = _OpenTrade(
                quantity=(1 if delta > 0 else -1) * total,
                average_price=(
                    ((state.average_price * current_quantity) if state is not None else 0.0)
                    + price * added
                )
                / total,
                opened_on=state.opened_on if state is not None else session,
                opening_fees=(state.opening_fees if state is not None else 0.0) + fee,
                opening_slippage=(state.opening_slippage if state is not None else 0.0) + slip,
            )
            continue
        open_quantity = abs(state.quantity)
        fill_quantity = abs(delta)
        closed_quantity = min(open_quantity, fill_quantity)
        open_fraction = closed_quantity / open_quantity
        exit_fraction = closed_quantity / fill_quantity
        fees = state.opening_fees * open_fraction + fee * exit_fraction
        slippage_cost = state.opening_slippage * open_fraction + slip * exit_fraction
        gross_pnl = (
            (price - state.average_price) * closed_quantity * (1 if state.quantity > 0 else -1)
        )
        trades.append(
            RawTrade(
                security_id=security_id,
                opened_on=state.opened_on,
                closed_on=session,
                side="long" if state.quantity > 0 else "short",
                quantity=str(Decimal(str(closed_quantity)).normalize()),
                entry_price=state.average_price,
                exit_price=price,
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
                average_price=price,
                opened_on=session,
                opening_fees=fee * (1 - exit_fraction),
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
