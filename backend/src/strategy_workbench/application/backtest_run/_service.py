from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event, RLock, Thread

from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPipelineCancelledError,
    PortfolioPipelineOptions,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    StrategyNotFoundError,
    StrategyRepositoryPort,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestRunState,
    BacktestStartResponse,
    DataWarning,
    InlineDraft,
    RunProgressEvent,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
    WarningSeverity,
)
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


class StrategyRevisionRequiresUpgradeError(RuntimeError):
    """The saved revision is frozen under a retired schema version (spec D2).

    Its stored hash cannot equal the hash of the spec that would execute, so a run manifest
    could not name a reproducible saved reference. The author upgrades the source and saves a
    new revision, which is then runnable.
    """


class BacktestRunNotFoundError(KeyError):
    pass


class BacktestResultNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class BacktestRunSummary:
    """One process-lifetime run and the strategy meaning resolved before it started."""

    run: BacktestRunState
    strategy_provenance: StrategyProvenance


@dataclass
class _RunRecord:
    state: BacktestRunState
    request: BacktestRunSpec
    provenance: StrategyProvenance
    accepted_sequence: int
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
        self._next_accepted_sequence = 0
        self._lock = RLock()

    def start(self, request: BacktestRunSpec) -> BacktestStartResponse:
        """실행 요청을 받아 즉시 `QUEUED` 로 접수한다.

        요청 스레드에서는 데이터를 읽지 않는 검사(스펙 해석·검증·metric window·엔진 호환성·저장
        리비전 해시)만 하고, TargetTape 계산은 run 스레드의 `tape` 단계로 넘긴다. 이전에는 tape 를
        여기서 동기로 만들어 긴 구간에서 응답이 수 분 이상 걸리고 취소 수단이 없었다(이슈 #158).
        """

        spec, provenance = self._resolve(request)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - _resolve always fills it
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        validation = validate_strategy(strategy)
        if not validation.valid:
            raise InvalidPortfolioRequestError(validation)
        for window in spec.metric_windows:
            if window.start < strategy.data.start or window.end > strategy.data.end:
                raise InvalidBacktestRunError(
                    f"metric window exceeds strategy data range: {window.scope.value}"
                )
        engine = self._portfolio_design.preflight(PortfolioPreviewRequest(strategy))
        if not engine.compatible:
            raise InvalidBacktestRunError("strategy exceeds engine capabilities")
        # TargetTape.strategy_hash 는 같은 spec 의 strategy_spec_hash 다. tape 없이도 provenance 를
        # 확정할 수 있고, tape 단계가 이 값을 다시 대조한다.
        executed_hash = strategy_spec_hash(strategy)
        if provenance is None:
            source_hash = (
                request.strategy_source.source_hash
                if isinstance(request.strategy_source, InlineDraft)
                else None
            )
            provenance = StrategyProvenance(
                kind=StrategySourceKind.INLINE_DRAFT,
                spec_hash=executed_hash,
                schema_version=strategy.identity.schema_version,
                source_hash=source_hash,
            )
        elif provenance.spec_hash != executed_hash:
            raise RuntimeError(
                "saved strategy repository hash differs from the executed StrategySpec — "
                f"stored={provenance.spec_hash!r} executed={executed_hash!r}"
            )
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
        with self._lock:
            record = _RunRecord(
                state=state,
                request=request,
                provenance=provenance,
                accepted_sequence=self._next_accepted_sequence,
                events=[],
                cancellation=Event(),
            )
            self._next_accepted_sequence += 1
            self._records[run_id] = record
            self._emit(record, RunStatus.QUEUED, 0.0, "queued", "Run accepted")
            # 응답은 접수 시점 상태다. 스레드를 띄운 뒤 record.state 를 읽으면 이미 tape 단계로
            # 바뀌어 있을 수 있어 202 본문의 status 가 비결정이 된다.
            accepted = record.state
        Thread(
            target=self._run,
            args=(run_id, spec, provenance),
            name=f"backtest-{run_id}",
            daemon=True,
        ).start()
        return BacktestStartResponse(accepted)

    def state(self, run_id: str) -> BacktestRunState:
        with self._lock:
            return self._record(run_id).state

    def request(self, run_id: str) -> BacktestRunSpec:
        """Return the normalized request accepted for an in-process run.

        The unresolved request carries exactly one strategy source and is therefore safe to
        submit again. The resolved execution spec intentionally remains an internal detail until
        it is committed to the immutable result manifest.
        """

        with self._lock:
            return self._record(run_id).request

    def list_runs(
        self,
        page: PageRequest,
        *,
        strategy_id: str | None = None,
    ) -> Page[BacktestRunSummary]:
        """Return an atomic newest-accepted-first snapshot of the in-process run register."""

        with self._lock:
            ordered = tuple(
                sorted(
                    (
                        record
                        for record in self._records.values()
                        if strategy_id is None or record.provenance.strategy_id == strategy_id
                    ),
                    key=lambda record: record.accepted_sequence,
                    reverse=True,
                )
            )
            return Page(
                items=tuple(
                    BacktestRunSummary(
                        run=record.state,
                        strategy_provenance=record.provenance,
                    )
                    for record in ordered[page.offset : page.offset + page.limit]
                ),
                total=len(ordered),
                offset=page.offset,
                limit=page.limit,
            )

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

    def _resolve(
        self, request: BacktestRunSpec
    ) -> tuple[BacktestRunSpec, StrategyProvenance | None]:
        """Turn the request into a run spec whose `strategy` is the exact spec to execute."""
        source = request.strategy_source
        if request.strategy is None and source is None:
            raise InvalidBacktestRunError(
                "run request must name its strategy — neither strategy nor strategy_source given"
            )
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
            if record.requires_upgrade:
                raise StrategyRevisionRequiresUpgradeError(
                    "saved revision is frozen under a retired schema version and must be "
                    "upgraded and saved again before it can run — "
                    f"strategy_id={source.strategy_id} revision={source.revision} "
                    f"schema_version={record.spec.identity.schema_version}"
                )
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
            strategy = source.spec
        else:
            if request.strategy is None:  # pragma: no cover - dataclass invariant
                raise InvalidBacktestRunError("run spec has neither strategy nor strategy_source")
            strategy = request.strategy
        # Inline provenance is assembled from the truthful TargetTape hash after validation.
        return replace(request, strategy=strategy), None

    def _run(
        self,
        run_id: str,
        spec: BacktestRunSpec,
        provenance: StrategyProvenance,
    ) -> None:
        record = self._record(run_id)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - resolved before the thread starts
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        try:
            # tape 단계: 원시 관측 로딩 + 팩터 평가 + TargetTape 컴파일. 실데이터에서 실행 시간의
            # 대부분을 차지하므로 취소 콜백을 파이프라인 checkpoint 에 그대로 건다.
            self._update(record, RunStatus.RUNNING, 0.02, "tape", "Compiling target tape")
            try:
                preview = self._portfolio_design.run_pipeline(
                    PortfolioPreviewRequest(strategy),
                    options=PortfolioPipelineOptions(require_engine_compatible=True),
                    cancelled=record.cancellation.is_set,
                ).preview
            except PortfolioPipelineCancelledError as error:
                raise RunCancelledError("run cancelled while compiling target tape") from error
            tape = preview.tape
            if tape.strategy_hash != provenance.spec_hash:
                raise RuntimeError(
                    "compiled TargetTape hash differs from the accepted strategy provenance — "
                    f"run_id={run_id} provenance={provenance.spec_hash!r} "
                    f"tape={tape.strategy_hash!r}"
                )
            self._raise_if_cancelled(record)
            self._update(record, RunStatus.RUNNING, 0.1, "data", "Loading market data")
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
            # The preview's caveats travel with the data they describe, so the manifest records
            # every warning the run was built on, not just the market-data ones.
            dataset = replace(
                dataset,
                warnings=(*_as_data_warnings(preview.warnings), *dataset.warnings),
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
            cancelled_after_commit = False
            with self._lock:
                # Completion and cancel acceptance linearize on the same lock. If cancel acquired
                # it first, the committed bundle is compensation-cleaned and never becomes
                # observable through state/result. If completion acquired it first, cancel sees a
                # terminal run and is not accepted.
                if record.cancellation.is_set():
                    cancelled_after_commit = True
                else:
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
            if cancelled_after_commit:
                self._artifact_store.discard(run_id)
                raise RunCancelledError("run cancelled during artifact commit")
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
                    record.state = replace(record.state, error=_describe_failure(error))
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


def _describe_failure(error: BaseException) -> str:
    """run 상태의 `error` 문자열.

    검증 실패는 issue 코드·경로까지 실어야 화면에서 원인을 알 수 있다.
    """

    text = f"{type(error).__name__}: {error}"
    if isinstance(error, InvalidPortfolioRequestError):
        issues = "; ".join(
            f"{issue.code}@{issue.path}: {issue.message}" for issue in error.validation.issues
        )
        return f"{text} — {issues}" if issues else text
    return text


def _as_data_warnings(messages: tuple[str, ...]) -> tuple[DataWarning, ...]:
    """Raw observation warnings as manifest rows, under one code so their origin stays readable."""
    return tuple(
        DataWarning(
            code="portfolio.raw_observation",
            message=message,
            severity=WarningSeverity.WARNING,
        )
        for message in messages
    )
