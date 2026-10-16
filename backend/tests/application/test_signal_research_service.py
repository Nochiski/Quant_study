"""연구 실행은 입력 판본·봉인·실패 상태를 확인하고 미래 결과를 계획에 되돌리지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import cast

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.signal_research.facade.ports import (
    LabelDataLoad,
    LabelDataQuery,
    LabelDataStatus,
    PreparedSignals,
    SignalLabelData,
    SnapshotProof,
)
from strategy_workbench.application.signal_research.facade.research import (
    SignalResearchOutcome,
    SignalResearchRequest,
    SignalResearchService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import (
    ResearchWindowViolationError,
    RunEnvironment,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioObservation,
    TargetTape,
)
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustedCloseObservation,
    ListingInterval,
    PriceLabelSpec,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec

D0, D1 = date(2024, 1, 8), date(2024, 1, 9)
PROOF = SnapshotProof("relative/preserved", "ledger", "contract-source", "descriptor")


@dataclass
class _Guard:
    calls: int = 0
    changed: bool = False

    def verify(self) -> SnapshotProof:
        self.calls += 1
        return (
            replace(PROOF, descriptor_sha256="changed")
            if self.changed and self.calls > 1
            else PROOF
        )


@dataclass
class _Pipeline:
    calls: int = 0
    snapshot: str = "ledger:fields"
    tape_snapshot: str = "ledger:fields"
    member: bool = True

    def prepare(self, strategy: StrategySpec, environment: RunEnvironment) -> PreparedSignals:
        self.calls += 1
        factor_id = strategy.factors[0].factor_id
        return PreparedSignals(
            self.snapshot,
            (
                PortfolioObservation(
                    D0, "a", self.member, (PortfolioFactorValue(factor_id, 1, D0),)
                ),
            ),
            TargetTape(self.tape_snapshot, "strategy", "tape", ()),
            (),
        )


@dataclass
class _Labels:
    calls: int = 0
    snapshot: str = "ledger:fields"
    status: LabelDataStatus = LabelDataStatus.READY

    def load(self, query: LabelDataQuery) -> LabelDataLoad:
        self.calls += 1
        if self.status is not LabelDataStatus.READY:
            return LabelDataLoad(self.status, detail="명시적으로 지원하지 않는 합성 원천")
        assert query.security_ids == ("a",)
        assert query.evaluation_as_of == D1
        return LabelDataLoad(
            LabelDataStatus.READY,
            SignalLabelData(
                self.snapshot,
                (D0, D1),
                (
                    AdjustedCloseObservation(D0, "a", 100, D0),
                    AdjustedCloseObservation(D1, "a", 110, D1),
                ),
                (ListingInterval("a", D0, D1),),
                (),
                (),
            ),
        )


def _request() -> SignalResearchRequest:
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    return SignalResearchRequest(
        strategy,
        RunEnvironment(start=D0, end=D0, universe_id="krx.common-stock"),
        (strategy.factors[0].factor_id,),
        PriceLabelSpec((1,)),
        D1,
        D1,
    )


def test_use_case_preserves_requested_factor_and_original_signal_evidence() -> None:
    guard, pipeline, labels = _Guard(), _Pipeline(), _Labels()
    outcome = SignalResearchService(guard, pipeline, labels).run(_request())
    assert outcome.status is LabelDataStatus.READY
    assert outcome.report is not None
    assert outcome.report.input_proof == PROOF
    assert outcome.report.evaluations[0].samples[0].signal_value == 1
    assert outcome.report.evaluations[0].samples[0].label.return_fraction == pytest.approx(0.1)
    assert (guard.calls, pipeline.calls, labels.calls) == (2, 1, 1)


@pytest.mark.parametrize("change", ["wrong_ledger", "wrong_fields", "wrong_tape", "changed_proof"])
def test_mismatched_or_changed_snapshot_is_never_returned_as_research_evidence(change: str) -> None:
    guard, pipeline, labels = _Guard(), _Pipeline(), _Labels()
    if change == "wrong_ledger":
        pipeline.snapshot = "different:fields"
    elif change == "wrong_fields":
        labels.snapshot = "ledger:different-fields"
    elif change == "wrong_tape":
        pipeline.tape_snapshot = "different:fields"
    else:
        guard.changed = True
    with pytest.raises(ValueError, match="snapshot|preserved|changed"):
        SignalResearchService(guard, pipeline, labels).run(_request())


def test_unsupported_label_source_is_a_failure_value_and_still_rechecks_preserved_input() -> None:
    guard, pipeline, labels = _Guard(), _Pipeline(), _Labels(status=LabelDataStatus.UNSUPPORTED)
    outcome = SignalResearchService(guard, pipeline, labels).run(_request())
    assert outcome.status is LabelDataStatus.UNSUPPORTED
    assert outcome.report is None
    assert outcome.detail == "명시적으로 지원하지 않는 합성 원천"
    assert guard.calls == 2


@pytest.mark.parametrize(
    "change", ["sealed", "future_signal", "calendar_short", "unknown_factor", "duplicate_factor"]
)
def test_invalid_research_request_is_rejected_before_reading_preserved_data(change: str) -> None:
    guard, pipeline, labels = _Guard(), _Pipeline(), _Labels()
    request = _request()
    if change == "sealed":
        request = replace(
            request, environment=replace(request.environment, start=date(2019, 12, 30))
        )
    elif change == "future_signal":
        request = replace(request, environment=replace(request.environment, end=date(2024, 1, 10)))
    elif change == "calendar_short":
        request = replace(request, label_calendar_through=D0)
    elif change == "unknown_factor":
        request = replace(request, factor_ids=("unknown",))
    else:
        request = replace(request, factor_ids=(*request.factor_ids, *request.factor_ids))
    error = ResearchWindowViolationError if change == "sealed" else ValueError
    with pytest.raises(error):
        SignalResearchService(guard, pipeline, labels).run(request)
    assert (guard.calls, pipeline.calls, labels.calls) == (0, 0, 0)


def test_outcome_and_label_load_reject_impossible_success_failure_combinations() -> None:
    outcome = SignalResearchService(_Guard(), _Pipeline(), _Labels()).run(_request())
    report = outcome.report
    assert report is not None
    impossible = (
        (LabelDataStatus.READY, None, None),
        (LabelDataStatus.READY, report, "오류와 성공이 함께 있음"),
        (LabelDataStatus.UNSUPPORTED, report, "실패에 보고서가 있음"),
        (LabelDataStatus.UNAVAILABLE, None, None),
        (LabelDataStatus.UNAVAILABLE, None, " "),
        (cast(LabelDataStatus, "unknown"), None, "알 수 없는 상태"),
    )
    for status, invalid_report, detail in impossible:
        with pytest.raises(ValueError):
            SignalResearchOutcome(status, invalid_report, detail)
    loaded = _Labels().load(LabelDataQuery(D0, D1, D1, ("a",)))
    assert loaded.data is not None
    for status, data, detail in (
        (LabelDataStatus.READY, None, None),
        (LabelDataStatus.READY, loaded.data, "실패 사유"),
        (LabelDataStatus.UNSUPPORTED, loaded.data, "실패에 값이 있음"),
        (LabelDataStatus.UNAVAILABLE, None, " "),
        (cast(LabelDataStatus, "unknown"), None, "알 수 없는 상태"),
    ):
        with pytest.raises(ValueError):
            LabelDataLoad(status, data, detail)


def test_empty_universe_is_reported_without_a_fabricated_comparison() -> None:
    guard, labels = _Guard(), _Labels()
    outcome = SignalResearchService(guard, _Pipeline(member=False), labels).run(_request())
    assert outcome.status is LabelDataStatus.UNAVAILABLE
    assert outcome.report is None
    assert labels.calls == 0
    assert guard.calls == 2
