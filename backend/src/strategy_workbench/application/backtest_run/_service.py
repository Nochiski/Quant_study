from __future__ import annotations

import logging
import re
from collections import deque
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
from strategy_workbench.domain.backtest.facade.environment import (
    MissingRunEnvironmentError,
    ResearchWindowViolationError,
    participation_history_sessions,
    require_environment,
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

from ._gc_policy import full_collections_suspended
from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import BacktestDataPort, BacktestDataQuery
from .ports.outgoing.backtest_executor import (
    BacktestExecutionRequest,
    BacktestExecutorPort,
    EquityWipedOutError,
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

# 한꺼번에 계산하는 run 수의 상한. 실데이터 긴 구간 run 한 건이 CPU 수백 초·RSS 약 5GB 를 쓰므로
# (#158·#161) 넘는 run 은 스레드 없이 `queued` 로 접수 순서대로 기다린다.
MAX_CONCURRENT_RUNS = 2

# run `error` 문자열에서 서버 절대 경로를 가린다. 서버 경로를 담을 수 있는 서드파티 원문(파일·DB
# 입출력 예외)이 응답으로 나가는 곳은 run `error` 하나다 — 우리가 쓰는 문장(어댑터 detail 등)은
# 처음부터 경로 없이 쓰지만(#163), duckdb·`OSError` 원문은 `C:\...`·`/home/...`·`\\server\share` 를
# 담아 화면(role=alert)에 서버 레이아웃·계정명·호스트명을 흘린다. 원문은 서버 로그에. 규칙(오탐·
# 미탐을 모두 줄이는 쪽으로):
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


class MissingBacktestRunEnvironmentError(InvalidBacktestRunError):
    """실행 요청에 실행 설정이 없다(spec D3·D6, P2-03).

    `InvalidBacktestRunError` 의 하위 타입으로 두되 HTTP 코드를 따로 준다. 프론트는 이 한
    코드를 보고 "실행 설정을 채우라"는 화면(P3-02 실행 설정 패널)으로 보내야 하고,
    `backtest.run.invalid` 에 묻으면 문장 파싱 말고는 구분할 방법이 없다. 하위 타입이므로
    run 스레드의 방어 분기(`_run`)와 실패 코드 분류는 기존 `backtest.run.invalid` 를 그대로
    쓴다 — 시작 요청이 앞에서 거르므로 그 경로로는 도달하지 않는다.
    """


class BacktestResearchWindowViolationError(InvalidBacktestRunError):
    """측정 시작일이 연구 구간 밖이다(spec D1, V1-01).

    `MissingBacktestRunEnvironmentError` 와 같은 이유로 HTTP 코드를 따로 준다. 프론트는 이 코드를
    보고 봉인 구간과 연구 하한을 안내해야 하고, `backtest.run.invalid` 에 묻으면 문장 파싱 말고는
    구분할 방법이 없다. 날짜는 도메인 오류(`violation`)가 싣는다.
    """

    def __init__(self, violation: ResearchWindowViolationError) -> None:
        super().__init__(str(violation))
        self.violation = violation


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


# 레코드는 정체성으로 가린다(`eq=False`) — 대기열에서 꺼내고 지울 때 같은 run 만 맞아야 한다.
@dataclass(eq=False)
class _RunRecord:
    state: BacktestRunState
    request: BacktestRunSpec
    # 실행할 spec — `strategy` 를 해소하고 실행 설정을 박은 것. `request` 는 다시 제출할 수 있는
    # 원본이라 따로 둔다.
    spec: BacktestRunSpec
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
        max_concurrent_runs: int = MAX_CONCURRENT_RUNS,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError(
                "max_concurrent_runs must be at least 1 — "
                f"max_concurrent_runs={max_concurrent_runs!r}"
            )
        self._portfolio_design = portfolio_design
        self._strategy_repository = strategy_repository
        self._data_source = data_source
        self._executor = executor
        self._artifact_store = artifact_store
        self._new_id = new_id
        self._now = now
        self._max_concurrent_runs = max_concurrent_runs
        self._records: dict[str, _RunRecord] = {}
        self._waiting: deque[_RunRecord] = deque()
        self._running = 0
        self._next_accepted_sequence = 0
        self._lock = RLock()

    def start(self, request: BacktestRunSpec) -> BacktestStartResponse:
        """실행 요청을 받아 즉시 `QUEUED` 로 접수한다.

        요청 스레드에서는 데이터를 읽지 않는 검사(스펙 해석·검증·metric window·엔진 호환성·저장
        리비전 해시)만 하고, TargetTape 계산은 run 스레드의 `tape` 단계로 넘긴다. 이전에는 tape 를
        여기서 동기로 만들어 긴 구간에서 응답이 수 분 이상 걸리고 취소 수단이 없었다(이슈 #158).

        같은 입력으로 도는 run 이 있으면 새 run 을 만들지 않고 그 run 을 돌려주고, 도는 run 이
        상한(`MAX_CONCURRENT_RUNS`)에 차 있으면 `queued` 로 기다리게 한다(이슈 #161).
        """

        spec, provenance = self._resolve(request)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - _resolve always fills it
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        # 실행 설정을 **preflight 앞에서** 한 번 확정해 run spec 에 박는다. 매니페스트·엔진·
        # tape·데이터 조회와 preflight 가 모두 같은 객체를 읽어야 명시 `environment` 가 조용히
        # 무시되지 않는다(P2-01 P0). 1.2 는 문서에서 만드는 대체 경로가 없으므로 해소 단계가
        # 사라졌고, 그래서 두 호출부가 다른 값을 볼 여지도 없다(P2-03 결정 항목).
        try:
            environment = require_environment(
                spec.environment, requested_by=f"backtest.run({strategy.title!r})"
            )
        except MissingRunEnvironmentError as error:
            raise MissingBacktestRunEnvironmentError(str(error)) from error
        except ResearchWindowViolationError as error:
            raise BacktestResearchWindowViolationError(error) from error
        spec = replace(spec, environment=environment)
        # preflight 가 스펙 검증(InvalidPortfolioRequestError)·플랜 컴파일까지 대신한다.
        engine = self._portfolio_design.preflight(
            PortfolioPreviewRequest(strategy, environment=environment)
        )
        if not engine.compatible:
            raise InvalidBacktestRunError(
                "strategy exceeds engine capabilities — " + _describe_engine_issues(engine)
            )
        for window in spec.metric_windows:
            if window.start < environment.start or window.end > environment.end:
                raise InvalidBacktestRunError(
                    "metric window exceeds the run range — "
                    f"scope={window.scope.value} window={window.start}..{window.end} "
                    f"run={environment.start}..{environment.end}"
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
        with self._lock:
            # 같은 입력(실행할 spec·실행 설정·실행 옵션·provenance)으로 도는 run 이 있으면 그 run 을
            # 돌려준다. 재클릭·새로고침 뒤 재시작·프록시 재시도가 같은 tape 를 겹쳐 계산하지 않게
            # 한다(#161). spec 의 `==` 는 1 과 1.0 을 같게 보지만 provenance 의 `spec_hash` 는
            # 가르고, 매니페스트도 provenance 를 기록하므로 둘 다 같아야 한다. 데이터 snapshot·
            # 엔진·지표 레지스트리 판본은 프로세스 안에서 고정이라 같은 입력이면 결과도 같다.
            # 취소를 요청한 run 은 곧 끝나므로 잇지 않는다.
            for existing in self._records.values():
                if (
                    existing.state.status in (RunStatus.QUEUED, RunStatus.RUNNING)
                    and existing.spec == spec
                    and existing.provenance == provenance
                ):
                    return BacktestStartResponse(existing.state)
            run_id = self._new_id()
            created = self._now()
            message = (
                "Run accepted"
                if self._running < self._max_concurrent_runs
                else "Waiting for a free run slot"
            )
            record = _RunRecord(
                state=BacktestRunState(
                    run_id=run_id,
                    status=RunStatus.QUEUED,
                    progress=0.0,
                    stage="queued",
                    message=message,
                    created_at=created,
                    updated_at=created,
                ),
                request=request,
                spec=spec,
                provenance=provenance,
                accepted_sequence=self._next_accepted_sequence,
                events=[],
                cancellation=Event(),
            )
            self._next_accepted_sequence += 1
            self._records[run_id] = record
            self._emit(record, RunStatus.QUEUED, 0.0, "queued", message)
            # 응답은 접수 시점 상태다. 스레드를 띄운 뒤 record.state 를 읽으면 이미 tape 단계로
            # 바뀌어 있을 수 있어 202 본문의 status 가 비결정이 된다.
            accepted = record.state
            self._waiting.append(record)
            self._dispatch()
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
            if record in self._waiting:
                # 스레드가 없는 대기 run 은 여기서 끝낸다. 자리가 날 때까지 `cancel_requested` 로
                # 남겨 두지 않는다(#161).
                self._waiting.remove(record)
                self._emit(
                    record, RunStatus.CANCELLED, record.state.progress, "cancelled", "Run cancelled"
                )
                return record.state
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

    def _dispatch(self) -> None:
        """자리가 비는 만큼 대기열 앞의 run 을 스레드로 띄운다. `self._lock` 안에서 부른다.

        대기 run 에는 스레드가 없다 — run 마다 스레드를 먼저 띄워 두면 스레드 수가 상한 없이 는다
        (#161).
        """

        while self._waiting and self._running < self._max_concurrent_runs:
            record = self._waiting.popleft()
            try:
                Thread(
                    target=self._run,
                    args=(record,),
                    name=f"backtest-{record.state.run_id}",
                    daemon=True,
                ).start()
            except (RuntimeError, MemoryError) as error:
                # 스레드 상한(RuntimeError)·메모리 압박(MemoryError)으로 기동에 실패하면 레코드가
                # `queued` 로 영구 고착한다(관측자 없음). `failed` 로 종결해 폴링·목록·취소가 막다른
                # 상태를 보지 않게 하고 다음 run 으로 넘어간다.
                record.state = replace(
                    record.state,
                    error=_describe_failure(error),
                    error_code=_failure_code(error),
                )
                self._emit(record, RunStatus.FAILED, 0.0, "failed", "Run thread failed to start")
                logger.exception(
                    "backtest run thread failed to start — run_id=%s", record.state.run_id
                )
            else:
                self._running += 1

    def _run(self, record: _RunRecord) -> None:
        """run 스레드 본문. 전체 수집(2세대)을 run 이 끝날 때까지 미룬다(이슈 #196).

        tape 단계가 만드는 수백만 개의 오래 사는 객체 때문에 전체 수집이 매번 수 초씩 GIL 을 쥐고
        다른 HTTP 요청까지 세웠다. 범위와 복원 규칙은 `_gc_policy` 가 소유한다. 끝나면 자리를
        내놓고 대기열의 다음 run 을 띄운다(#161).
        """

        try:
            with full_collections_suspended():
                self._execute(record)
        finally:
            with self._lock:
                self._running -= 1
                self._dispatch()

    def _execute(self, record: _RunRecord) -> None:
        run_id = record.state.run_id
        spec = record.spec
        provenance = record.provenance
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - resolved before the thread starts
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        environment = spec.environment
        if environment is None:  # pragma: no cover - start() pins it before the thread starts
            raise MissingBacktestRunEnvironmentError(
                f"resolved run spec has no run environment — run_id={run_id}"
            )
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
                    PortfolioPreviewRequest(strategy, environment=environment),
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
                    start=environment.start,
                    end=environment.end,
                    security_ids=security_ids,
                    benchmark_security_id=spec.benchmark_security_id,
                    history_sessions_before_start=participation_history_sessions(environment),
                )
            )
            # The preview's caveats travel with the data they describe, so the manifest records
            # every warning the run was built on, not just the market-data ones.
            # tape 컴파일 경고는 컴파일러 코드를 그대로 쓴다(이슈 #203). 원시 관측 코드로 묶으면
            # 결과 화면에서 출처가 틀린다.
            dataset = replace(
                dataset,
                warnings=(
                    *_as_data_warnings(preview.warnings),
                    *(
                        DataWarning(
                            code=item.code.value,
                            message=item.message,
                            severity=WarningSeverity.WARNING,
                        )
                        for item in preview.tape.warnings
                    ),
                    *dataset.warnings,
                ),
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
                    record.state = replace(record.state, artifact_sha256=commit.sha256)
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
    if isinstance(error, EquityWipedOutError):
        return "backtest.run.equity_wiped_out"
    return "backtest.run.internal"


def _describe_failure(error: BaseException) -> str:
    """run 상태의 `error` 문자열(화면 노출용).

    검증 실패는 issue 코드·경로를, 엔진 비호환은 부족한 능력을 실어야 화면에서 원인을 알 수 있다.
    서버 절대 경로는 가린다 — 원문은 `_execute` 가 로그로 남긴다.
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
