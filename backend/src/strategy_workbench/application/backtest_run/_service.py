from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, fields, replace
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
    RunEnvironment,
    cost_history_sessions,
    require_environment,
)
from strategy_workbench.domain.backtest.facade.runs import (
    AdmissionRejectionCode,
    BacktestCancelResult,
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
    run_input_key,
)
from strategy_workbench.domain.backtest.facade.trials import (
    BlockedTrialAttempt,
    TrialLedger,
    TrialPreview,
    preview_trial,
    representative_sharpe,
    summarize_trial_ledger,
    trial_key,
)
from strategy_workbench.domain.strategy.facade.specification import (
    InvalidParameterValueError,
    StrategySpec,
    resolve_parameter_values,
    strategy_spec_hash,
)

from ._gc_policy import full_collections_suspended
from ._scheduler import RunQueue, experiment_slots
from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import (
    BacktestDataNotReadyError,
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataUnavailableError,
)
from .ports.outgoing.backtest_executor import (
    BacktestExecutionRequest,
    BacktestExecutorPort,
    EquityWipedOutError,
    RunCancelledError,
)
from .ports.outgoing.run_repository import BacktestRunRepositoryPort, BacktestRunSummary, RunKind

logger = logging.getLogger(__name__)

# 실행 진행 막대에서 단계가 차지하는 구간. 실데이터 실측에서 tape 단계가 실행 시간의 96~97% 를
# 쓰므로(이슈 #162, 6개월·9.5년 구간) 막대 대부분을 tape 에 주고 나머지 단계를 그 뒤에 둔다.
_TAPE_PROGRESS_START = 0.02
_TAPE_PROGRESS_END = 0.8
_DATA_PROGRESS = 0.82
_ENGINE_PROGRESS_START = 0.84
_ENGINE_PROGRESS_END = 0.92
_ARTIFACT_PROGRESS = 0.93
# run 하나가 메모리에 들고 있는 진행 이벤트 수의 상한(검증 랩 spec D3). 이벤트는 저장하지 않으므로
# SSE 재생이 이만큼 뒤처진 구독자는 앞 이벤트를 건너뛴다 — 상태는 폴링·목록이 저장소에서 읽는다.
_EVENT_RING_SIZE = 256
# 팩터 평가기는 종목마다 진행을 보고한다. 같은 작업 설명 안에서 이 폭보다 작은 상승은 이벤트로
# 남기지 않아 run 당 tape 이벤트 수를 약 100개 이하로 묶는다(SSE 재생·메모리 보호).
_MIN_TAPE_PROGRESS_STEP = 0.01

# 동시 실행 슬롯 수의 기본값(설정 `run_slots`, 환경 변수 `STRATEGY_WORKBENCH_RUN_SLOTS` 가
# 덮어쓴다). 실데이터 긴 구간 run 한 건이 CPU 수백 초·RSS 약 5GB 를 쓰므로(#158·#161) 넘는 run 은
# 스레드 없이 `queued` 로 기다린다. 배정 순서는 `_scheduler.py` 가 정한다.
DEFAULT_RUN_SLOTS = 2

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


class NoPositionsError(RuntimeError):
    """tape 가 한 번도 종목을 고르지 않았다 — 기간 안에 리밸런싱일이 없거나 선정이 비었다(#360).

    요청이 틀린 것도 서버 오류도 아니라 이 설정의 결과라 전용 실패 코드로 끝난다.
    """


class BacktestParameterValueError(InvalidBacktestRunError):
    """실행 요청의 파라미터 값을 전략 문서 정의로 해소할 수 없다(spec D4, V3-02).

    `backtest.run.invalid` 에 묻으면 문장 파싱 말고는 구분할 방법이 없어 HTTP 코드를 따로 준다.
    어느 파라미터인지는 `parameter_id` 가 싣는다.
    """

    def __init__(self, error: InvalidParameterValueError) -> None:
        super().__init__(str(error))
        self.parameter_id = error.parameter_id


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


@dataclass(frozen=True)
class RunAdmission:
    """접수 판정을 통과한 요청. `spec` 은 전략·실행 설정·파라미터 값을 해소한 실행 spec 이다."""

    spec: BacktestRunSpec
    provenance: StrategyProvenance
    lineage_id: str | None


class BacktestResultNotReadyError(RuntimeError):
    pass


# 이 프로세스가 접수한 run 의 메모리 사본. 목록·상태·요청의 정본은 저장소, 결과의 정본은 산출물
# 저장소이고, 이 사본은 저장하지 않는 것(진행률·진행 이벤트 링·취소 신호)을 든다. 정체성으로
# 가린다(`eq=False`) — 대기열에서 꺼내고 지울 때 같은 run 만 맞아야 한다.
@dataclass(eq=False)
class _RunRecord:
    state: BacktestRunState
    # 실행할 spec — `strategy` 를 해소하고 실행 설정을 박은 것. 다시 제출할 수 있는 원본 요청은
    # 저장소가 가진다.
    spec: BacktestRunSpec
    provenance: StrategyProvenance
    events: deque[RunProgressEvent]
    cancellation: Event
    # 같은 입력 잇기의 기준 — 실행 입력(`run_input_key`)·계열·시도 키. 계열이나 시도 키가 다르면
    # 잇지 않아 run 하나가 원장 행 하나로 남는다(잇기가 N 을 빠뜨리지 않는다).
    join_key: tuple[str, str | None, str]
    # 이 run 을 쓰는 소유자. None 은 사용자 단일 실행, 문자열은 실험 id 다. 모두 취소해야 취소된다.
    owners: set[str | None]
    # 실험 몫 슬롯을 쓰고 있는가(띄울 때 정한다).
    experiment_slot: bool = False


class BacktestRunService:
    def __init__(
        self,
        portfolio_design: PortfolioDesignService,
        strategy_repository: StrategyRepositoryPort,
        data_source: BacktestDataPort,
        executor: BacktestExecutorPort,
        artifact_store: BacktestArtifactStorePort,
        *,
        run_repository: BacktestRunRepositoryPort,
        new_id: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_slots: int = DEFAULT_RUN_SLOTS,
    ) -> None:
        if run_slots < 1:
            raise ValueError(f"run_slots must be at least 1 — run_slots={run_slots!r}")
        self._portfolio_design = portfolio_design
        self._strategy_repository = strategy_repository
        self._data_source = data_source
        self._executor = executor
        self._artifact_store = artifact_store
        self._repository = run_repository
        self._new_id = new_id
        self._now = now
        self._run_slots = run_slots
        self._records: dict[str, _RunRecord] = {}
        # 끝난 run 은 최근 것만 메모리에 둔다(끝난 순). 상태·요청·결과의 정본은 저장소·산출물이다.
        self._finished: deque[str] = deque()
        self._waiting: RunQueue[_RunRecord] = RunQueue()
        self._running = 0
        self._running_experiments = 0
        self._lock = RLock()
        self._close_interrupted_runs()

    def admit(self, request: BacktestRunSpec) -> RunAdmission:
        """시작과 같은 판정(preflight·엔진 호환성 포함)을 타되 접수하지 않는다.

        실험 기반 요청 검사(검증 랩 V3-03)가 쓴다. 봉인 겹침 거절은 실행 요청이 아니므로 봉인 원장에
        남기지 않는다.
        """
        return self._admit(request, record_blocked=False)

    def start(
        self,
        request: BacktestRunSpec,
        *,
        owner: str | None = None,
        trial_key_override: str | None = None,
    ) -> BacktestStartResponse:
        """실행 요청을 받아 즉시 `QUEUED` 로 접수한다.

        `owner` 는 실험 id 다(없으면 사용자 단일 실행). 대기 순서의 레인이고, 같은 입력을 이은
        소유자가 모두 취소해야 run 이 취소된다(검증 랩 spec D6).

        `trial_key_override` 는 실험 유스케이스가 정한 시도 키다(창 날짜 대신 실험 기반 실행
        설정으로 낸다, V3-03). 없으면 이 실행 spec 의 시도 키를 원장에 적는다.

        요청 스레드에서는 데이터를 읽지 않는 검사(스펙 해석·검증·metric window·엔진 호환성·저장
        리비전 해시)만 하고, TargetTape 계산은 run 스레드의 `tape` 단계로 넘긴다. 이전에는 tape 를
        여기서 동기로 만들어 긴 구간에서 응답이 수 분 이상 걸리고 취소 수단이 없었다(이슈 #158).

        같은 입력으로 도는 run 이 있으면 새 run 을 만들지 않고 그 run 을 돌려주고, 슬롯이 차 있으면
        `queued` 로 기다리게 한다(이슈 #161).
        """
        admission = self._admit(request, record_blocked=True)
        spec, provenance = admission.spec, admission.provenance
        with self._lock:
            # 같은 입력(실행 지문의 요청 칸 `run_input_key`·provenance)·같은 계열·같은 시도 키로
            # 도는 run 이 있으면 그 run 을 돌려준다. 재클릭·새로고침 뒤 재시작·프록시 재시도·실험
            # trial 이 같은 tape 를 겹쳐 계산하지 않게 한다(#161, spec D6). provenance 는
            # 매니페스트가 기록하므로 같아야 하고, 계열·시도 키가 다르면 원장 행을 따로 적어야
            # 하므로 잇지 않는다. 데이터 snapshot·엔진 규칙·지표 레지스트리 판본은 프로세스 안에서
            # 고정이라 같은 입력이면 결과도 같다. 취소를 요청한 run 은 곧 끝나므로 잇지 않는다.
            key = trial_key_override or trial_key(spec)
            join_key = (run_input_key(spec), admission.lineage_id, key)
            for existing in self._records.values():
                if (
                    existing.state.status in (RunStatus.QUEUED, RunStatus.RUNNING)
                    and existing.join_key == join_key
                    and existing.provenance == provenance
                ):
                    existing.owners.add(owner)
                    # 사용자가 이었으면 단일 실행 레인, 아니면 멈추지 않은 실험 레인으로 옮긴다.
                    self._relane(existing)
                    self._dispatch()
                    return BacktestStartResponse(existing.state)
            run_id = self._new_id()
            created = self._now()
            slot_free = self._running < self._run_slots and (
                owner is None or self._running_experiments < experiment_slots(self._run_slots)
            )
            message = "Run accepted" if slot_free else "Waiting for a free run slot"
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
                spec=spec,
                provenance=provenance,
                events=deque(maxlen=_EVENT_RING_SIZE),
                cancellation=Event(),
                join_key=join_key,
                owners={owner},
            )
            self._emit(record, RunStatus.QUEUED, 0.0, "queued", message)
            # 저장이 실패하면 접수하지 않는다 — 기록 없는 run 이 돌면 재시작 뒤 흔적이 없다.
            self._repository.add(
                record.state,
                provenance,
                request,
                lineage_id=admission.lineage_id,
                trial_key=key,
            )
            self._records[run_id] = record
            # 응답은 접수 시점 상태다. 스레드를 띄운 뒤 record.state 를 읽으면 이미 tape 단계로
            # 바뀌어 있을 수 있어 202 본문의 status 가 비결정이 된다.
            accepted = record.state
            self._waiting.push(record, self._lane(record))
            self._dispatch()
        return BacktestStartResponse(accepted)

    def _admit(self, request: BacktestRunSpec, *, record_blocked: bool) -> RunAdmission:
        """요청을 실행할 spec 으로 해소하고 접수 판정을 모두 한다. 데이터는 읽지 않는다."""
        spec, provenance = self._resolve(request)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - _resolve always fills it
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        lineage_id = self._lineage(request, provenance)
        # 실행 설정을 **preflight 앞에서** 한 번 확정해 run spec 에 박는다. 매니페스트·엔진·
        # tape·데이터 조회와 preflight 가 모두 같은 객체를 읽어야 명시 `environment` 가 조용히
        # 무시되지 않는다(P2-01 P0). 1.2 는 문서에서 만드는 대체 경로가 없으므로 해소 단계가
        # 사라졌고, 그래서 두 호출부가 다른 값을 볼 여지도 없다(P2-03 결정 항목).
        environment = self._pin_environment(
            spec, strategy, lineage_id, record_blocked=record_blocked
        )
        spec = replace(spec, environment=environment)
        # preflight 가 스펙 검증(InvalidPortfolioRequestError)·플랜 컴파일까지 대신한다.
        engine = self._portfolio_design.preflight(
            PortfolioPreviewRequest(strategy, environment=environment)
        )
        if not engine.compatible:
            raise InvalidBacktestRunError(
                "strategy exceeds engine capabilities — " + _describe_engine_issues(engine)
            )
        # 파라미터 값은 문서 검증(preflight) 뒤에 해소한다. 해소 결과가 실행 spec 에 박혀 지문·같은
        # 입력 잇기·매니페스트·tape 가 모두 같은 값을 본다(spec D4).
        try:
            parameter_values = resolve_parameter_values(strategy.parameters, spec.parameter_values)
        except InvalidParameterValueError as error:
            raise BacktestParameterValueError(error) from error
        spec = replace(spec, parameter_values=parameter_values)
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
        return RunAdmission(spec, provenance, lineage_id)

    def preview_trial(self, request: BacktestRunSpec) -> TrialPreview:
        """실행 전 미리 계산 — 이 요청이 결과를 내면 계열 N 에 새로 드는가(검증 랩 spec D2).

        `start` 와 같은 해소·계열·실행 설정 판정을 타서 같은 시도 키를 낸다. 봉인 겹침은 같은 422 로
        거절하되 실행 요청이 아니므로 봉인 원장에 남기지 않는다. 전략 검증(preflight)은 하지 않는다.
        """
        spec, provenance = self._resolve(request)
        strategy = spec.strategy
        if strategy is None:  # pragma: no cover - _resolve always fills it
            raise InvalidBacktestRunError("resolved run spec has no strategy")
        lineage_id = self._lineage(request, provenance)
        environment = self._pin_environment(spec, strategy, lineage_id, record_blocked=False)
        try:
            key = trial_key(replace(spec, environment=environment))
        except InvalidParameterValueError as error:
            raise BacktestParameterValueError(error) from error
        return preview_trial(None if lineage_id is None else self.trial_ledger(lineage_id), key)

    def request_ledger(self, request: BacktestRunSpec) -> TrialLedger | None:
        """요청이 속한 계열의 원장(`_lineage` 규칙). 계열이 없으면 None. 전략 검증은 하지 않는다."""
        _spec, provenance = self._resolve(request)
        lineage_id = self._lineage(request, provenance)
        return None if lineage_id is None else self.trial_ledger(lineage_id)

    def trial_ledger(self, lineage_id: str) -> TrialLedger:
        """계열 원장 — 시도 묶음·재확인·N 제외 실행·차단한 시도.

        합쳐진 계열을 물으면 남은 계열의 원장이다. 저장된 전략이 아니면 `StrategyNotFoundError`.
        """
        self._strategy_repository.get(lineage_id)
        records = self._repository.trial_ledger(lineage_id)
        return summarize_trial_ledger(
            records.lineage_id, records.merged_lineage_ids, records.entries, records.blocked
        )

    def merge_lineages(self, source_id: str, target_id: str) -> TrialLedger:
        """`source_id` 계열을 `target_id` 계열에 합친다. 되돌릴 수 없다(spec D2).

        두 계열 모두 저장된 전략이어야 한다(없으면 `StrategyNotFoundError`).
        """
        for strategy_id in (source_id, target_id):
            self._strategy_repository.get(strategy_id)
        self._repository.merge_lineages(source_id, target_id, merged_at=self._now())
        return self.trial_ledger(target_id)

    def schedule(self, owner: str, *, paused: bool, weight: int) -> None:
        """실험(`owner`)의 대기 run 을 멈추거나 풀고, 한 차례에 배정할 수(우선순위)를 정한다.

        도는 run 은 끝까지 돈다. `RunStatus` 는 바뀌지 않고 멈춘 run 은 `queued` 로 남는다(spec D6).
        """
        with self._lock:
            self._waiting.configure(owner, paused=paused, weight=weight)
            for record in self._records.values():
                if owner in record.owners and len(record.owners) > 1:
                    self._relane(record)
            self._dispatch()

    def states(self, run_ids: Collection[str]) -> dict[str, BacktestRunState]:
        """여러 run 의 상태를 한 번에 — 이 프로세스가 도는 run 은 메모리, 나머지는 저장소 한 번."""
        with self._lock:
            known = {
                run_id: self._records[run_id].state for run_id in run_ids if run_id in self._records
            }
        stored = self._repository.states([run_id for run_id in run_ids if run_id not in known])
        return known | stored

    def state(self, run_id: str) -> BacktestRunState:
        """이 프로세스가 도는 run 은 메모리의 진행률까지, 나머지는 저장소의 마지막 상태."""
        with self._lock:
            record = self._records.get(run_id)
            if record is not None:
                return record.state
        return self._repository.get(run_id).run

    def request(self, run_id: str) -> BacktestRunSpec:
        """접수한 원본 요청을 돌려준다(저장소가 정본).

        해소하지 않은 요청은 전략 출처를 정확히 하나 실으므로 그대로 다시 제출해도 된다. 해소한
        실행 spec 은 불변 결과 매니페스트에 기록될 때까지 내부 사항으로 둔다.
        """

        return self._repository.request(run_id)

    def list_runs(
        self,
        page: PageRequest,
        *,
        strategy_id: str | None = None,
        kind: RunKind | None = None,
    ) -> Page[BacktestRunSummary]:
        """최근 접수 순. 목록은 저장소가 정하고, 이 프로세스가 도는 run 은 메모리 상태로 덮는다."""

        stored = self._repository.list(page, strategy_id=strategy_id, kind=kind)
        with self._lock:
            return replace(
                stored,
                items=tuple(
                    replace(item, run=self._records[item.run.run_id].state)
                    if item.run.run_id in self._records
                    else item
                    for item in stored.items
                ),
            )

    def result(self, run_id: str) -> BacktestRunResult:
        """완료된 run 의 결과를 산출물 저장소에서 읽는다(V1-04).

        메모리에 결과를 들지 않는다 — 이 프로세스가 끝낸 run 과 재시작 전에 끝난 run 이 같은 길로
        읽힌다. 산출물은 `COMPLETED` 전이보다 먼저 커밋되므로 완료 상태면 파일이 있다.
        """
        state = self.state(run_id)
        if state.status is not RunStatus.COMPLETED or state.artifact_sha256 is None:
            raise BacktestResultNotReadyError(
                f"backtest result is not ready: run_id={run_id} status={state.status}"
            )
        return self._artifact_store.load(run_id, sha256=state.artifact_sha256)

    def cancel(self, run_id: str, *, owner: str | None = None) -> BacktestCancelResult:
        """`owner`(없으면 사용자)가 이 run 에서 빠진다. 남은 소유자가 없을 때만 run 을 취소한다.

        실험만 쓰는 run 은 사용자가 취소해도 돌고(`kept_by_owners`), 그 실험을 취소하면 멈춘다.
        남은 소유자가 있으면 대기 run 의 레인을 그들로 다시 고른다.
        """
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                # 이 프로세스가 돌리지 않은 run 은 재시작 때 이미 종결됐다.
                return _cancel_result(self.state(run_id), kept_by_owners=False)
            record.owners.discard(owner)
            if record.state.status in _SETTLED:
                return _cancel_result(record.state, kept_by_owners=False)
            if record.owners:
                self._relane(record)
                self._dispatch()
                return _cancel_result(record.state, kept_by_owners=True)
            record.cancellation.set()
            if record in self._waiting:
                # 스레드가 없는 대기 run 은 여기서 끝낸다. 자리가 날 때까지 `cancel_requested` 로
                # 남겨 두지 않는다(#161).
                self._waiting.remove(record)
                self._emit(
                    record, RunStatus.CANCELLED, record.state.progress, "cancelled", "Run cancelled"
                )
            else:
                self._emit(
                    record,
                    RunStatus.CANCEL_REQUESTED,
                    record.state.progress,
                    "cancellation",
                    "Cancellation requested",
                )
            return _cancel_result(record.state, kept_by_owners=False)

    def _lane(self, record: _RunRecord) -> str | None:
        """대기 run 의 레인. 사용자가 이었으면 단일 실행 레인이고, 아니면 일시정지하지 않은 소유
        실험의 레인이다 — 일시정지는 그 run 의 소유 실험이 모두 멈췄을 때만 run 을 붙잡는다."""
        if None in record.owners:
            return None
        owners = sorted(owner for owner in record.owners if owner is not None)
        return next((owner for owner in owners if not self._waiting.is_paused(owner)), owners[0])

    def _relane(self, record: _RunRecord) -> None:
        """소유자나 일시정지가 바뀐 대기 run 을 맞는 레인으로 옮긴다. `self._lock` 안에서 부른다."""
        self._waiting.move(record, self._lane(record))

    def events(self, run_id: str, *, after_sequence: int = -1) -> tuple[RunProgressEvent, ...]:
        """메모리 링의 진행 이벤트. 이 프로세스가 돌리지 않은 run 은 이벤트가 없다."""
        with self._lock:
            record = self._records.get(run_id)
            if record is not None:
                return tuple(event for event in record.events if event.sequence > after_sequence)
        self._repository.get(run_id)
        return ()

    def _close_interrupted_runs(self) -> None:
        """지난 프로세스에서 끝나지 못한 run 을 `failed` + `backtest.run.interrupted` 로 닫는다.

        run 스레드는 프로세스와 함께 사라지므로 `queued`·`running` 으로 남은 기록은 다시 돌 수 없다.
        그대로 두면 목록·폴링이 끝나지 않는 run 을 영원히 본다(검증 랩 spec D3). 저장소 파일 하나를
        서버 프로세스 하나가 쓴다는 전제다 — 두 프로세스가 같은 파일을 열면 뒤에 뜬 쪽이 앞의 도는
        run 을 닫는다.
        """
        for state in self._repository.unfinished():
            self._repository.update(
                replace(
                    state,
                    status=RunStatus.FAILED,
                    stage="failed",
                    message="Run interrupted by a server restart",
                    error=(
                        "run did not finish before the server stopped — "
                        f"run_id={state.run_id} status={state.status} stage={state.stage}"
                    ),
                    error_code="backtest.run.interrupted",
                    updated_at=self._now(),
                )
            )
            logger.warning(
                "backtest run closed as interrupted — run_id=%s status=%s stage=%s",
                state.run_id,
                state.status,
                state.stage,
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

    def _lineage(
        self, request: BacktestRunSpec, provenance: StrategyProvenance | None
    ) -> str | None:
        """실행이 속한 계열. 저장 리비전은 그 전략, 인라인 초안은 요청이 실은 계열이다."""
        lineage_id = request.lineage_strategy_id
        if provenance is not None:
            if lineage_id not in (None, provenance.strategy_id):
                raise InvalidBacktestRunError(
                    "lineage_strategy_id differs from the saved revision strategy — "
                    f"lineage_strategy_id={lineage_id} strategy_id={provenance.strategy_id}"
                )
            return provenance.strategy_id
        if lineage_id is not None:
            try:
                self._strategy_repository.get(lineage_id)
            except StrategyNotFoundError as error:
                raise StrategyReferenceNotFoundError(
                    f"lineage strategy not found — lineage_strategy_id={lineage_id}"
                ) from error
        return lineage_id

    def _pin_environment(
        self,
        spec: BacktestRunSpec,
        strategy: StrategySpec,
        lineage_id: str | None,
        *,
        record_blocked: bool,
    ) -> RunEnvironment:
        """요청의 실행 설정을 확정한다.

        없거나 연구 구간 밖이면 domain 오류(`MissingRunEnvironmentError`·
        `ResearchWindowViolationError`)를 그대로 올리고, 접수 거절 코드는 `rejection_code` 가 준다.
        봉인 겹침 거절은 `record_blocked` 면 봉인 원장에 남긴다(spec D11).
        """
        try:
            return require_environment(
                spec.environment, requested_by=f"backtest.run({strategy.title!r})"
            )
        except ResearchWindowViolationError:
            if record_blocked and spec.environment is not None:
                self._record_blocked(spec, strategy, spec.environment, lineage_id)
            raise

    def _record_blocked(
        self,
        spec: BacktestRunSpec,
        strategy: StrategySpec,
        environment: RunEnvironment,
        lineage_id: str | None,
    ) -> None:
        # 기록이 실패해도 거절은 그대로 돌려준다 — 막는 것이 먼저다.
        try:
            key = trial_key(spec)
        except InvalidParameterValueError:
            # 파라미터 값을 해소할 수 없는 요청은 전략이 확정되기 전의 거절이라 남기지 않는다.
            return
        try:
            self._repository.record_blocked_attempt(
                BlockedTrialAttempt(
                    blocked_at=self._now(),
                    lineage_id=lineage_id,
                    trial_key=key,
                    spec_hash=strategy_spec_hash(strategy),
                    start=environment.start,
                )
            )
        except Exception:
            logger.exception(
                "sealed-window block could not be recorded — lineage_id=%s start=%s",
                lineage_id,
                environment.start,
            )

    def _record_trial_result(self, run_id: str, result: BacktestRunResult) -> None:
        # 실패해도 run 은 끝까지 간다. 대표 샤프가 빈 시도로 보이고 원인은 로그에 남는다.
        try:
            self._repository.record_trial_result(
                run_id,
                session_sharpe=representative_sharpe(result),
                metric_registry_version=result.manifest.metric_registry_version,
            )
        except Exception:
            logger.exception("trial result could not be recorded — run_id=%s", run_id)

    def _dispatch(self) -> None:
        """자리가 비는 만큼 대기열 앞의 run 을 스레드로 띄운다. `self._lock` 안에서 부른다.

        대기 run 에는 스레드가 없다 — run 마다 스레드를 먼저 띄워 두면 스레드 수가 상한 없이 는다
        (#161).
        """

        while self._running < self._run_slots:
            record = self._waiting.pop(
                experiments=self._running_experiments < experiment_slots(self._run_slots)
            )
            if record is None:
                return
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
                record.experiment_slot = None not in record.owners
                self._running += 1
                self._running_experiments += record.experiment_slot

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
                self._running_experiments -= record.experiment_slot
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
            raise InvalidBacktestRunError(
                f"resolved run spec has no run environment — run_id={run_id}"
            )
        try:
            # tape 단계: 원시 관측 로딩 + 팩터 평가 + TargetTape 컴파일. 실데이터에서 실행 시간의
            # 대부분을 차지하므로 취소 콜백을 파이프라인 checkpoint 에 그대로 건다.
            self._update(
                record, RunStatus.RUNNING, _TAPE_PROGRESS_START, "tape", "Compiling target tape"
            )
            # 데이터 포트의 준비(원장 카탈로그 등, #369)와 벤치마크 id(#361)는 tape 앞에서 한 번의
            # 조회로 확인한다 — tape 계산을 다 쓴 뒤 알리지 않는다. 벤치마크가 없으면 종목 없는
            # 질의다. 접수는 여전히 데이터를 읽지 않는다(#158).
            self._data_source.load_backtest_dataset(
                BacktestDataQuery(
                    environment.start, environment.end, (), spec.benchmark_security_id
                )
            )
            try:
                # require_engine_compatible 은 start() 의 preflight 판정을 되풀이하는 심층 방어다 —
                # 같은 순수 판정이라 정상 경로에서는 발동하지 않는다.
                preview = self._portfolio_design.run_pipeline(
                    PortfolioPreviewRequest(strategy, environment=environment),
                    options=PortfolioPipelineOptions(
                        require_engine_compatible=True, parameter_values=spec.parameter_values
                    ),
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
                raise NoPositionsError(
                    "target tape selected no position in any rebalance frame — "
                    f"frames={len(tape.frames)} start={environment.start} "
                    f"end={environment.end} rebalance={strategy.portfolio.rebalance.value}"
                )
            dataset = self._data_source.load_backtest_dataset(
                BacktestDataQuery(
                    start=environment.start,
                    end=environment.end,
                    security_ids=security_ids,
                    benchmark_security_id=spec.benchmark_security_id,
                    history_sessions_before_start=cost_history_sessions(environment),
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
            self._record_trial_result(run_id, result)
            cancelled_after_commit = False
            with self._lock:
                # Completion and cancel acceptance linearize on the same lock. If cancel acquired
                # it first, the committed bundle is compensation-cleaned and never becomes
                # observable through state/result. If completion acquired it first, cancel sees a
                # terminal run and is not accepted.
                if record.cancellation.is_set():
                    cancelled_after_commit = True
                else:
                    record.state = replace(record.state, artifact_sha256=commit.sha256)
                    try:
                        self._emit(
                            record,
                            RunStatus.COMPLETED,
                            1.0,
                            "completed",
                            "Run completed",
                            durable=True,
                        )
                    except Exception:
                        # 저장하지 못한 완료는 보이지 않는다 — 보이면 사용자가 본 결과를 원장이
                        # 세지 않는다(N 과소, #381). 실행은 아래 실패 분기로 끝난다.
                        record.state = replace(record.state, artifact_sha256=None)
                        self._artifact_store.discard(run_id)
                        raise
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
        *,
        durable: bool = False,
    ) -> None:
        """상태를 바꾸고 이벤트를 남긴다. `durable` 이면 저장이 먼저이고 실패하면 올린다(메모리
        상태도 바꾸지 않는다). 아니면 상태가 바뀔 때만 저장하고 저장 실패는 삼킨다(`_persist`)."""
        occurred_at = self._now()
        previous = record.state.status
        state = replace(
            record.state,
            status=status,
            progress=progress,
            stage=stage,
            message=message,
            updated_at=occurred_at,
        )
        if durable:
            self._repository.update(state)
        record.state = state
        record.events.append(
            RunProgressEvent(
                sequence=record.events[-1].sequence + 1 if record.events else 0,
                run_id=record.state.run_id,
                status=status,
                progress=progress,
                stage=stage,
                message=message,
                occurred_at=occurred_at,
            )
        )
        # 상태가 바뀔 때만 저장한다(진행률은 메모리). 접수는 `start` 가 `add` 로 저장한다.
        saved = durable
        if status is not previous and not durable:
            saved = self._persist(record.state)
        # 종결이 저장된 run 만 사본을 버린다 — 저장하지 못한 run 의 사본을 버리면 저장소의 옛
        # 상태(`queued`·`running`)로 되돌아가 끝난 run 이 다시 도는 것처럼 보인다.
        if status in _SETTLED and saved:
            self._retire(record)

    def _retire(self, record: _RunRecord) -> None:
        """끝난 run 을 최근 목록에 넣고, 넘친 오래된 끝난 run 의 메모리 사본을 버린다(#332)."""
        self._finished.append(record.state.run_id)
        while len(self._finished) > _FINISHED_RECORDS_KEPT:
            self._records.pop(self._finished.popleft(), None)

    def _persist(self, state: BacktestRunState) -> bool:
        """상태 전이를 저장하고 저장했는지 돌려준다. 실패해도 run 수명은 멈추지 않는다.

        run 스레드의 실패 분기가 다시 저장하다 터지면 레코드가 비종결로 굳는다. 이 프로세스에서는
        메모리 상태가 계속 맞고(저장하지 못한 종결 run 은 사본을 버리지 않는다), 저장되지 않은
        전이는 재시작 때 `interrupted` 로 닫힌다. 완료는 여기로 오지 않는다(`_emit(durable=True)`).
        """
        try:
            self._repository.update(state)
        except Exception:
            logger.exception(
                "backtest run state could not be persisted — run_id=%s status=%s stage=%s",
                state.run_id,
                state.status,
                state.stage,
            )
            return False
        return True

    @staticmethod
    def _raise_if_cancelled(record: _RunRecord) -> None:
        if record.cancellation.is_set():
            raise RunCancelledError("run cancelled")


# 끝난 run 상태 집합과, 메모리에 사본을 남기는 최근 끝난 run 수. 이보다 오래 끝난 run 은
# 저장소에서 읽고 진행 이벤트는 비어 있다.
_SETTLED = (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED)
_FINISHED_RECORDS_KEPT = 256


def _cancel_result(state: BacktestRunState, *, kept_by_owners: bool) -> BacktestCancelResult:
    return BacktestCancelResult(
        **{item.name: getattr(state, item.name) for item in fields(BacktestRunState)},
        kept_by_owners=kept_by_owners,
    )


# 실행 접수 거절과 그 안정 키. HTTP 거절(시작·미리 계산·실험 기반 검사와, 같은 판정을 타는
# 미리보기·추적 — #351)과 실험 trial 제출이 이 목록 하나를 쓴다. 하위 타입을 먼저 둔다.
# 실행 설정 거절은 domain 오류 그대로다 — 봉인 구간·연구 하한 날짜도 그 오류가 싣는다.
_REJECTION_CODES: tuple[tuple[type[Exception], AdmissionRejectionCode], ...] = (
    (MissingRunEnvironmentError, "backtest.run.environment_required"),
    (ResearchWindowViolationError, "backtest.run.research_window_violation"),
    (BacktestParameterValueError, "backtest.run.parameter_invalid"),
    (InvalidBacktestRunError, "backtest.run.invalid"),
    (StrategyReferenceNotFoundError, "backtest.strategy.not_found"),
    (StaleStrategyReferenceError, "backtest.strategy.stale"),
    (StrategyRevisionRequiresUpgradeError, "backtest.strategy.requires_upgrade"),
    (InvalidPortfolioRequestError, "portfolio.strategy.invalid"),
)


def rejection_code(error: BaseException) -> AdmissionRejectionCode | None:
    """실행 접수 거절이면 그 코드, 아니면 None(예상 밖 오류)."""
    return next((code for kind, code in _REJECTION_CODES if isinstance(error, kind)), None)


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
    if isinstance(error, BacktestDataNotReadyError):
        return "backtest.run.data_not_ready"
    if isinstance(error, NoPositionsError):
        return "backtest.run.no_positions"
    if isinstance(error, BacktestDataUnavailableError):
        return "backtest.run.benchmark_unknown"
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
