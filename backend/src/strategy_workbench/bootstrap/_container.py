from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
    EquityDuckdbAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
    SQLiteExperimentRepository,
)
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyDraftRepository,
    SQLiteStrategyRepository,
)
from strategy_workbench.application.assistant_chat.facade.chat import AssistantChatService
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.turns import AssistantTurnRunner
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestArtifactUnreadableError,
    BacktestExecutionRequest,
    BacktestExecutorPort,
    CancellationCheck,
    ProgressCallback,
    RunCancelledError,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    DEFAULT_RUN_SLOTS,
    BacktestRunResult,
    BacktestRunService,
    BacktestRunSpec,
    BacktestRunState,
    TrialLedger,
    rejection_code,
)
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
)
from strategy_workbench.application.experiment_run.facade.experiments import (
    ExperimentRunService,
)
from strategy_workbench.application.experiment_run.facade.ports import (
    AdmittedRun,
    RunSlotUsage,
    TrialResultUnreadableError,
    TrialRunRejectedError,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorResearchService,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.portfolio_design.facade.trace import StrategyTraceService
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
    StrategyDocumentService,
    StrategyDraftService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import StrategyRepositoryPort
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

from ._assistant import (
    DEFAULT_ASSISTANT_SETTINGS,
    AssistantSettings,
    build_assistant_services,
)
from ._file_guard import restrict_to_current_user

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendContainer:
    equity_data: EquityDataPort
    equity_workspace: EquityWorkspaceService
    strategy_repository: StrategyRepositoryPort
    strategy_design: StrategyDesignService
    strategy_authoring: StrategyAuthoringService
    strategy_documents: StrategyDocumentService
    strategy_drafts: StrategyDraftService
    factor_research: FactorResearchService
    portfolio_design: PortfolioDesignService
    strategy_traces: StrategyTraceService
    backtest_runs: BacktestRunService
    experiments: ExperimentRunService
    assistant_profiles: ProviderProfileService
    assistant_chat: AssistantChatService
    assistant_turns: AssistantTurnRunner


EQUITY_ADAPTERS = ("mock", "duckdb")
# `artifact_root`를 주지 않은 컨테이너의 백테스트 산출물 위치. `build_container`가 호출 시점에 읽는
# 모듈 상수라 테스트가 개발자 로컬 `.local/`을 쓰지 않게 tmp로 바꿀 수 있다(#211).
DEFAULT_RUN_ARTIFACT_ROOT = Path(__file__).resolve().parents[3] / ".local" / "backtest-runs"


def build_container(
    *,
    equity_adapter: str = "mock",
    artifact_root: Path | None = None,
    equity_root: Path | None = None,
    strategy_repository_path: str | Path | None = None,
    research_db_path: str | Path | None = None,
    run_slots: int = DEFAULT_RUN_SLOTS,
    trial_hold_seconds: float = 0.0,
    assistant: AssistantSettings = DEFAULT_ASSISTANT_SETTINGS,
) -> BackendContainer:
    """Build one explicit dependency graph; unknown adapters fail instead of falling back.

    `equity_adapter="duckdb"` reads the equity layer at `equity_root` (S21); it needs the
    `equity` optional extra (duckdb) and fails loudly when the root or the extra is missing.
    `strategy_repository_path=None` selects isolated in-memory SQLite for tests; the HTTP
    runtime supplies a durable file path explicitly so both exercise the same adapter contract.
    `research_db_path`(실행 기록)도 같은 규칙이다. 파일이면 현재 사용자 전용으로 잠근다 — 요청에
    인라인 초안 전략 원문이 그대로 담긴다(`_file_guard`).
    `assistant`는 어시스턴트 DB에 같은 규칙을 적용하고, 공급자 adapter 레지스트리를 같이
    나른다. 레지스트리는 A-05·A-06이 자기 항목을 등록할 때까지 비어 있고, 팩토리는 시작할 때가
    아니라 처음 필요할 때 불린다(미설치 SDK가 서버 시작을 막지 않는다).
    """
    equity_data: MockEquityDataAdapter | EquityDuckdbAdapter
    if equity_adapter == "mock":
        equity_data = MockEquityDataAdapter.demo()
    elif equity_adapter == "duckdb":
        if equity_root is None:
            raise ValueError(
                "equity_adapter='duckdb' requires equity_root — "
                "pass the directory holding <table>/MANIFEST.json and equity.duckdb"
            )
        equity_data = EquityDuckdbAdapter(equity_root)
    else:
        raise ValueError(
            f"unsupported equity adapter — equity_adapter={equity_adapter!r} "
            f"available={EQUITY_ADAPTERS}"
        )
    engine_portfolio = BacktestEnginePortfolioAdapter()
    factor_registry = build_default_factor_registry()
    portfolio_design = PortfolioDesignService(
        equity_data,
        engine_portfolio,
        factor_metadata=equity_data,
        factor_registry_version=factor_registry.version,
    )
    metric_registry = build_default_metric_registry()
    strategy_authoring = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version=factor_registry.version,
        dataset_snapshot_id=lambda: equity_data.snapshot().snapshot_id,
        field_catalog=equity_data,
    )
    # 저장소 무결성 검사는 "원문이 저장된 spec 으로 compile 되는가"만 본다. 연결된 어댑터의 필드
    # 계약·capability 로 판정하면 어댑터를 바꾸거나(mock ↔ duckdb) 필드가 빠진 순간 저장된
    # revision 을 읽지 못해 목록·이력이 통째로 500 이 된다. 실행 가능성은 compile·실행이 따로 본다.
    strategy_repository = SQLiteStrategyRepository(
        strategy_repository_path,
        source_spec_hash=_source_spec_hash_resolver(
            StrategyAuthoringService(
                RuamelDocumentCodec(),
                factor_registry_version=factor_registry.version,
                dataset_snapshot_id=lambda: equity_data.snapshot().snapshot_id,
            )
        ),
    )
    strategy_draft_repository = SQLiteStrategyDraftRepository(strategy_repository_path)
    run_artifact_root = artifact_root or DEFAULT_RUN_ARTIFACT_ROOT
    run_repository = SQLiteBacktestRunRepository(research_db_path)
    if run_repository.database_path is not None:
        restrict_to_current_user(run_repository.database_path)
    strategy_traces = StrategyTraceService(portfolio_design, strategy_repository, equity_data)
    executor = BacktestEngineExecutorAdapter(metric_registry)
    hold = _TrialHold(executor, trial_hold_seconds) if trial_hold_seconds > 0 else None
    if hold is not None:
        logger.warning(
            "e2e trial hold is on — experiment trial runs wait %.1fs before the engine stage",
            trial_hold_seconds,
        )
    backtest_runs = BacktestRunService(
        portfolio_design,
        strategy_repository,
        equity_data,
        hold or executor,
        LocalArtifactStore(run_artifact_root),
        run_repository=run_repository,
        new_id=lambda: str(uuid4()),
        run_slots=run_slots,
    )
    # 결과 설명 세션이 완료된 실행을 읽으므로 실행 레지스트리를 먼저 세운다(결과 설명 spec R2).
    assistant_services = build_assistant_services(
        settings=assistant,
        equity_data=equity_data,
        factor_registry=factor_registry,
        strategy_authoring=strategy_authoring,
        backtest_runs=backtest_runs,
    )
    experiments = ExperimentRunService(
        SQLiteExperimentRepository(research_db_path),
        _RunServiceTrialRuns(
            backtest_runs, equity_data, held_run_ids=None if hold is None else hold.run_ids
        ),
        metric_registry=metric_registry,
        new_id=lambda: str(uuid4()),
    )
    # 지난 프로세스가 끝내지 못한 실험의 남은 trial 을 다시 넘긴다(검증 랩 spec D6). 실행
    # 레지스트리가 중단된 run 을 먼저 닫은 뒤라야 한다.
    experiments.recover()
    return BackendContainer(
        equity_data=equity_data,
        equity_workspace=EquityWorkspaceService(equity_data),
        strategy_repository=strategy_repository,
        strategy_design=StrategyDesignService(
            strategy_repository,
            new_id=lambda: str(uuid4()),
        ),
        strategy_authoring=strategy_authoring,
        strategy_documents=StrategyDocumentService(
            strategy_authoring, strategy_repository, new_id=lambda: str(uuid4())
        ),
        strategy_drafts=StrategyDraftService(
            strategy_draft_repository,
            strategy_repository,
        ),
        factor_research=FactorResearchService(
            factor_registry,
            metadata_source=equity_data,
        ),
        portfolio_design=portfolio_design,
        strategy_traces=strategy_traces,
        backtest_runs=backtest_runs,
        experiments=experiments,
        assistant_profiles=assistant_services.profiles,
        assistant_chat=assistant_services.chat,
        assistant_turns=assistant_services.turns,
    )


class _RunServiceTrialRuns:
    """`TrialRunPort` 구현: 실행 서비스를 감싼다(검증 랩 spec D6).

    어시스턴트의 `_RunServiceBacktestResults` 와 같은 모양이다. 실험이 `backtest_run` 유스케이스를
    import 하지 않도록 bootstrap 이 감싼다. 접수 거절(`rejection_code` 가 코드를 주는 오류)은
    `TrialRunRejectedError` 로, 결과 파일 읽기 실패는 `TrialResultUnreadableError` 로 옮긴다.
    """

    def __init__(
        self,
        runs: BacktestRunService,
        equity_data: EquityDataPort,
        *,
        held_run_ids: set[str] | None,
    ) -> None:
        self._runs = runs
        self._equity_data = equity_data
        self._held_run_ids = held_run_ids

    def admit(self, request: BacktestRunSpec) -> AdmittedRun:
        try:
            admission = self._runs.admit(request)
        except Exception as error:
            rejected = _rejected(error)
            if rejected is None:
                raise
            raise rejected from error
        if admission.lineage_id is None:  # pragma: no cover - 실험은 저장 리비전으로만 만든다
            raise RuntimeError(
                f"experiment base run has no lineage — spec_hash={admission.provenance.spec_hash}"
            )
        return AdmittedRun(admission.spec, self._runs.trial_ledger(admission.lineage_id))

    def start(self, request: BacktestRunSpec, *, trial_key: str, owner: str) -> str:
        try:
            run_id = self._runs.start(request, owner=owner, trial_key_override=trial_key).run.run_id
        except Exception as error:
            rejected = _rejected(error)
            if rejected is None:
                raise
            raise rejected from error
        if self._held_run_ids is not None:
            self._held_run_ids.add(run_id)
        return run_id

    def result(self, run_id: str) -> BacktestRunResult:
        try:
            return self._runs.result(run_id)
        except BacktestArtifactUnreadableError as error:
            raise TrialResultUnreadableError(str(error)) from error

    def trial_ledger(self, request: BacktestRunSpec) -> TrialLedger:
        ledger = self._runs.request_ledger(request)
        if ledger is None:  # pragma: no cover - 실험은 저장 리비전으로만 만든다
            raise RuntimeError("experiment base run has no lineage")
        return ledger

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        return self._equity_data.trading_sessions(start, end)

    def states(self, run_ids: Collection[str]) -> Mapping[str, BacktestRunState]:
        return self._runs.states(run_ids)

    def slot_usage(self) -> RunSlotUsage:
        total, running = self._runs.slot_usage()
        return RunSlotUsage(total=total, running=running)

    def schedule(self, owner: str, *, paused: bool, priority: int) -> None:
        self._runs.schedule(owner, paused=paused, weight=priority)

    def cancel(self, run_id: str, *, owner: str) -> None:
        self._runs.cancel(run_id, owner=owner)


def _rejected(error: Exception) -> TrialRunRejectedError | None:
    """접수 거절이면 코드를 실은 `TrialRunRejectedError`, 아니면 None(예상 밖 오류)."""
    code = rejection_code(error)
    return None if code is None else TrialRunRejectedError(code, str(error))


class _TrialHold:
    """e2e 훅: 실험 trial run 을 엔진 단계 앞에서 `seconds` 동안 붙잡는다(취소하면 바로 풀린다).

    화면 e2e 가 도는 trial 을 다시 열고 실험을 일시정지하는 흐름을 재현하게 한다(spec D6). mock
    실행은 1초 안에 끝나 붙잡지 않으면 볼 수 없다. 사용자 단일 실행은 붙잡지 않지만, 실험 trial 이
    이은 사용자 run 은 함께 붙잡힌다(e2e 전용 훅이라 받아들인다).
    """

    def __init__(self, executor: BacktestExecutorPort, seconds: float) -> None:
        self._executor = executor
        self._seconds = seconds
        self.run_ids: set[str] = set()

    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult:
        deadline = time.monotonic() + self._seconds
        while request.run_id in self.run_ids and time.monotonic() < deadline:
            if cancelled():
                raise RunCancelledError(
                    f"held experiment trial cancelled — run_id={request.run_id}"
                )
            time.sleep(0.05)
        return self._executor.execute(request, progress=progress, cancelled=cancelled)


def _source_spec_hash_resolver(
    strategy_authoring: StrategyAuthoringService,
) -> Callable[[str, SourceFormat], str]:
    """Adapt the authoring compile contract to storage integrity without copying its rules."""

    def resolve(source: str, format: SourceFormat) -> str:
        compiled = strategy_authoring.compile(CompileRequest(source, format))
        if compiled.spec_hash is None:
            diagnostics = ", ".join(
                f"{diagnostic.code}@{diagnostic.pointer}" for diagnostic in compiled.diagnostics[:5]
            )
            raise ValueError(f"stored source no longer compiles -- {diagnostics}")
        return compiled.spec_hash

    return resolve
