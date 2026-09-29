from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.errors import EquityWipedOut
from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.ports.universe import Membership, UniverseResult
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionEvent,
    CorporateActionType,
    CostKind,
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
    BacktestDataset,
    BacktestExecutionRequest,
    CancellationCheck,
    EquityWipedOutError,
    MarketBarRecord,
    ProgressCallback,
    RunCancelledError,
)
from strategy_workbench.domain.analytics.facade.metrics import (
    BASE_RATE_CONFIRMED_ON,
    AnalysisPoint,
    AnalyticsInput,
    MetricRegistry,
    MetricUnavailableReason,
    TradeOutcome,
    build_default_metric_registry,
    compute_analytics,
    unavailable_metric_values,
)
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_hash,
    participation_volumes,
    sell_tax_schedule,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestSeries,
    DataWarning,
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


def _columnar_feed(
    rows: Sequence[MarketBarRecord],
    liquidity: Mapping[tuple[date, str], int] | None = None,
) -> DataFeed:
    """dataset 행을 열로 펴 DataFeed를 만든다 — 중간에 `Bar` 객체를 만들지 않는다.

    세션은 오름차순, 한 세션 안 종목은 dataset 입력 순서다. 같은 행 묶음을 `Bar`로 만들어
    `DataFeed(bars)`에 넣었을 때와 같은 스냅샷 순서다. 가격·거래량 불변식과 세션 단조는
    `DataFeed.from_columns`가 검사한다 — 어댑터는 모양만 바꾼다.

    Args:
        rows: dataset이 답한 시장 bar 행. 순서는 세션 기준으로만 쓰인다.
        liquidity: `(세션, 종목)` → 유동성 캡 기준 거래량(`participation_volumes`). 없으면 엔진이
            세션 거래량을 쓴다.

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
    liquidity_volumes: list[int] = []
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
            if liquidity is not None:
                liquidity_volumes.append(liquidity[(row.session, row.security_id)])
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
        liquidity_volumes=None if liquidity is None else liquidity_volumes,
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
        *,
        environment: RunEnvironment,
    ) -> None:
        self._spec = spec
        self._environment = environment
        self._bridge = portfolio_bridge
        self._frames: dict[date, TapeFrame] = {
            frame.signal_as_of: TapeFrame(
                action=portfolio_bridge.to_target_action(
                    frame, max_participation=environment.participation_rate
                ),
                reason=f"target_tape:{frame.signal_as_of.isoformat()}",
            )
            for frame in tape.frames
        }

    def requirements(self) -> StrategyRequirements:
        return self._bridge.requirements(self._spec, self._environment)

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
        # 비용·체결 파라미터는 실행 설정이 소유한다(P2-01). 요청자가 명시한 값이 엔진에 닿지
        # 않으면 매니페스트에 적힌 수수료와 실제로 돌린 수수료가 달라진다.
        environment = request.spec.environment
        if environment is None:
            raise ValueError(
                "execution request must carry a resolved run environment — "
                f"run_id={request.run_id} provenance={request.strategy_provenance.kind.value}"
            )
        self._check_cancelled(cancelled)
        started_at = datetime.now(UTC)
        # 진행 값은 이 실행기 작업 안의 완료 비율(0~1)이다(`ProgressCallback` 계약). 준비 0.35,
        # 엔진 루프가 끝난 뒤 지표 계산 0.78, 산출물 고정 0.88 이다. run 진행 막대의 engine 구간
        # 배치(84~92%)는 유스케이스가 정한다(이슈 #162).
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
                fee_bps=environment.fee_bps,
                annualization_days=request.spec.annualization_days,
                max_gross_leverage=max(1.0, strategy.risk.gross_exposure),
                sell_tax_schedule=sell_tax_schedule(environment),
            ),
            slippage=FixedBpsSlippage(environment.slippage_bps),
            max_participation=environment.participation_rate,
            core=request.spec.core.value,
        )
        try:
            engine.run(
                TargetTapeStrategy(
                    strategy,
                    request.target_tape,
                    self._portfolio_bridge,
                    environment=environment,
                ),
                _columnar_feed(
                    request.dataset.bars,
                    participation_volumes(
                        environment,
                        (
                            (item.session, item.security_id, item.close, item.trading_value)
                            for item in (*request.dataset.history_bars, *request.dataset.bars)
                        ),
                    ),
                ),
                corporate_actions=corporate_actions,
                universe=universe,
            )
        except EquityWipedOut as error:
            # 커널 예외를 포트 어휘로 옮긴다 — application 은 커널을 import 하지 않는다(#285).
            raise EquityWipedOutError(str(error)) from error
        self._check_cancelled(cancelled)
        progress(0.78, "analytics", "Calculating professional metrics")
        # 엔진 결과는 columnar 테이블로 받는다 — 공개 Event 객체는 여기서 곧바로 raw
        # artifact로 다시 옮겨질 중간 산물일 뿐이라 만들 이유가 없다.
        tables = engine.event_store.result_tables()
        artifacts, outcomes = _artifacts(tables)
        run_sessions = tuple(item.session for item in artifacts.snapshots)
        benchmark, carried = _benchmark_series(
            request.dataset, request.spec.initial_cash, run_sessions
        )
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
            total_taxes=sum(item.amount for item in artifacts.costs if item.kind == _SELL_TAX),
            total_slippage_cost=tables.fill_totals.total_slippage_cost,
            total_carry_cost=sum(
                item.amount for item in artifacts.costs if item.kind in _CARRY_COSTS
            ),
        )
        full = compute_analytics(
            full_input,
            self._registry,
            annualization_days=request.spec.annualization_days,
        )
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
                        reason=MetricUnavailableReason.NO_OBSERVATIONS_IN_SCOPE,
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
                fee_bps=environment.fee_bps,
                slippage_bps=environment.slippage_bps,
                participation_rate=environment.participation_rate,
                environment=environment,
                environment_hash=environment_hash(environment),
                strategy_provenance=request.strategy_provenance,
                warnings=(
                    *request.dataset.warnings,
                    *_benchmark_warnings(request.dataset, run_sessions, benchmark, carried),
                    *_base_rate_warnings(full.base_rate_carried_sessions),
                ),
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


# 비용 기록의 kind 를 지표로 나눈다. 매도 거래세는 `total_taxes`, 대차 비용·신용 이자는
# `total_carry_cost` 다.
_SELL_TAX = CostKind.SELL_TAX.value
_CARRY_COSTS = frozenset({CostKind.SHORT_BORROW.value, CostKind.MARGIN_INTEREST.value})

# 엔진이 보유 수량에 적용하는 확인된 사건(`_apply_corporate_action`)과 같은 집합이다. 알림 전용
# 사건(주식 수 변화만 확인)은 가격 반비례가 확인되지 않아 곡선도 조정하지 않는다.
_PRICE_ADJUSTING_ACTIONS = frozenset(
    {CorporateActionType.SPLIT.value, CorporateActionType.REVERSE_SPLIT.value}
)


def _benchmark_values(dataset: BacktestDataset, initial_cash: float) -> dict[date, float]:
    """벤치마크 종목을 첫 세션에 `initial_cash`어치 사서 들고 있는 곡선.

    bar는 원주가라 분할·병합 날 끊긴다. 엔진이 전략 보유 수량에 적용하는 것과 같은 사건
    (`dataset.corporate_actions`의 split·reverse_split, `ratio` = 주식 수 배율)을 사건 세션부터
    누적해 곱하면 보유자가 실제로 가진 가치가 된다(이슈 #219). 사건 세션에 bar가 없으면 다음 bar부터
    반영된다. 첫 bar 이전·당일 사건은 기준값에도 곱해져 곡선을 움직이지 않는다.
    """
    benchmark_id = dataset.benchmark_security_id
    if benchmark_id is None:
        return {}
    bars = sorted(
        (item for item in dataset.bars if item.security_id == benchmark_id),
        key=lambda item: item.session,
    )
    if not bars:
        return {}
    actions = sorted(
        (
            (item.session, float(item.ratio))
            for item in dataset.corporate_actions
            if item.security_id == benchmark_id and item.action_type in _PRICE_ADJUSTING_ACTIONS
        ),
        key=lambda item: item[0],
    )
    values: dict[date, float] = {}
    cumulative = 1.0
    applied = 0
    base: float | None = None
    for bar in bars:
        while applied < len(actions) and actions[applied][0] <= bar.session:
            cumulative *= actions[applied][1]
            applied += 1
        adjusted = bar.close * cumulative
        if base is None:
            base = adjusted
        values[bar.session] = initial_cash * adjusted / base
    return values


def _benchmark_series(
    dataset: BacktestDataset, initial_cash: float, sessions: Sequence[date]
) -> tuple[dict[date, float], tuple[date, ...]]:
    """run 세션마다의 벤치마크 값과, bar가 없어 직전 값을 이어 쓴 세션들.

    벤치마크 종목이 거래정지·상장폐지로 bar가 없는 세션에는 직전 값(분할·병합 반영)을 이어 쓴다
    (이슈 #226). 엔진이 정지 종목을 평가하는 규칙과 같다 — `Portfolio.mark`(Python·Rust core)는
    bar가 있는 종목의 평가 가격만 갱신하므로 정지 종목은 직전 종가로 계속 평가된다. 정지 중 사건은
    엔진이 다음 bar 세션에 정산하듯 `_benchmark_values`가 다음 bar부터 반영한다. 첫 bar 이전 세션은
    이어 쓸 값이 없어(살 수 없었다) 비워 두고, 그 경우 벤치마크 지표는 전과 같이 사용 불가다.
    """
    by_bar = _benchmark_values(dataset, initial_cash)
    if not by_bar:
        return {}, ()
    values: dict[date, float] = {}
    carried: list[date] = []
    last: float | None = None
    for session in sorted(sessions):
        value = by_bar.get(session)
        if value is not None:
            last = value
        elif last is not None:
            value = last
            carried.append(session)
        else:
            continue
        values[session] = value
    return values, tuple(carried)


_WARNING_SESSION_LIST_LIMIT = 10


def _listed_sessions(sessions: Sequence[date]) -> str:
    """경고에 적을 세션 목록. 앞 10개만 쓰고 나머지는 개수로 적는다."""
    shown = ", ".join(session.isoformat() for session in sessions[:_WARNING_SESSION_LIST_LIMIT])
    rest = len(sessions) - _WARNING_SESSION_LIST_LIMIT
    return f"{shown} (+{rest})" if rest > 0 else shown


def _benchmark_warnings(
    dataset: BacktestDataset,
    sessions: Sequence[date],
    values: Mapping[date, float],
    carried: Sequence[date],
) -> tuple[DataWarning, ...]:
    """벤치마크 곡선이 bar 그대로가 아닌 세션을 원인별 manifest 경고로 알린다(이슈 #226·#229).

    - 첫 bar 전 세션(`benchmark.no_bar_at_start`): 값을 비워 전체 구간 벤치마크 지표가 사용 불가다.
      상장 전인지 거래정지인지는 멤버십 `first_session`으로, 무효 행인지는 `dataset.invalid_bars`로
      가른다.
    - 이어 쓴 세션 중 무효 OHLC 행(`benchmark.invalid_bar_sessions_carried`): 거래된 날인데 원장
      행이 GAP-14라 bar가 없다(이슈 #241).
    - 나머지 이어 쓴 세션 중 멤버십 `last_session` 이하(`benchmark.suspended_sessions_carried`):
      거래정지.
    - 이어 쓴 세션 중 `last_session` 뒤(`benchmark.delisted_sessions_frozen`): 상장이 끝난 뒤 동결.
      멤버십이 없으면 무효 행이 아닌 이어 쓴 세션을 모두 거래정지로 센다.

    화면과 AI 결과 설명은 code와 message를 그대로 쓴다. 코드가 달라 한 목록에서 서로 섞이지 않는다.
    """
    benchmark_id = dataset.benchmark_security_id
    if benchmark_id is None:
        return ()
    ordered = sorted(sessions)
    membership = next(
        (item for item in dataset.memberships if item.security_id == benchmark_id), None
    )
    invalid = {item.session for item in dataset.invalid_bars if item.security_id == benchmark_id}
    warnings: list[DataWarning] = []
    # 첫 bar 뒤로는 이어 쓰기로 값이 모두 차므로, 값이 없는 세션은 곧 첫 bar 전 세션이다.
    leading = [session for session in ordered if session not in values]
    if leading:
        first_bar = min(values) if values else None
        leading_invalid = sum(1 for session in leading if session in invalid)
        if membership is not None and ordered and membership.first_session > ordered[0]:
            cause = f"상장 전이다(first_session={membership.first_session.isoformat()})"
        elif first_bar is None:
            cause = "창 안에 벤치마크 종목의 bar가 하나도 없다"
        elif leading_invalid == len(leading):
            cause = "창 시작 세션의 원장 행이 무효(GAP-14)였다(거래는 있었다)"
        elif leading_invalid:
            cause = (
                f"창 시작부터 거래정지 중이었고 그중 {leading_invalid}세션은 원장 행이 "
                "무효(GAP-14)였다"
            )
        elif membership is not None:
            cause = "창 시작부터 거래정지 중이었다"
        else:
            cause = "상장 전이거나 거래정지 중이었다"
        invalid_note = f"invalid_sessions={leading_invalid} " if leading_invalid else ""
        warnings.append(
            DataWarning(
                code="benchmark.no_bar_at_start",
                message=(
                    "벤치마크 종목의 첫 bar보다 앞선 세션은 살 수 없어 벤치마크 값을 비웠다 — "
                    f"{cause}. 그래서 전체 구간의 benchmark_return·excess_return은 사용 불가다. "
                    "첫 bar 이후에 시작하는 측정 창은 영향을 받지 않는다. "
                    f"benchmark={benchmark_id} leading_sessions={len(leading)} {invalid_note}"
                    f"first_bar={first_bar.isoformat() if first_bar else '없음'} "
                    f"sessions={_listed_sessions(leading)}"
                ),
            )
        )
    last_listed = membership.last_session if membership is not None else None
    frozen = [s for s in carried if last_listed is not None and s > last_listed]
    listed = [s for s in carried if last_listed is None or s <= last_listed]
    invalid_carried = [s for s in listed if s in invalid]
    suspended = [s for s in listed if s not in invalid]
    if invalid_carried:
        warnings.append(
            DataWarning(
                code="benchmark.invalid_bar_sessions_carried",
                message=(
                    "벤치마크 종목의 원장 행이 무효(OHLC 결측·0 이하·불일치, GAP-14)라 bar로 내지 "
                    "않은 세션은 직전 종가(분할·병합 반영)를 이어 썼다 — 거래정지가 아니라 거래된 "
                    "날이다. 같은 실행의 equity.invalid_ohlc_rows_dropped 경고가 그 행을 센다. "
                    f"benchmark={benchmark_id} invalid_sessions={len(invalid_carried)} "
                    f"sessions={_listed_sessions(invalid_carried)}"
                ),
            )
        )
    if suspended:
        warnings.append(
            DataWarning(
                code="benchmark.suspended_sessions_carried",
                message=(
                    "벤치마크 종목이 거래정지돼 bar가 없는 세션은 직전 종가(분할·병합 반영)를 "
                    "이어 썼다 — 엔진이 정지 종목을 평가하는 규칙과 같다. "
                    f"benchmark={benchmark_id} carried_sessions={len(suspended)} "
                    f"sessions={_listed_sessions(suspended)}"
                ),
            )
        )
    if frozen and last_listed is not None:
        warnings.append(
            DataWarning(
                code="benchmark.delisted_sessions_frozen",
                message=(
                    "벤치마크 종목의 상장이 끝난 뒤(상장폐지 등) 세션은 마지막 값에 동결했다 — "
                    "엔진이 상장 종료 종목의 포지션을 마지막 평가 가격에 두는 규칙과 같다. "
                    "benchmark_return·excess_return은 이 구간을 포함해 계산했지만 이 구간의 "
                    "벤치마크는 거래되지 않은 값이므로 초과수익을 실제 비교로 읽지 않는다. "
                    f"benchmark={benchmark_id} frozen_sessions={len(frozen)} "
                    f"last_session={last_listed.isoformat()} sessions={_listed_sessions(frozen)}"
                ),
            )
        )
    return tuple(warnings)


def _base_rate_warnings(carried: Sequence[date]) -> tuple[DataWarning, ...]:
    """기준금리 이력 확인일 뒤 세션에 마지막 금리를 이어 썼다고 알린다(#274)."""
    if not carried:
        return ()
    return (
        DataWarning(
            code="analytics.base_rate_carried_forward",
            message=(
                "한국은행 기준금리 이력을 확인한 날 뒤의 세션은 마지막 기준금리를 이어 써서 "
                "샤프·소르티노·롤링 샤프의 무위험수익률을 계산했다. 그 뒤 금리가 바뀌었다면 이 "
                "지표들이 틀린다. "
                f"confirmed_on={BASE_RATE_CONFIRMED_ON.isoformat()} "
                f"carried_sessions={len(carried)} sessions={_listed_sessions(carried)}"
            ),
        ),
    )


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
    # 거래 단위 비용(`fees`)은 매도 거래세를 포함한다. 엔진은 매도 체결마다 거래세 기록을 그 체결
    # 바로 뒤에 하나씩 남기므로, 같은 세션·종목의 기록을 체결 순서대로 짝짓는다.
    taxes: dict[tuple[int, int], deque[float]] = {}
    for session_index, kind, instrument_index, amount in tables.costs:
        if kind == _SELL_TAX and instrument_index is not None:
            taxes.setdefault((session_index, instrument_index), deque()).append(amount)
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
        pending_taxes = taxes.get((session_index, instrument_index))
        if side == "sell" and pending_taxes:
            fee += pending_taxes.popleft()
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
                # 지수 표기 금지(#135): `Decimal("700.0").normalize()`는 `7E+2`라 `str()`이
                # "7E+2"를 냈다. `format(…, "f")`는 같은 값을 "700"으로 — fills·positions의
                # 정수 문자열과 표현이 맞는다.
                quantity=format(Decimal(str(closed_quantity)).normalize(), "f"),
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
        total_taxes=sum(item.amount for item in costs if item.kind == _SELL_TAX),
        total_slippage_cost=sum(
            float(item.quantity) * abs(item.slippage_per_share) for item in fills
        ),
        total_carry_cost=sum(item.amount for item in costs if item.kind in _CARRY_COSTS),
        # 구간 첫날 수익률이 빠지지 않게 직전 세션을 기준으로 넘긴다. 실행 첫날부터면 없다.
        base=next((item for item in reversed(data.points) if item.session < start), None),
    )
