from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event, RLock, Thread

from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRepositoryPort,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestRunState,
    BacktestStartResponse,
    InlineDraft,
    RunProgressEvent,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.portfolio.facade.construction import TargetTape
from strategy_workbench.domain.strategy.facade.specification import strategy_spec_hash
from strategy_workbench.domain.strategy.facade.validation import validate_strategy

from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import BacktestDataPort, BacktestDataQuery
from .ports.outgoing.backtest_executor import (
    BacktestExecutionRequest,
    BacktestExecutorPort,
    RunCancelledError,
)


class InvalidBacktestRunError(ValueError):
    pass


class StrategyReferenceNotFoundError(LookupError):
    """The saved revision a run refers to does not exist."""


class StaleStrategyReferenceError(RuntimeError):
    """The saved revision exists but its spec_hash differs from what the caller expected."""


class BacktestRunNotFoundError(KeyError):
    pass


class BacktestResultNotReadyError(RuntimeError):
    pass


@dataclass
class _RunRecord:
    state: BacktestRunState
    events: list[RunProgressEvent]
    cancellation: Event
    result: BacktestRunResult | None = None


class BacktestRunService:
    def __init__(
        self,
        portfolio_design: PortfolioDesignService,
        strategy_repository: StrategyRepositoryPort,
        data_source: BacktestDataPort,
        executor: BacktestExecutorPort,
        artifact_store: BacktestArtifactStorePort,
        *,
        new_id: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._portfolio_design = portfolio_design
        self._strategy_repository = strategy_repository
        self._data_source = data_source
        self._executor = executor
        self._artifact_store = artifact_store
        self._new_id = new_id
        self._now = now
        self._records: dict[str, _RunRecord] = {}
        self._lock = RLock()

    def start(self, request: BacktestRunSpec) -> BacktestStartResponse:
        spec, provenance = self._resolve(request)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - _resolve always fills it
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        validation = validate_strategy(strategy)
        if not validation.valid:
            codes = ", ".join(item.code for item in validation.issues)
            raise InvalidBacktestRunError(f"invalid strategy: {codes}")
        for window in spec.metric_windows:
            if window.start < strategy.data.start or window.end > strategy.data.end:
                raise InvalidBacktestRunError(
                    f"metric window exceeds strategy data range: {window.scope.value}"
                )
        portfolio = self._portfolio_design.preview(PortfolioPreviewRequest(strategy))
        if not portfolio.engine.compatible:
            raise InvalidBacktestRunError("strategy exceeds engine capabilities")
        run_id = self._new_id()
        created = self._now()
        state = BacktestRunState(
            run_id=run_id,
            status=RunStatus.QUEUED,
            progress=0.0,
            stage="queued",
            message="Run accepted",
            created_at=created,
            updated_at=created,
        )
        record = _RunRecord(state=state, events=[], cancellation=Event())
        with self._lock:
            self._records[run_id] = record
            self._emit(record, RunStatus.QUEUED, 0.0, "queued", "Run accepted")
        Thread(
            target=self._run,
            args=(run_id, spec, portfolio.tape, provenance),
            name=f"backtest-{run_id}",
            daemon=True,
        ).start()
        return BacktestStartResponse(record.state)

    def state(self, run_id: str) -> BacktestRunState:
        with self._lock:
            return self._record(run_id).state

    def result(self, run_id: str) -> BacktestRunResult:
        with self._lock:
            record = self._record(run_id)
            if record.result is None or record.state.status is not RunStatus.COMPLETED:
                raise BacktestResultNotReadyError(
                    f"backtest result is not ready: run_id={run_id} status={record.state.status}"
                )
            return record.result

    def cancel(self, run_id: str) -> BacktestRunState:
        with self._lock:
            record = self._record(run_id)
            if record.state.status in (
                RunStatus.COMPLETED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
            ):
                return record.state
            record.cancellation.set()
            self._emit(
                record,
                RunStatus.CANCEL_REQUESTED,
                record.state.progress,
                "cancellation",
                "Cancellation requested",
            )
            return record.state

    def events(self, run_id: str, *, after_sequence: int = -1) -> tuple[RunProgressEvent, ...]:
        with self._lock:
            return tuple(
                event for event in self._record(run_id).events if event.sequence > after_sequence
            )

    def _resolve(self, request: BacktestRunSpec) -> tuple[BacktestRunSpec, StrategyProvenance]:
        """Turn the request into a run spec whose `strategy` is the exact spec to execute."""
        source = request.strategy_source
        if request.strategy is not None and source is not None:
            raise InvalidBacktestRunError(
                "run request must name its strategy once — both strategy and strategy_source given"
            )
        if isinstance(source, SavedRevisionReference):
            try:
                record = self._strategy_repository.get(source.strategy_id, source.revision)
            except StrategyNotFoundError as error:
                raise StrategyReferenceNotFoundError(
                    "saved strategy revision not found — "
                    f"strategy_id={source.strategy_id} revision={source.revision}"
                ) from error
            if record.spec_hash != source.expected_spec_hash:
                raise StaleStrategyReferenceError(
                    "saved revision hash mismatch — "
                    f"strategy_id={source.strategy_id} revision={source.revision} "
                    f"expected={source.expected_spec_hash} actual={record.spec_hash}"
                )
            provenance = StrategyProvenance(
                kind=StrategySourceKind.SAVED_REVISION,
                spec_hash=record.spec_hash,
                schema_version=record.spec.identity.schema_version,
                strategy_id=record.strategy_id,
                revision=record.revision,
                source_hash=record.source.source_hash if record.source else None,
            )
            return replace(request, strategy=record.spec), provenance
        if isinstance(source, InlineDraft):
            strategy, source_hash = source.spec, source.source_hash
        else:
            if request.strategy is None:  # pragma: no cover - dataclass invariant
                raise InvalidBacktestRunError("run spec has neither strategy nor strategy_source")
            strategy, source_hash = request.strategy, None
        provenance = StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=strategy_spec_hash(strategy),
            schema_version=strategy.identity.schema_version,
            source_hash=source_hash,
        )
        return replace(request, strategy=strategy), provenance

    def _run(
        self,
        run_id: str,
        spec: BacktestRunSpec,
        tape: TargetTape,
        provenance: StrategyProvenance,
    ) -> None:
        record = self._record(run_id)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - resolved before the thread starts
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        try:
            self._update(record, RunStatus.RUNNING, 0.05, "data", "Loading market data")
            security_ids = tuple(
                sorted({target.security_id for frame in tape.frames for target in frame.targets})
            )
            if not security_ids:
                raise InvalidBacktestRunError("target tape does not contain any positions")
            dataset = self._data_source.load_backtest_dataset(
                BacktestDataQuery(
                    start=strategy.data.start,
                    end=strategy.data.end,
                    security_ids=security_ids,
                    benchmark_security_id=spec.benchmark_security_id,
                )
            )
            self._raise_if_cancelled(record)
            self._update(record, RunStatus.RUNNING, 0.25, "engine", "Running backtest engine")
            result = self._executor.execute(
                BacktestExecutionRequest(run_id, spec, tape, dataset, provenance),
                progress=lambda value, stage, message: self._update(
                    record,
                    RunStatus.RUNNING,
                    min(max(value, 0.25), 0.9),
                    stage,
                    message,
                ),
                cancelled=record.cancellation.is_set,
            )
            self._raise_if_cancelled(record)
            self._update(record, RunStatus.RUNNING, 0.93, "artifact", "Committing artifacts")
            commit = self._artifact_store.commit(result)
            with self._lock:
                record.result = result
                record.state = replace(
                    record.state,
                    artifact_uri=commit.uri,
                    artifact_sha256=commit.sha256,
                )
                self._emit(
                    record,
                    RunStatus.COMPLETED,
                    1.0,
                    "completed",
                    "Run completed",
                )
        except RunCancelledError:
            self._update(
                record, RunStatus.CANCELLED, record.state.progress, "cancelled", "Run cancelled"
            )
        except BaseException as error:
            with self._lock:
                if record.cancellation.is_set():
                    self._emit(
                        record,
                        RunStatus.CANCELLED,
                        record.state.progress,
                        "cancelled",
                        "Run cancelled",
                    )
                else:
                    record.state = replace(record.state, error=f"{type(error).__name__}: {error}")
                    self._emit(
                        record,
                        RunStatus.FAILED,
                        record.state.progress,
                        "failed",
                        "Run failed",
                    )

    def _update(
        self,
        record: _RunRecord,
        status: RunStatus,
        progress: float,
        stage: str,
        message: str,
    ) -> None:
        with self._lock:
            if record.cancellation.is_set() and status is RunStatus.RUNNING:
                raise RunCancelledError("run cancelled")
            self._emit(record, status, progress, stage, message)

    def _emit(
        self,
        record: _RunRecord,
        status: RunStatus,
        progress: float,
        stage: str,
        message: str,
    ) -> None:
        occurred_at = self._now()
        record.state = replace(
            record.state,
            status=status,
            progress=progress,
            stage=stage,
            message=message,
            updated_at=occurred_at,
        )
        record.events.append(
            RunProgressEvent(
                sequence=len(record.events),
                run_id=record.state.run_id,
                status=status,
                progress=progress,
                stage=stage,
                message=message,
                occurred_at=occurred_at,
            )
        )

    def _record(self, run_id: str) -> _RunRecord:
        try:
            return self._records[run_id]
        except KeyError:
            raise BacktestRunNotFoundError(run_id) from None

    @staticmethod
    def _raise_if_cancelled(record: _RunRecord) -> None:
        if record.cancellation.is_set():
            raise RunCancelledError("run cancelled")
