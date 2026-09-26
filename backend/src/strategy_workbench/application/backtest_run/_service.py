from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event, RLock, Thread

from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCompatibility,
    IncompatiblePortfolioRequestError,
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPipelineCancelledError,
    PortfolioPipelineOptions,
    PortfolioPreviewRequest,
    RawObservationContractError,
    RawObservationUnavailableError,
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
    RunFailureCode,
    RunProgressEvent,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
    WarningSeverity,
)
from strategy_workbench.domain.strategy.facade.specification import strategy_spec_hash

from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import BacktestDataPort, BacktestDataQuery
from .ports.outgoing.backtest_executor import (
    BacktestExecutionRequest,
    BacktestExecutorPort,
    RunCancelledError,
)

logger = logging.getLogger(__name__)

# 실행 진행 막대에서 단계가 차지하는 구간. 실데이터 실측에서 tape 단계가 실행 시간의 96~97% 를
# 쓰므로(이슈 #162, 6개월·9.5년 구간) 막대 대부분을 tape 에 주고 나머지 단계를 그 뒤에 둔다.
_TAPE_PROGRESS_START = 0.02
_TAPE_PROGRESS_END = 0.8
_DATA_PROGRESS = 0.82
_ENGINE_PROGRESS_START = 0.84
_ENGINE_PROGRESS_END = 0.92
_ARTIFACT_PROGRESS = 0.93
# 팩터 평가기는 종목마다 진행을 보고한다. 같은 작업 설명 안에서 이 폭보다 작은 상승은 이벤트로
# 남기지 않아 run 당 tape 이벤트 수를 약 100개 이하로 묶는다(SSE 재생·메모리 보호).
_MIN_TAPE_PROGRESS_STEP = 0.01

# run `error` 문자열에서 서버 절대 경로를 가린다. 어댑터 detail 이 `root=C:\...`·`/home/...`·
# `\\server\share` 를 싣는데 화면(role=alert)에 그대로 나가면 서버 레이아웃·계정명·호스트명이
# 새어 나간다. 원문은 서버 로그에. 규칙(오탐·미탐을 모두 줄이는 쪽으로):
#   - `root=`·`path=`·`file=`·`dir=`·`manifest=`(접미형 `equity_root=` 포함) 값은 모양과 무관하게
#     전부 가린다(컨테이너 `/app`, MSYS `/c/Users`, 임의 루트 포함).
#   - 드라이브(`C:\`)·UNC(`\\host\share`)·`file://` 은 어디 있든 가린다(모양만으로 경로).
#   - 키 없는 POSIX 문자열은 (a) 확장자 있는 파일(`/x/y/z.parquet`)과 (b) 계정명·서버 레이아웃을
#     담는 루트(`/home`·`/Users`·`/tmp` 등) 아래 디렉터리만 가린다 — 서드파티 예외(`OSError`,
#     duckdb)가 싣는 `'/home/<user>/...'` 를 잡되, `/data/universe_id` 같은 JSON Pointer 진단
#     경로와 `/s` 단위 표기는 파일 경로가 아니다.
#   - `https://` 등 다른 URL 은 그대로 둔다. 값 끝의 문장부호(`,`·`.`)는 경로에 넣지 않는다.
_PATH_CHARS = r"[^\s'\"`()<>]"
_POSIX_ROOTS = r"(?:home|Users|root|tmp|var|srv|opt|mnt|app|workspace|Library|private|Volumes)"
_ABSOLUTE_PATH = re.compile(
    r"(?P<url>\b(?!file://)[A-Za-z][A-Za-z0-9+.\-]*://" + _PATH_CHARS + r"*)"
    r"|(?P<key>\b\w*(?:root|path|file|dir|directory|manifest)=)(?P<value>" + _PATH_CHARS + r"+)"
    r"|(?P<path>"
    r"\bfile://" + _PATH_CHARS + r"*"
    r"|\\\\" + _PATH_CHARS + r"+"
    r"|(?<!\w)[A-Za-z]:[\\/]" + _PATH_CHARS + r"*"
    r"|(?<![\w.:])/(?:[\w.\-~%+]+/)+[\w\-~%+]+\.\w+"
    r"|(?<![\w.:])/" + _POSIX_ROOTS + r"(?:/[\w.\-~%+]*)+"
    r")",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ",.;:"


def _mask_paths(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        if match.group("url") is not None:
            return whole
        kept = whole.rstrip(_TRAILING_PUNCTUATION)
        trailing = whole[len(kept) :]
        if match.group("key") is not None:
            return match.group("key") + "<path>" + trailing
        return "<path>" + trailing

    return _ABSOLUTE_PATH.sub(replace, text)


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
        for window in spec.metric_windows:
            if window.start < strategy.data.start or window.end > strategy.data.end:
                raise InvalidBacktestRunError(
                    f"metric window exceeds strategy data range: {window.scope.value}"
                )
        # preflight 가 스펙 검증(InvalidPortfolioRequestError)·플랜 컴파일까지 대신한다.
        engine = self._portfolio_design.preflight(PortfolioPreviewRequest(strategy))
        if not engine.compatible:
            raise InvalidBacktestRunError(
                "strategy exceeds engine capabilities — " + _describe_engine_issues(engine)
            )
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
        elif provenance.spec_hash != executed_hash:  # pragma: no cover - 도달 불가 방어 분기
            # StrategyRevisionRecord.__post_init__ 이 비동결 리비전에 같은 등식을 강제하고, 동결
            # 리비전은 _resolve 가 앞에서 requires_upgrade 로 거부한다.
            raise RuntimeError(
                "saved strategy repository hash differs from the executed StrategySpec — "
                f"strategy_id={provenance.strategy_id!r} revision={provenance.revision!r} "
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
        try:
            Thread(
                target=self._run,
                args=(run_id, spec, provenance),
                name=f"backtest-{run_id}",
                daemon=True,
            ).start()
        except (RuntimeError, MemoryError) as error:
            # 스레드 상한(RuntimeError)·메모리 압박(MemoryError)으로 기동에 실패하면 레코드가
            # `queued` 로 영구 고착한다(관측자 없음). `failed` 로 종결해 폴링·목록·취소가 막다른
            # 상태를 보지 않게 한다.
            with self._lock:
                record.state = replace(
                    record.state,
                    error=_describe_failure(error),
                    error_code=_failure_code(error),
                )
                self._emit(record, RunStatus.FAILED, 0.0, "failed", "Run thread failed to start")
            logger.exception("backtest run thread failed to start — run_id=%s", run_id)
            raise
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
        # inline draft 의 provenance 는 start() 가 strategy_spec_hash(spec) 으로 만든다 —
        # TargetTape 와 같은 함수라 tape 없이 확정할 수 있고(#158), tape 단계가 다시 대조한다.
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
            self._update(
                record, RunStatus.RUNNING, _TAPE_PROGRESS_START, "tape", "Compiling target tape"
            )
            try:
                # require_engine_compatible 은 start() 의 preflight 판정을 되풀이하는 심층 방어다 —
                # 같은 순수 판정이라 정상 경로에서는 발동하지 않는다.
                preview = self._portfolio_design.run_pipeline(
                    PortfolioPreviewRequest(strategy),
                    options=PortfolioPipelineOptions(require_engine_compatible=True),
                    cancelled=record.cancellation.is_set,
                    progress=self._tape_progress(record),
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
            self._update(record, RunStatus.RUNNING, _DATA_PROGRESS, "data", "Loading market data")
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
            self._update(
                record,
                RunStatus.RUNNING,
                _ENGINE_PROGRESS_START,
                "engine",
                "Running backtest engine",
            )
            result = self._executor.execute(
                BacktestExecutionRequest(run_id, spec, tape, dataset, provenance),
                # 실행기는 자기 작업 안의 비율(0~1)을 보고한다. run 막대의 engine 구간으로 옮긴다.
                progress=lambda value, stage, message: self._update(
                    record,
                    RunStatus.RUNNING,
                    _ENGINE_PROGRESS_START
                    + min(max(value, 0.0), 1.0) * (_ENGINE_PROGRESS_END - _ENGINE_PROGRESS_START),
                    stage,
                    message,
                ),
                cancelled=record.cancellation.is_set,
            )
            self._raise_if_cancelled(record)
            self._update(
                record, RunStatus.RUNNING, _ARTIFACT_PROGRESS, "artifact", "Committing artifacts"
            )
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
            # 실패 사유 원문(절대 경로 포함)과 stack trace 는 서버 로그에만 남기고 상태에는 가린
            # 문자열을 싣는다. 어댑터 계약 위반은 사용자 오류가 아니라 어댑터 버그라 로그로 반드시
            # 드러나야 한다 — 이 로그가 유일한 진단 채널이다(HTTP 500 이 없다).
            with self._lock:
                failed_stage = record.state.stage
            logger.exception(
                "backtest run failed — run_id=%s stage=%s error_code=%s",
                run_id,
                failed_stage,
                _failure_code(error),
            )
            with self._lock:
                # 취소와 겹쳐도 실패 사유는 버리지 않는다 — "내가 취소했다" 와 "데이터가 없었다" 를
                # 화면에서 구분할 수 있어야 한다.
                record.state = replace(
                    record.state,
                    error=_describe_failure(error),
                    error_code=_failure_code(error),
                )
                if record.cancellation.is_set():
                    self._emit(
                        record,
                        RunStatus.CANCELLED,
                        record.state.progress,
                        "cancelled",
                        "Run cancelled",
                    )
                else:
                    self._emit(
                        record,
                        RunStatus.FAILED,
                        record.state.progress,
                        "failed",
                        "Run failed",
                    )

    def _tape_progress(self, record: _RunRecord) -> Callable[[float, str], None]:
        """파이프라인 진행(0~1)을 run 막대의 tape 구간으로 옮기고 이벤트 빈도를 묶는 콜백.

        작업 설명이 바뀌면 항상 남기고, 같은 설명 안에서는 `_MIN_TAPE_PROGRESS_STEP` 이상 오를 때만
        남긴다. run 스레드에서만 불리므로 마지막 보고값은 잠금 없이 이 클로저가 소유한다.
        """

        last_value = _TAPE_PROGRESS_START
        last_message: str | None = None

        def report(fraction: float, message: str) -> None:
            nonlocal last_value, last_message
            value = _TAPE_PROGRESS_START + fraction * (_TAPE_PROGRESS_END - _TAPE_PROGRESS_START)
            if message == last_message and value - last_value < _MIN_TAPE_PROGRESS_STEP:
                return
            self._update(record, RunStatus.RUNNING, value, "tape", message)
            last_value, last_message = value, message

        return report

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


def _failure_code(error: BaseException) -> RunFailureCode:
    """run `error_code`. 어휘 SoT 는 `RunFailureCode`(프론트 번역 키 `backtest.run.error.*`)."""

    if isinstance(error, InvalidPortfolioRequestError):
        return "portfolio.strategy.invalid"
    if isinstance(error, RawObservationUnavailableError):
        return "portfolio.data.unavailable"
    if isinstance(error, RawObservationContractError):
        return "portfolio.raw_observation.invalid"
    if isinstance(error, (InvalidBacktestRunError, IncompatiblePortfolioRequestError)):
        return "backtest.run.invalid"
    return "backtest.run.internal"


def _describe_failure(error: BaseException) -> str:
    """run 상태의 `error` 문자열(화면 노출용).

    검증 실패는 issue 코드·경로를, 엔진 비호환은 부족한 능력을 실어야 화면에서 원인을 알 수 있다.
    서버 절대 경로는 가린다 — 원문은 `_run` 이 로그로 남긴다.
    """

    text = f"{type(error).__name__}: {error}"
    if isinstance(error, InvalidPortfolioRequestError):
        issues = "; ".join(
            f"{issue.code}@{issue.path}: {issue.message}" for issue in error.validation.issues
        )
        text = f"{text} — {issues}" if issues else text
    elif isinstance(error, IncompatiblePortfolioRequestError):
        text = f"{text} — {_describe_engine_issues(error.compatibility)}"
    return _mask_paths(text)


def _describe_engine_issues(engine: EngineCompatibility) -> str:
    return (
        "; ".join(
            f"{issue.category}.{issue.name}={issue.support}"
            + (f" ({issue.reason})" if issue.reason else "")
            for issue in engine.issues
        )
        or "no issues reported"
    )


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
