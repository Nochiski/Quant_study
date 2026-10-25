"""임시 저장 리비전·선택과 실제 SQLite를 관통하는 합성 사전등록 절차."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4

import pytest

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
    SQLiteExperimentRepository,
    SQLiteHoldoutSimulationLedger,
)
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyRepository,
)
from strategy_workbench.application.experiment_run.ports.outgoing.experiment_repository import (
    ExperimentSelection,
)
from strategy_workbench.application.holdout_registration.facade.registration import (
    HoldoutRegistrationService,
    HoldoutSimulationRejectedError,
    HoldoutSimulationStorageError,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyRevisionRecord,
)
from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    MetricUnit,
    MetricValue,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.holdout import (
    CriterionComparison,
    FrozenHoldoutCandidate,
    HoldoutCriterion,
    HoldoutRegistration,
    HoldoutSimulationClaim,
    HoldoutSimulationObservation,
    SimulationClaimStatus,
    SimulationVerdict,
)
from strategy_workbench.domain.canonical.facade.encoding import (
    canonical_json_hash,
    canonical_payload_json,
)
from strategy_workbench.domain.strategy.facade.document import SourceFormat, source_hash_of
from strategy_workbench.domain.strategy.facade.provenance import SavedRevisionReference
from strategy_workbench.domain.strategy.facade.specification import (
    ParameterValue,
    canonical_strategy_json,
    resolve_parameter_values,
    strategy_spec_hash,
)
from tests.application.test_experiment_run import _BASE, _STRATEGY, _FakeRuns, _request, _service
from tests.contract.test_strategy_repository import _source_spec_hash

_AT = datetime(2026, 10, 18, tzinfo=UTC)
_ENVIRONMENT = RunEnvironment(
    start=datetime(2016, 1, 1).date(),
    end=datetime(2019, 12, 31).date(),
    universe_id="synthetic-only",
)
_CRITERIA = (
    HoldoutCriterion(
        "sharpe", MetricScope.FULL, MetricUnit.RATIO, CriterionComparison.AT_LEAST, 0.5, 30
    ),
)


class _Source:
    def __init__(self, root: Path) -> None:
        self.strategies = SQLiteStrategyRepository(
            root / "strategies.sqlite3", source_spec_hash=_source_spec_hash
        )
        self.spec = replace(
            _STRATEGY, identity=replace(_STRATEGY.identity, strategy_id="s-1", revision=1)
        )
        text = canonical_strategy_json(self.spec)
        self.strategies.add(
            StrategyRevisionRecord(
                self.spec,
                strategy_spec_hash(self.spec),
                RevisionSource(SourceFormat.JSON, text, source_hash_of(text)),
                RevisionProvenance(RevisionOrigin.DOCUMENT, _AT),
            )
        )
        self.reference = SavedRevisionReference(
            "s-1", 1, strategy_spec_hash(self.spec), "saved_revision"
        )
        self.experiments = SQLiteExperimentRepository(root / "research.sqlite3")
        runs = _FakeRuns()
        runs.strategy = self.spec
        record = (
            _service(runs, repository=self.experiments)
            .create(
                _request(
                    replace(_BASE, strategy_source=self.reference, parameter_values={"mode": "a"})
                )
            )
            .record
        )
        self.selection = ExperimentSelection(
            record.experiment_id, 0, "s-1", 1, {"mode": "a", "scale": 1.0}, "합성 fixture 선택", _AT
        )
        self.experiments.add_selection(self.selection)
        self.selection_id = f"{record.experiment_id}:0"

    def pin(
        self,
        reference: SavedRevisionReference,
        selection_id: str,
        parameter_values: Mapping[str, ParameterValue],
    ) -> FrozenHoldoutCandidate:
        record = self.strategies.get(reference.strategy_id, reference.revision)
        assert selection_id == self.selection_id
        selected = self.experiments.selections(self.selection.experiment_id)[0]
        payload = asdict(selected)
        text = canonical_payload_json(payload)
        assert record.source is not None
        return FrozenHoldoutCandidate(
            reference,
            record.spec,
            record.source.text,
            record.source.source_hash,
            tuple(
                sorted(resolve_parameter_values(record.spec.parameters, parameter_values).items())
            ),
            selection_id,
            text,
            canonical_json_hash(payload),
            "simulation-metrics-v1",
            "simulation-evaluator-v1",
            "synthetic-snapshot",
            canonical_payload_json({"source": "test fixture"}),
            build_default_metric_registry().definitions(),
        )


class _Executor:
    def __init__(self, metrics: tuple[MetricValue, ...] | None = None) -> None:
        self.calls = 0
        self.lock = Lock()
        self.metrics = (
            (MetricValue("sharpe", 0.8, MetricScope.FULL, 100),) if metrics is None else metrics
        )

    def execute(self, registration: HoldoutRegistration) -> HoldoutSimulationObservation:
        with self.lock:
            self.calls += 1
        candidate = registration.candidate
        return HoldoutSimulationObservation(
            self.metrics,
            candidate.metric_definitions,
            candidate.metric_registry_version,
            candidate.evaluator_version,
            candidate.data_snapshot_id,
        )


@pytest.fixture
def setup(
    tmp_path: Path,
) -> tuple[
    _Source,
    _Executor,
    SQLiteHoldoutSimulationLedger,
    HoldoutRegistrationService,
    HoldoutRegistration,
]:
    source = _Source(tmp_path)
    executor = _Executor()
    ledger = SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3")
    service = HoldoutRegistrationService(source, executor, ledger, now=lambda: _AT)
    registration = service.register(source.reference, source.selection_id, _ENVIRONMENT, _CRITERIA)
    return source, executor, ledger, service, registration


def test_registration_claim_observation_replay_restart_and_existing_records_unchanged(
    setup, tmp_path: Path
) -> None:
    source, executor, ledger, service, registration = setup
    old_selection = source.experiments.selections(source.selection.experiment_id)
    runs = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
    old_ledger = runs.trial_ledger("s-1")
    request_id = str(uuid4())
    result = service.simulate(registration.registration_id, request_id)
    assert result.status is SimulationClaimStatus.OBSERVED
    assert result.result is not None and result.result.verdict is SimulationVerdict.SIMULATION_MET
    assert result.namespace == "simulation"
    assert executor.calls == 1
    assert service.simulate(registration.registration_id, request_id) == result
    ledger.close()
    restarted = HoldoutRegistrationService(
        source,
        executor,
        SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3"),
        now=lambda: _AT,
    )
    restarted.recover()
    assert restarted.registration(registration.registration_id) == registration
    assert restarted.simulate(registration.registration_id, request_id) == result
    assert executor.calls == 1
    assert runs.trial_ledger("s-1") == old_ledger
    assert source.experiments.selections(source.selection.experiment_id) == old_selection
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM sealed_window_blocks").fetchone()[0] == 0
    with pytest.raises(HoldoutSimulationRejectedError, match="consumed"):
        restarted.simulate(registration.registration_id, str(uuid4()))


def test_two_independent_services_concurrently_execute_only_once(setup, tmp_path: Path) -> None:
    source, executor, _, service, registration = setup
    other = HoldoutRegistrationService(
        source,
        executor,
        SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3"),
        now=lambda: _AT,
    )
    barrier = Barrier(2)

    def start(service: HoldoutRegistrationService) -> HoldoutSimulationClaim | str:
        barrier.wait()
        try:
            return service.simulate(registration.registration_id, str(uuid4()))
        except HoldoutSimulationRejectedError:
            return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, (service, other)))
    assert sum(isinstance(item, HoldoutSimulationClaim) for item in results) == 1
    assert results.count("consumed") == 1
    assert executor.calls == 1


def test_same_uuid_concurrently_replays_the_same_claim_without_reexecution(
    setup, tmp_path: Path
) -> None:
    source, executor, _, service, registration = setup
    other = HoldoutRegistrationService(
        source,
        executor,
        SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3"),
        now=lambda: _AT,
    )
    request_id = str(uuid4())
    barrier = Barrier(2)

    def start(service: HoldoutRegistrationService) -> HoldoutSimulationClaim:
        barrier.wait()
        return service.simulate(registration.registration_id, request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, (service, other)))
    assert all(result.request_id == request_id for result in results)
    assert executor.calls == 1


def test_different_registration_and_snapshot_do_not_create_a_fresh_window(setup) -> None:
    _, executor, ledger, service, registration = setup
    changed = replace(
        registration,
        registration_id=str(uuid4()),
        registration_hash="",
        candidate=replace(
            registration.candidate,
            data_snapshot_id="different-synthetic-snapshot",
            candidate_hash="",
        ),
    )
    ledger.register(changed)
    request_id = str(uuid4())
    service.simulate(registration.registration_id, request_id)
    with pytest.raises(HoldoutSimulationRejectedError, match="another registration"):
        service.simulate(changed.registration_id, request_id)
    with pytest.raises(HoldoutSimulationRejectedError, match="consumed"):
        service.simulate(changed.registration_id, str(uuid4()))
    assert executor.calls == 1


class _Crash(BaseException):
    pass


def test_claim_before_executor_crash_remains_consumed_after_explicit_restart_recovery(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, executor, _, service, registration = setup

    def crash(registration: HoldoutRegistration) -> HoldoutSimulationObservation:
        raise _Crash()

    monkeypatch.setattr(executor, "execute", crash)
    request_id = str(uuid4())
    with pytest.raises(_Crash):
        service.simulate(registration.registration_id, request_id)
    restarted_executor = _Executor()
    restarted = HoldoutRegistrationService(
        source,
        restarted_executor,
        SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3"),
        now=lambda: _AT,
    )
    restarted.recover()
    recovered = restarted.simulate(registration.registration_id, request_id)
    assert recovered.status is SimulationClaimStatus.INTERRUPTED_UNKNOWN
    assert recovered.result is None
    assert restarted_executor.calls == 0


def test_executor_exception_and_missing_metric_are_consumed_unavailable_not_pass(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, executor, _, service, registration = setup

    def fail(registration: HoldoutRegistration) -> HoldoutSimulationObservation:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(executor, "execute", fail)
    request_id = str(uuid4())
    result = service.simulate(registration.registration_id, request_id)
    assert result.status is SimulationClaimStatus.INTERRUPTED_UNKNOWN
    assert result.result is None
    assert service.simulate(registration.registration_id, request_id) == result


@pytest.mark.parametrize("after_commit", [False, True])
def test_claim_commit_uncertainty_never_executes_or_automatically_retries(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    source, executor, ledger, service, registration = setup
    original = ledger._database.transaction

    @contextmanager
    def uncertain(*, write: bool) -> Iterator[sqlite3.Connection]:
        with original(write=write) as connection:
            yield connection
            if write and not after_commit:
                raise HoldoutSimulationStorageError("commit outcome unknown before commit")
        if write and after_commit:
            raise HoldoutSimulationStorageError("commit outcome unknown after durable commit")

    monkeypatch.setattr(ledger._database, "transaction", uncertain)
    request_id = str(uuid4())
    with pytest.raises(HoldoutSimulationStorageError):
        service.simulate(registration.registration_id, request_id)
    assert executor.calls == 0
    with pytest.raises(HoldoutSimulationStorageError, match="manual"):
        service.simulate(registration.registration_id, request_id)
    fresh = SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3")
    assert (fresh.get(request_id) is not None) is after_commit
    # 행 부재는 직전 호출 executor=0 제어 흐름만 입증한다. 임의 DB 손실 복구를 주장하지 않는다.
    if after_commit:
        restarted = HoldoutRegistrationService(source, executor, fresh, now=lambda: _AT)
        restarted.recover()
        assert (
            restarted.simulate(registration.registration_id, request_id).status
            is SimulationClaimStatus.INTERRUPTED_UNKNOWN
        )
        assert executor.calls == 0


def test_result_loss_stays_consumed_and_never_reexecutes(setup, tmp_path: Path) -> None:
    _, executor, _, service, registration = setup
    request_id = str(uuid4())
    service.simulate(registration.registration_id, request_id)
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        connection.execute(
            "DELETE FROM holdout_simulation_results WHERE request_id=?", (request_id,)
        )
    result = service.simulate(registration.registration_id, request_id)
    assert result.status is SimulationClaimStatus.INTERRUPTED_UNKNOWN
    assert result.result is None
    assert executor.calls == 1


@pytest.mark.parametrize("after_finish", [False, True])
def test_result_store_uncertainty_keeps_consumption_and_replays_without_execution(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_finish: bool
) -> None:
    source, executor, ledger, service, registration = setup
    original = ledger.finish

    def uncertain(claim: HoldoutSimulationClaim) -> None:
        if after_finish:
            original(claim)
        raise HoldoutSimulationStorageError("synthetic result commit uncertainty")

    monkeypatch.setattr(ledger, "finish", uncertain)
    request_id = str(uuid4())
    with pytest.raises(HoldoutSimulationStorageError):
        service.simulate(registration.registration_id, request_id)
    assert executor.calls == 1
    with pytest.raises(HoldoutSimulationStorageError, match="manual"):
        service.simulate(registration.registration_id, request_id)
    restarted = HoldoutRegistrationService(
        source,
        executor,
        SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3"),
        now=lambda: _AT,
    )
    restarted.recover()
    result = restarted.simulate(registration.registration_id, request_id)
    assert result.status is (
        SimulationClaimStatus.OBSERVED
        if after_finish
        else SimulationClaimStatus.INTERRUPTED_UNKNOWN
    )
    assert executor.calls == 1


def test_result_payload_corruption_stays_consumed_without_showing_a_value(
    setup, tmp_path: Path
) -> None:
    _, executor, _, service, registration = setup
    request_id = str(uuid4())
    service.simulate(registration.registration_id, request_id)
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        connection.execute(
            (
                "UPDATE holdout_simulation_results SET payload=replace(payload,'0.8','0.9') "
                "WHERE request_id=?"
            ),
            (request_id,),
        )
    result = service.simulate(registration.registration_id, request_id)
    assert result.status is SimulationClaimStatus.STORAGE_UNKNOWN
    assert result.result is None
    assert executor.calls == 1


def test_frozen_source_is_not_queried_again_during_simulation(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, executor, _, service, registration = setup

    def changed(*args: object, **kwargs: object) -> FrozenHoldoutCandidate:
        raise AssertionError("registered source was resolved again")

    monkeypatch.setattr(source, "pin", changed)
    service.simulate(registration.registration_id, str(uuid4()))
    assert executor.calls == 1


def test_new_lineage_is_not_a_new_simulation_window_and_registration_cannot_be_overwritten(
    setup,
) -> None:
    _, executor, ledger, service, registration = setup
    candidate = registration.candidate
    reference = replace(candidate.reference, strategy_id="another-lineage")
    spec = replace(
        candidate.spec, identity=replace(candidate.spec.identity, strategy_id=reference.strategy_id)
    )
    import json

    selection = json.loads(candidate.selection_json)
    selection["strategy_id"] = reference.strategy_id
    changed = replace(
        candidate,
        reference=reference,
        spec=spec,
        selection_json=canonical_payload_json(selection),
        selection_hash=canonical_json_hash(selection),
        candidate_hash="",
    )
    second = replace(
        registration, registration_id=str(uuid4()), candidate=changed, registration_hash=""
    )
    ledger.register(second)
    with pytest.raises(HoldoutSimulationRejectedError, match="already bound"):
        ledger.register(
            replace(
                registration, criteria=(replace(_CRITERIA[0], threshold=0.9),), registration_hash=""
            )
        )
    service.simulate(registration.registration_id, str(uuid4()))
    with pytest.raises(HoldoutSimulationRejectedError, match="consumed"):
        service.simulate(second.registration_id, str(uuid4()))
    assert executor.calls == 1


def test_migration_from_v8_preserves_existing_schema_and_adds_only_simulation_records(
    tmp_path: Path,
) -> None:
    from strategy_workbench.adapters.outbound.research_sqlite._schema import V8_SCHEMA_OBJECTS

    path = tmp_path / "old-research.sqlite3"
    with sqlite3.connect(path) as connection:
        for _, _, statement in V8_SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute("PRAGMA application_id = 1398231635")
        connection.execute("PRAGMA user_version = 8")
        before = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    SQLiteHoldoutSimulationLedger(path).close()
    with sqlite3.connect(path) as connection:
        after = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        assert all(after[name] == sql for name, sql in before)
        # v8 → v9(모의 사전등록) → v10(목록 머리 지표)를 차례로 올린다.
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert (
            connection.execute("SELECT count(*) FROM holdout_simulation_claims").fetchone()[0] == 0
        )


def test_plain_research_window_gate_still_rejects_the_same_sealed_start() -> None:
    from strategy_workbench.domain.backtest.facade.environment import (
        ResearchWindowViolationError,
        require_research_window,
    )

    with pytest.raises(ResearchWindowViolationError):
        require_research_window(_ENVIRONMENT.start, requested_by="synthetic gate regression")


def test_finite_but_unavailable_metric_survives_sqlite_roundtrip_as_unavailable(
    setup, tmp_path: Path
) -> None:
    from strategy_workbench.domain.analytics.facade.metrics import MetricUnavailableReason

    _, executor, _, service, registration = setup
    executor.metrics = (
        MetricValue(
            "sharpe",
            0.8,
            MetricScope.FULL,
            100,
            unavailable_reason=MetricUnavailableReason.ZERO_RETURN_VARIANCE,
        ),
    )
    request_id = str(uuid4())
    result = service.simulate(registration.registration_id, request_id)
    assert result.result is not None and result.result.verdict is SimulationVerdict.UNAVAILABLE
    replay = SQLiteHoldoutSimulationLedger(tmp_path / "research.sqlite3").get(request_id)
    assert replay == result
    assert replay is not None and replay.result is not None
    assert (
        replay.result.outcomes[0].metric_unavailable_reason
        is MetricUnavailableReason.ZERO_RETURN_VARIANCE
    )
    assert executor.calls == 1
