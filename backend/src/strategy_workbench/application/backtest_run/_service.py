from __future__ import annotations

import logging
from collections import OrderedDict, deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from threading import Event, Lock, RLock, Thread
from typing import TypeVar

from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPipelineResult,
    PortfolioPreviewRequest,
    PortfolioSnapshotMismatchError,
    RawObservationUnavailableError,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    StrategyNotFoundError,
    StrategyRepositoryPort,
)
from strategy_workbench.domain.analytics.facade.factor_regression import (
    FactorRegressionUnavailableReason,
    FactorReturnSeries,
    factor_return_series,
    regress_on_factor_returns,
    unavailable_factor_regression,
)
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.account_report import (
    AccountReport,
    project_account_report,
)
from strategy_workbench.domain.backtest.facade.environment import (
    MissingRunEnvironmentError,
    ResearchWindowViolationError,
    RunEnvironment,
    environment_hash,
    require_environment,
)
from strategy_workbench.domain.backtest.facade.runs import (
    AdmissionRejectionCode,
    BacktestCancelResult,
    BacktestRunResult,
    BacktestRunSpec,
    BacktestRunState,
    BacktestStartResponse,
    InlineDraft,
    ResolvedBacktestRunSpec,
    RunFailureCode,
    RunManifest,
    RunProgressEvent,
    RunStage,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySource,
    StrategySourceKind,
    run_input_key,
)
from strategy_workbench.domain.backtest.facade.trials import (
    BlockedTrialAttempt,
    TrialLedger,
    TrialPreview,
    preview_trial,
    summarize_trial_ledger,
    trial_key,
)
from strategy_workbench.domain.experiment.facade.statistics import (
    alpha_t_threshold,
    run_deflated_sharpe_evidence,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.factor.facade.planning import FACTOR_EVALUATION_VERSION
from strategy_workbench.domain.factor.facade.registry import (
    FactorRegistry,
    build_default_factor_registry,
)
from strategy_workbench.domain.strategy.facade.specification import (
    InvalidParameterValueError,
    StrategySpec,
    resolve_parameter_values,
    strategy_spec_hash,
)

from ._computation import (
    InvalidBacktestRunError,
    RunComputation,
    _describe_engine_issues,
    _describe_failure,
    _failure_code,
)
from ._factor_regression import (
    MOMENTUM_FACTOR_ID,
    VALUE_FACTOR_ID,
    RunFactorRegression,
    factor_periods,
    factor_regression_spec,
    strategy_period_returns,
)
from ._scheduler import RunLaneControls, RunQueue, experiment_slots
from ._validation_report import RunValidationReport
from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import BacktestDataPort
from .ports.outgoing.backtest_executor import BacktestExecutorPort, RunCancelledError
from .ports.outgoing.run_execution import (
    RunCommitted,
    RunExecutionPort,
    RunFailed,
    RunHandle,
    RunJob,
    RunOutcome,
)
from .ports.outgoing.run_repository import BacktestRunRepositoryPort, BacktestRunSummary, RunKind

logger = logging.getLogger(__name__)

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
    """접수 판정 결과. `resolved`는 실행 입력이고 `spec`은 연구 저장 경계의 기존 JSON 투영이다."""

    resolved: ResolvedBacktestRunSpec
    provenance: StrategyProvenance
    lineage_id: str | None
    strategy_source: StrategySource | None
    lineage_strategy_id: str | None

    @property
    def spec(self) -> BacktestRunSpec:
        # 연구 저장 계약은 기존 wire를 유지한다. 실행 경로는 resolved만 읽는다.
        return self.resolved.to_wire(
            strategy_source=self.strategy_source, lineage_strategy_id=self.lineage_strategy_id
        )


class BacktestResultNotReadyError(RuntimeError):
    pass


# 팩터 수익률 캐시 키: (회귀 실행 설정 해시, 데이터 판, 레지스트리 판본, 평가 규칙 판본).
_FactorReturnsKey = tuple[str, str, str, str]
# 계산해 두는 키 수(결과와 오래가는 실패 모두). 한 키는 구간 수(5년 약 1200) × 숫자 일곱이라 작다 —
# 실행 설정을 바꿔 가며 여러 결과를 함께 열어도 끝난 계산이 읽히기 전에 밀려나지 않을 만큼 둔다.
_FACTOR_RETURNS_KEPT = 32


@dataclass(frozen=True)
class _FactorReturns:
    series: FactorReturnSeries
    # 단면을 계산한 평가 계획의 판본(응답 `factor_evaluation_version`).
    evaluation_version: str | None


@dataclass(frozen=True)
class _FactorMiss:
    """팩터 단면을 얻지 못한 사유. `lasting` 이면 이 프로세스에서 다시 계산해도 같다."""

    reason: FactorRegressionUnavailableReason
    lasting: bool


_Cached = TypeVar("_Cached")


def _keep_latest(entries: OrderedDict[_FactorReturnsKey, _Cached]) -> None:
    while len(entries) > _FACTOR_RETURNS_KEPT:
        entries.popitem(last=False)


def _start_daemon(job: Callable[[], None]) -> None:
    Thread(target=job, name="factor-returns", daemon=True).start()


# 이 프로세스가 접수한 run 의 메모리 사본. 목록·상태·요청의 정본은 저장소, 결과의 정본은 산출물
# 저장소이고, 이 사본은 저장하지 않는 것(진행률·진행 이벤트 링·취소 신호)을 든다. 정체성으로
# 가린다(`eq=False`) — 대기열에서 꺼내고 지울 때 같은 run 만 맞아야 한다.
@dataclass(eq=False)
class _RunRecord:
    state: BacktestRunState
    # 실행할 spec — `strategy` 를 해소하고 실행 설정을 박은 것. 다시 제출할 수 있는 원본 요청은
    # 저장소가 가진다.
    spec: ResolvedBacktestRunSpec
    strategy_source: StrategySource | None
    lineage_strategy_id: str | None
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
    # 전송이 돌려준 손잡이(띄운 뒤에만 있다). 취소를 전송에 알린다.
    handle: RunHandle | None = None


class _RunListener:
    """run 하나의 진행·종결을 서비스 상태 기계로 옮긴다(`RunListener` 구현)."""

    def __init__(self, service: BacktestRunService, record: _RunRecord) -> None:
        self._service = service
        self._record = record
        # 마지막으로 남긴 tape 진행(작업 설명, 막대 위치). 한 run 의 진행은 전송의 한 스레드만
        # 부르므로 잠금 없이 이 객체가 소유한다.
        self._last_tape: tuple[str, float] | None = None

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        # tape 는 작업 설명이 바뀌면 항상 남기고, 같은 설명 안에서는 `_MIN_TAPE_PROGRESS_STEP`
        # 이상 오를 때만 남긴다(spec D3 — 이 규칙은 부모가 소유한다).
        if stage == "tape":
            last = self._last_tape
            if (
                last is not None
                and message == last[0]
                and fraction - last[1] < _MIN_TAPE_PROGRESS_STEP
            ):
                return
            self._service._update(self._record, RunStatus.RUNNING, fraction, stage, message)
            self._last_tape = (message, fraction)
            return
        self._service._update(self._record, RunStatus.RUNNING, fraction, stage, message)

    def on_finished(self, outcome: RunOutcome) -> None:
        self._service._finish(self._record, outcome)


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
        run_execution: Callable[[RunComputation], RunExecutionPort],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_slots: int = DEFAULT_RUN_SLOTS,
        factor_registry: FactorRegistry | None = None,
        background: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        """`run_execution` 은 run 계산을 돌릴 전송을 만든다(spec D10).

        서비스가 자기 포트로 만든 계산(`RunComputation`)을 받는다. 같은 프로세스의 전송은 그 계산을
        부르고, 다른 프로세스의 전송은 자기 쪽에서 같은 계산을 다시 조립한다. 어느 전송을 쓸지는
        composition root 가 정한다.
        """
        if run_slots < 1:
            raise ValueError(f"run_slots must be at least 1 — run_slots={run_slots!r}")
        self._portfolio_design = portfolio_design
        self._strategy_repository = strategy_repository
        self._artifact_store = artifact_store
        self._run_execution = run_execution(
            RunComputation(portfolio_design, data_source, executor, artifact_store)
        )
        self._repository = run_repository
        self._new_id = new_id
        self._now = now
        self._run_slots = run_slots
        # 팩터 회귀가 모멘텀·가치 기본 graph 를 읽는다(V4-05). 레지스트리는 순수 값이라 주입이
        # 없으면 기본 레지스트리를 만든다.
        self._factor_registry = factor_registry or build_default_factor_registry()
        self._records: dict[str, _RunRecord] = {}
        # 끝난 run 은 최근 것만 메모리에 둔다(끝난 순). 상태·요청·결과의 정본은 저장소·산출물이다.
        self._finished: deque[str] = deque()
        self._waiting: RunQueue[_RunRecord] = RunQueue()
        self._running = 0
        self._running_experiments = 0
        self._lock = RLock()
        self._factor_regression_lock = Lock()
        # 팩터 수익률은 전략과 무관해 키(`_factor_returns_key`)마다 한 번 계산해 둔다. 최근 것만
        # 둔다. 값은 계산한 팩터 수익률이거나, 이 프로세스에서 다시 계산해도 같은 사유(데이터 판이
        # 다르다)다.
        self._factor_cache: OrderedDict[
            _FactorReturnsKey, _FactorReturns | FactorRegressionUnavailableReason
        ] = OrderedDict()
        # 활성 키는 최대 하나다. 다른 키의 요청을 대기 스레드나 내부 큐로 쌓지 않는다.
        self._factor_jobs: set[_FactorReturnsKey] = set()
        # 한 번 알리고 잊는 일시 실패(원천 부재·조회 중 판 경합·예상 밖 오류).
        self._factor_failures: OrderedDict[_FactorReturnsKey, FactorRegressionUnavailableReason] = (
            OrderedDict()
        )
        # 무거운 계산을 거는 곳. 기본은 데몬 스레드이고, 테스트는 그 자리에서 돌리는 함수를 준다.
        self._background = background or _start_daemon
        self._close_interrupted_runs()

    def slot_usage(self) -> tuple[int, int]:
        """(슬롯 수, 지금 도는 run 수)."""
        with self._lock:
            return self._run_slots, self._running

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
        lane_controls: RunLaneControls | None = None,
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
        spec, provenance = admission.resolved, admission.provenance
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
                    self._apply_lane_controls(owner, lane_controls)
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
                strategy_source=admission.strategy_source,
                lineage_strategy_id=admission.lineage_strategy_id,
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
            self._apply_lane_controls(owner, lane_controls)
            self._waiting.push(record, self._lane(record))
            self._dispatch()
        return BacktestStartResponse(accepted)

    def _admit(self, request: BacktestRunSpec, *, record_blocked: bool) -> RunAdmission:
        """요청을 실행할 spec 으로 해소하고 접수 판정을 모두 한다. 데이터는 읽지 않는다."""
        strategy, provenance = self._resolve(request)
        spec = replace(request, strategy=strategy)
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
        return RunAdmission(
            ResolvedBacktestRunSpec.from_request(
                spec, strategy=strategy, environment=environment, parameter_values=parameter_values
            ),
            provenance,
            lineage_id,
            request.strategy_source,
            request.lineage_strategy_id,
        )

    def preview_trial(self, request: BacktestRunSpec) -> TrialPreview:
        """실행 전 미리 계산 — 이 요청이 결과를 내면 계열 N 에 새로 드는가(검증 랩 spec D2).

        `start` 와 같은 해소·계열·실행 설정 판정을 타서 같은 시도 키를 낸다. 봉인 겹침은 같은 422 로
        거절하되 실행 요청이 아니므로 봉인 원장에 남기지 않는다. 전략 검증(preflight)은 하지 않는다.
        """
        strategy, provenance = self._resolve(request)
        spec = replace(request, strategy=strategy)
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
            if not self._owner_active(owner):
                self._waiting.forget(owner)
                return
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

    def summary(self, run_id: str) -> BacktestRunSummary:
        """이력 한 행(실행 종류·소유 실험·provenance). 이 프로세스가 도는 run 은 메모리 상태로
        덮는다."""
        stored = self._repository.get(run_id)
        with self._lock:
            record = self._records.get(run_id)
            return stored if record is None else replace(stored, run=record.state)

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

    def account_report(self, run_id: str) -> AccountReport:
        """검증해 읽은 완료 결과를 초기자본 기준 계좌 평가손익으로 투영한다."""
        return project_account_report(self.result(run_id))

    def validation_report(self, run_id: str) -> RunValidationReport:
        """검증된 완료 결과와 현재 계열 원장의 통계를 읽는다. 실행·선택 기록은 쓰지 않는다."""
        result = self.result(run_id)
        manifest = result.manifest
        # 완료 결과의 검증된 출처를 쓴다. 은퇴한 리비전을 실행 가능 여부로 다시 해소하지 않는다.
        provenance = (
            manifest.strategy_provenance
            if manifest.strategy_provenance.kind is StrategySourceKind.SAVED_REVISION
            else None
        )
        lineage_id = self._lineage(manifest.run_spec, provenance)
        ledger = None if lineage_id is None else self.trial_ledger(lineage_id)
        return RunValidationReport(
            run_id=manifest.run_id,
            run_fingerprint=manifest.run_fingerprint,
            metric_registry_version=manifest.metric_registry_version,
            observed_at=self._now(),
            psr=next(
                (
                    m
                    for m in result.metrics
                    if m.scope is MetricScope.FULL and m.metric_id == "probabilistic_sharpe"
                ),
                None,
            ),
            dsr=run_deflated_sharpe_evidence(result, ledger),
        )

    def factor_regression(self, run_id: str) -> RunFactorRegression:
        """검증된 완료 결과의 세션 초과수익을 시장·규모·가치·모멘텀에 회귀한다(V4-05).

        팩터 단면은 run 의 실행 설정으로 기존 포트폴리오 파이프라인을 다시 돌려 읽는다. 결측 정책은
        채우지 않는 `drop` 으로 바꾼다 — 채운 팩터 값으로 정렬하면 없는 신호가 포트폴리오에 든다.
        지금 데이터 판이 run 의 판과 다르면 회귀하지 않는다(다른 데이터로 잰 노출을 그 run 의 것으로
        보이지 않는다). 실행·선택 기록은 쓰지 않는다.

        팩터 수익률은 전략과 무관해 실행 설정·데이터 판·레지스트리·평가 판본
        (`_factor_returns_key`)마다 한 번 계산해 두고 회귀만 매번 한다. 아직 없으면 뒤에서 계산을
        걸고 `computing` 으로 돌려준다 — 실원장 5년 유니버스 단면에 약 2분이 걸려 조회를 붙잡지
        않는다(실데이터 확인 2026-10-17 후속 1).
        """
        result = self.result(run_id)
        manifest = result.manifest
        strategy = strategy_period_returns(result)
        days = manifest.run_spec.annualization_days
        returns = self._factor_returns(manifest)
        if isinstance(returns, FactorRegressionUnavailableReason):
            report = unavailable_factor_regression(
                returns, strategy_period_count=len(strategy), annualization_days=days
            )
            evaluation_version = None
        else:
            report = regress_on_factor_returns(strategy, returns.series, annualization_days=days)
            evaluation_version = returns.evaluation_version
        lineage_id = self._lineage(
            manifest.run_spec,
            manifest.strategy_provenance
            if manifest.strategy_provenance.kind is StrategySourceKind.SAVED_REVISION
            else None,
        )
        trial_count = None if lineage_id is None else self.trial_ledger(lineage_id).trial_count
        return RunFactorRegression(
            run_id=manifest.run_id,
            run_fingerprint=manifest.run_fingerprint,
            data_snapshot_id=manifest.data_snapshot_id,
            momentum_factor_id=MOMENTUM_FACTOR_ID,
            value_factor_id=VALUE_FACTOR_ID,
            factor_registry_version=self._factor_registry.version,
            factor_evaluation_version=evaluation_version,
            trial_count=trial_count,
            alpha_t_threshold=None if trial_count is None else alpha_t_threshold(trial_count),
            report=report,
        )

    def _factor_returns_key(self, manifest: RunManifest) -> _FactorReturnsKey:
        """팩터 수익률을 가르는 입력 전부.

        회귀가 쓰는 실행 설정(결측 정책 `drop`), run 의 데이터 판, 레지스트리 판본(기본 graph),
        평가 규칙 판본이다. 전략·실행 옵션은 들지 않는다.
        """
        return (
            environment_hash(replace(manifest.environment, missing=MissingPolicy.DROP)),
            manifest.data_snapshot_id,
            self._factor_registry.version,
            FACTOR_EVALUATION_VERSION,
        )

    def _factor_returns(
        self, manifest: RunManifest
    ) -> _FactorReturns | FactorRegressionUnavailableReason:
        """계산해 둔 팩터 수익률. 없으면 뒤에서 계산을 걸고(같은 키는 한 번) `COMPUTING`.

        데이터 판은 프로세스 안에서 고정이라(SoT "백테스트 run 접수" 행) 계산해 둔 값은 늘 지금
        판과 같은 판의 것이고, 판이 달라 회귀하지 않는다는 답도 다시 계산하지 않고 그대로 둔다.
        일시 실패(원천 부재·조회 중 판 경합·예상 밖 오류)는 사유를 한 번 돌려주고 잊는다 — 다음
        조회가 다시 계산한다.
        활성 계산이 있으면 다른 키도 `COMPUTING`만 답하며, 완료 후 다음 조회에서 접수한다.
        """
        key = self._factor_returns_key(manifest)
        with self._lock:
            settled = self._settled_factor_returns(key)
            if settled is not None:
                return settled
            # 활성 계산 하나만 접수한다. 다른 키를 기다리는 daemon도 만들지 않는다.
            # 자리가 비면 다음 명시 조회가 그 키를 접수한다.
            if self._factor_jobs:
                return FactorRegressionUnavailableReason.COMPUTING
            self._factor_jobs.add(key)
        try:
            self._background(lambda: self._compute_factor_returns(key, manifest))
        except BaseException:
            # 계산을 걸지 못했다 — 키를 남기면 그 키는 재시작까지 "계산 중"만 답한다.
            with self._lock:
                self._factor_jobs.discard(key)
            raise
        with self._lock:
            # 그 자리에서 도는 실행자(테스트)면 이미 끝났다.
            return self._settled_factor_returns(key) or FactorRegressionUnavailableReason.COMPUTING

    def _settled_factor_returns(
        self, key: _FactorReturnsKey
    ) -> _FactorReturns | FactorRegressionUnavailableReason | None:
        cached = self._factor_cache.get(key)
        if cached is not None:
            self._factor_cache.move_to_end(key)
            return cached
        return self._factor_failures.pop(key, None)

    def _compute_factor_returns(self, key: _FactorReturnsKey, manifest: RunManifest) -> None:
        outcome: _FactorReturns | _FactorMiss
        try:
            pipeline = self._factor_pipeline(manifest)
            outcome = (
                pipeline
                if isinstance(pipeline, _FactorMiss)
                else _FactorReturns(
                    factor_return_series(factor_periods(pipeline.observations)),
                    pipeline.preview.tape.factor_evaluation_version,
                )
            )
        except Exception:
            logger.exception(
                "factor returns computation failed — run_id=%s environment=%s snapshot=%s",
                manifest.run_id,
                manifest.environment_hash,
                manifest.data_snapshot_id,
            )
            outcome = _FactorMiss(FactorRegressionUnavailableReason.COMPUTATION_FAILED, False)
        with self._lock:
            self._factor_jobs.discard(key)
            if isinstance(outcome, _FactorMiss) and not outcome.lasting:
                self._factor_failures[key] = outcome.reason
                _keep_latest(self._factor_failures)
                return
            self._factor_cache[key] = (
                outcome.reason if isinstance(outcome, _FactorMiss) else outcome
            )
            _keep_latest(self._factor_cache)

    def _factor_pipeline(self, manifest: RunManifest) -> PortfolioPipelineResult | _FactorMiss:
        """run 의 실행 설정으로 팩터 단면을 읽는다. 데이터가 없거나 판이 다르면 그 사유.

        유니버스 전체를 읽는 무거운 조회라 한 번에 하나만 돈다 — 화면을 여러 번 열어도 메모리·
        CPU 가 요청 수만큼 늘어 도는 백테스트와 다투지 않게 한다(V4-05 리뷰 P2-1).
        """
        try:
            with self._factor_regression_lock:
                pipeline = self._portfolio_design.run_pipeline(
                    PortfolioPreviewRequest(
                        factor_regression_spec(self._factor_registry),
                        environment=replace(manifest.environment, missing=MissingPolicy.DROP),
                    )
                )
        except RawObservationUnavailableError as error:
            logger.warning(
                "factor regression inputs unavailable — run_id=%s environment=%s: %s",
                manifest.run_id,
                manifest.environment_hash,
                error,
            )
            return _FactorMiss(FactorRegressionUnavailableReason.DATA_UNAVAILABLE, False)
        except PortfolioSnapshotMismatchError as error:
            # 원천과 팩터 메타데이터가 서로 다른 판을 답했다 — 조회 중 데이터가 바뀐 경합이다.
            logger.warning(
                "factor regression skipped — snapshot moved during the read: run_id=%s: %s",
                manifest.run_id,
                error,
            )
            return _FactorMiss(FactorRegressionUnavailableReason.DATA_SNAPSHOT_CHANGED, False)
        if pipeline.data_snapshot_id != manifest.data_snapshot_id:
            logger.warning(
                "factor regression skipped — data snapshot changed: run_id=%s run=%s current=%s",
                manifest.run_id,
                manifest.data_snapshot_id,
                pipeline.data_snapshot_id,
            )
            # 판은 프로세스 안에서 고정이라 다시 읽어도 같다 — 약 2분 계산을 되풀이하지 않는다.
            return _FactorMiss(FactorRegressionUnavailableReason.DATA_SNAPSHOT_CHANGED, True)
        return pipeline

    def shutdown(self) -> None:
        """서버가 끝날 때 전송이 띄운 것을 정리한다(run-worker spec D5).

        도는 run 을 종결로 바꾸지 않는다 — 기록은 `queued`·`running` 으로 남고 다음 부팅이
        `interrupted` 로 닫는다(`_close_interrupted_runs`).
        """
        self._run_execution.shutdown()

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
            if record.state.status.is_terminal:
                return _cancel_result(record.state, kept_by_owners=False)
            if record.owners:
                self._relane(record)
                self._dispatch()
                self._release_idle_owner(owner)
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
                if record.handle is not None:
                    # 도는 계산에 알린다. 막히지 않고, 멈춘 결과는 전송이 종결로 알린다.
                    record.handle.cancel()
                self._emit(
                    record,
                    RunStatus.CANCEL_REQUESTED,
                    record.state.progress,
                    "cancellation",
                    "Cancellation requested",
                )
            self._release_idle_owner(owner)
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

    def _resolve(self, request: BacktestRunSpec) -> tuple[StrategySpec, StrategyProvenance | None]:
        """요청 출처를 실행할 전략과 provenance로 해소한다."""
        source = request.strategy_source
        if source is None:
            if request.strategy is None:
                raise InvalidBacktestRunError(
                    "run request must name its strategy — "
                    "neither strategy nor strategy_source given"
                )
            return request.strategy, None
        if request.strategy is not None:
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
            return record.spec, provenance
        # 인라인 출처의 provenance는 접수가 동일 strategy_spec_hash로 확정한다.
        return source.spec, None

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
            self._portfolio_design.validate_trial_strategy(strategy, missing=environment.missing)
            key = trial_key(spec)
        except (InvalidPortfolioRequestError, InvalidParameterValueError):
            # 전략·팩터 플랜·파라미터가 확정되지 않은 요청은 봉인 감사 기록에 남기지 않는다.
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

    def _record_completion(self, completed: BacktestRunState, committed: RunCommitted) -> None:
        """완료 전이와 대표 샤프·머리 지표를 한 트랜잭션에 적는다. 실패하면 원인을 남기고 올려
        완료를 막는다(#381)."""
        try:
            self._repository.record_trial_result(
                completed,
                session_sharpe=committed.session_sharpe,
                metric_registry_version=committed.metric_registry_version,
                headline=committed.headline,
            )
        except Exception:
            logger.exception(
                "run completion and trial result could not be recorded — run_id=%s",
                completed.run_id,
            )
            raise

    def _dispatch(self) -> None:
        """자리가 비는 만큼 대기열 앞의 run 을 전송으로 띄운다. `self._lock` 안에서 부른다.

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
                record.handle = self._run_execution.start(
                    RunJob(
                        run_id=record.state.run_id,
                        spec=record.spec,
                        provenance=record.provenance,
                        strategy_source=record.strategy_source,
                        lineage_strategy_id=record.lineage_strategy_id,
                    ),
                    _RunListener(self, record),
                )
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

    def _finish(self, record: _RunRecord, outcome: RunOutcome) -> None:
        """전송이 알린 종결을 상태로 옮기고 자리를 내놓는다. 끝나면 대기열의 다음 run 을 띄운다
        (#161). 전송의 감독 스레드에서, 잠금 밖에서 불린다."""

        try:
            self._settle(record, outcome)
        finally:
            with self._lock:
                self._running -= 1
                self._running_experiments -= record.experiment_slot
                self._dispatch()

    def _settle(self, record: _RunRecord, outcome: RunOutcome) -> None:
        if isinstance(outcome, RunFailed):
            self._fail(record, outcome.error_code, outcome.error, outcome.exception)
            return
        if isinstance(outcome, RunCommitted):
            try:
                self._complete(record, outcome)
                return
            except RunCancelledError:
                pass
            except BaseException as error:
                self._fail(record, _failure_code(error), _describe_failure(error), error)
                return
        self._update(
            record, RunStatus.CANCELLED, record.state.progress, "cancelled", "Run cancelled"
        )

    def _complete(self, record: _RunRecord, committed: RunCommitted) -> None:
        """커밋한 결과를 완료로 보인다. 취소가 먼저 이겼으면 결과를 지우고 `RunCancelledError`.

        완료와 취소 접수는 같은 잠금에서 선형화한다. 취소가 먼저 잡았으면 커밋한 묶음을 지우고
        아무것도 적지 않는다. 완료가 먼저 잡았으면 완료 전이·대표 샤프·머리 지표를 한 트랜잭션에
        적고, 뒤의 취소는 끝난 run 을 보고 받아들여지지 않는다. 승패가 정해지기 전에 원장·머리
        지표를 적지 않는다 — 적으면 취소된 run 에 샤프·머리 지표가 남는다(#581 리뷰).
        """
        run_id = record.state.run_id
        with self._lock:
            cancelled_after_commit = record.cancellation.is_set()
            if not cancelled_after_commit:
                record.state = replace(record.state, artifact_sha256=committed.sha256)
                try:
                    self._emit(
                        record,
                        RunStatus.COMPLETED,
                        1.0,
                        "completed",
                        "Run completed",
                        completion=committed,
                    )
                except Exception:
                    # 저장하지 못한 완료는 보이지 않는다 — 보이면 사용자가 본 결과를 원장이
                    # 세지 않는다(N 과소, #381). 실행은 실패 분기로 끝난다.
                    record.state = replace(record.state, artifact_sha256=None)
                    self._artifact_store.discard(run_id)
                    raise
        if cancelled_after_commit:
            self._artifact_store.discard(run_id)
            raise RunCancelledError("run cancelled during artifact commit")

    def _fail(
        self,
        record: _RunRecord,
        error_code: RunFailureCode,
        error: str,
        exception: BaseException | None,
    ) -> None:
        # 실패 사유 원문(절대 경로 포함)과 stack trace 는 서버 로그에만 남기고 상태에는 가린
        # 문자열을 싣는다. 어댑터 계약 위반은 사용자 오류가 아니라 어댑터 버그라 로그로 반드시
        # 드러나야 한다 — 이 로그가 유일한 진단 채널이다(HTTP 500 이 없다).
        with self._lock:
            failed_stage = record.state.stage
        logger.error(
            "backtest run failed — run_id=%s stage=%s error_code=%s",
            record.state.run_id,
            failed_stage,
            error_code,
            exc_info=exception,
        )
        with self._lock:
            # 취소와 겹쳐도 실패 사유는 버리지 않는다 — "내가 취소했다" 와 "데이터가 없었다" 를
            # 화면에서 구분할 수 있어야 한다.
            record.state = replace(record.state, error=error, error_code=error_code)
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

    def _update(
        self,
        record: _RunRecord,
        status: RunStatus,
        progress: float,
        stage: RunStage,
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
        stage: RunStage,
        message: str,
        *,
        completion: RunCommitted | None = None,
    ) -> None:
        """상태를 바꾸고 이벤트를 남긴다.

        `completion` 이 있으면(완료 전이) 대표 샤프·머리 지표와 함께 저장이 먼저이고 실패하면
        올린다(메모리 상태도 바꾸지 않는다). 아니면 상태가 바뀔 때만 저장하고 저장 실패는
        삼킨다(`_persist`).
        """
        occurred_at = self._now()
        previous = record.state.status
        state = replace(
            record.state,
            status=status,
            progress=progress,
            stage=stage,
            message=message,
            updated_at=occurred_at,
            started_at=record.state.started_at
            or (occurred_at if status is RunStatus.RUNNING else None),
        )
        durable = completion is not None
        if completion is not None:
            self._record_completion(state, completion)
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
        if status.is_terminal:
            for owner in record.owners:
                self._release_idle_owner(owner)
            if saved:
                self._retire(record)

    def _apply_lane_controls(self, owner: str | None, controls: RunLaneControls | None) -> None:
        if owner is not None and controls is not None:
            self._waiting.configure(owner, paused=controls.paused, weight=controls.weight)

    def _owner_active(self, owner: str) -> bool:
        return any(
            owner in record.owners and not record.state.status.is_terminal
            for record in self._records.values()
        )

    def _release_idle_owner(self, owner: str | None) -> None:
        if owner is not None and not self._owner_active(owner):
            self._waiting.forget(owner)

    def _retire(self, record: _RunRecord) -> None:
        """끝난 run 을 최근 목록에 넣고, 넘친 오래된 끝난 run 의 메모리 사본을 버린다(#332)."""
        self._finished.append(record.state.run_id)
        while len(self._finished) > _FINISHED_RECORDS_KEPT:
            self._records.pop(self._finished.popleft(), None)

    def _persist(self, state: BacktestRunState) -> bool:
        """상태 전이를 저장하고 저장했는지 돌려준다. 실패해도 run 수명은 멈추지 않는다.

        run 스레드의 실패 분기가 다시 저장하다 터지면 레코드가 비종결로 굳는다. 이 프로세스에서는
        메모리 상태가 계속 맞고(저장하지 못한 종결 run 은 사본을 버리지 않는다), 저장되지 않은
        전이는 재시작 때 `interrupted` 로 닫힌다. 완료는 여기로 오지 않는다(`_emit(completion=…)`).
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


# 메모리에 사본을 남기는 최근 끝난 run 수. 이보다 오래 끝난 run 은
# 저장소에서 읽고 진행 이벤트는 비어 있다.
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
    (InvalidParameterValueError, "backtest.run.parameter_invalid"),
    (InvalidBacktestRunError, "backtest.run.invalid"),
    (StrategyReferenceNotFoundError, "backtest.strategy.not_found"),
    (StaleStrategyReferenceError, "backtest.strategy.stale"),
    (StrategyRevisionRequiresUpgradeError, "backtest.strategy.requires_upgrade"),
    (InvalidPortfolioRequestError, "portfolio.strategy.invalid"),
)


def rejection_code(error: BaseException) -> AdmissionRejectionCode | None:
    """실행 접수 거절이면 그 코드, 아니면 None(예상 밖 오류)."""
    return next((code for kind, code in _REJECTION_CODES if isinstance(error, kind)), None)
