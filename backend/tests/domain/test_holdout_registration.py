"""모의 기준은 수식/단위를 새로 계산하지 않고 등록한 필수 비교와 부재를 정직하게 보존한다."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    MetricUnavailableReason,
    MetricUnit,
    MetricValue,
)
from strategy_workbench.domain.backtest.facade.holdout import (
    CriterionComparison,
    HoldoutCriterion,
    HoldoutRegistration,
    HoldoutSimulationObservation,
    SimulationVerdict,
    evaluate_holdout_simulation,
)
from tests.application.test_holdout_registration_simulation import (
    _AT,
    _CRITERIA,
    _ENVIRONMENT,
    _Source,
)


def _registration(tmp_path: Path) -> HoldoutRegistration:
    source = _Source(tmp_path)
    pin = source.pin(source.reference, source.selection_id, {})
    return HoldoutRegistration(
        "00000000-0000-0000-0000-000000000001", _AT, pin, _ENVIRONMENT, _CRITERIA
    )


def _observation(
    registration: HoldoutRegistration, metrics: tuple[MetricValue, ...]
) -> HoldoutSimulationObservation:
    candidate = registration.candidate
    return HoldoutSimulationObservation(
        metrics,
        candidate.metric_definitions,
        candidate.metric_registry_version,
        candidate.evaluator_version,
        candidate.data_snapshot_id,
    )


@pytest.mark.parametrize(
    ("value", "samples", "expected", "reason"),
    [
        (0.5, 30, "simulation_met", None),
        (0.49, 100, "simulation_not_met", None),
        (None, 100, "unavailable", "metric_unavailable"),
        (0.8, 29, "unavailable", "insufficient_samples"),
        (float("nan"), 100, "unavailable", "nonfinite_or_untyped_metric"),
        (float("inf"), 100, "unavailable", "nonfinite_or_untyped_metric"),
        (True, 100, "unavailable", "nonfinite_or_untyped_metric"),
    ],
)
def test_registered_numeric_comparison_and_required_data(
    tmp_path: Path, value: float | None, samples: int, expected: str, reason: str | None
) -> None:
    registration = _registration(tmp_path)
    observation = _observation(
        registration, (MetricValue("sharpe", value, MetricScope.FULL, samples),)
    )
    result = evaluate_holdout_simulation(registration, observation)
    assert result.verdict == expected
    assert result.outcomes[0].reason == reason
    assert result.registration_hash == registration.registration_hash
    assert result.namespace == "simulation"


def test_all_required_and_unavailable_dominates_a_known_failure(tmp_path: Path) -> None:
    registration = _registration(tmp_path)
    criteria = (
        *registration.criteria,
        HoldoutCriterion(
            "max_drawdown",
            MetricScope.FULL,
            MetricUnit.PERCENT,
            CriterionComparison.AT_LEAST,
            -0.2,
            30,
        ),
    )
    registration = replace(registration, criteria=criteria, registration_hash="")
    metrics = (MetricValue("sharpe", 0.1, MetricScope.FULL, 100),)
    result = evaluate_holdout_simulation(registration, _observation(registration, metrics))
    assert result.outcomes[0].verdict is SimulationVerdict.SIMULATION_NOT_MET
    assert result.outcomes[1].reason == "missing_or_duplicate_metric"
    assert result.verdict is SimulationVerdict.UNAVAILABLE


def test_null_metric_keeps_original_unavailable_reason(tmp_path: Path) -> None:
    registration = _registration(tmp_path)
    result = evaluate_holdout_simulation(
        registration,
        _observation(
            registration,
            (
                MetricValue(
                    "sharpe",
                    None,
                    MetricScope.FULL,
                    100,
                    unavailable_reason=MetricUnavailableReason.ZERO_RETURN_VARIANCE,
                ),
            ),
        ),
    )
    assert (
        result.outcomes[0].metric_unavailable_reason is MetricUnavailableReason.ZERO_RETURN_VARIANCE
    )


@pytest.mark.parametrize(
    "change",
    ["registry", "evaluator", "snapshot", "unit", "definition_version", "duplicate", "scope"],
)
def test_mismatched_or_ambiguous_observation_never_passes(tmp_path: Path, change: str) -> None:
    registration = _registration(tmp_path)
    metrics = (MetricValue("sharpe", 0.8, MetricScope.FULL, 100),)
    observation = _observation(registration, metrics)
    if change == "registry":
        observation = replace(observation, metric_registry_version="other")
    elif change == "evaluator":
        observation = replace(observation, evaluator_version="other")
    elif change == "snapshot":
        observation = replace(observation, data_snapshot_id="other")
    elif change == "unit":
        observation = replace(
            observation,
            metric_definitions=tuple(
                replace(item, unit=MetricUnit.COUNT) if item.metric_id == "sharpe" else item
                for item in observation.metric_definitions
            ),
        )
    elif change == "definition_version":
        observation = replace(
            observation,
            metric_definitions=tuple(
                replace(item, version=item.version + 1) if item.metric_id == "sharpe" else item
                for item in observation.metric_definitions
            ),
        )
    elif change == "duplicate":
        observation = replace(observation, metrics=metrics * 2)
    else:
        observation = replace(
            observation, metrics=(replace(metrics[0], scope=MetricScope.OUT_OF_SAMPLE),)
        )
    assert (
        evaluate_holdout_simulation(registration, observation).verdict
        is SimulationVerdict.UNAVAILABLE
    )


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), True])
def test_criteria_reject_nonfinite_or_untyped_threshold(threshold: float) -> None:
    with pytest.raises(ValueError):
        replace(_CRITERIA[0], threshold=threshold)


@pytest.mark.parametrize(
    "change",
    [
        "candidate_hash",
        "selection_hash",
        "parameters",
        "selection_revision",
        "criteria",
        "authority",
    ],
)
def test_registered_identity_and_criteria_cannot_be_silently_changed(
    tmp_path: Path, change: str
) -> None:
    registration = _registration(tmp_path)
    with pytest.raises(ValueError):
        if change == "candidate_hash":
            replace(registration.candidate, candidate_hash="0" * 64)
        elif change == "selection_hash":
            replace(registration.candidate, selection_hash="0" * 64)
        elif change == "parameters":
            replace(
                registration.candidate,
                parameter_values=(("scale", -1.0), ("mode", "a")),
                candidate_hash="",
            )
        elif change == "selection_revision":
            replace(
                registration.candidate,
                reference=replace(registration.candidate.reference, revision=2),
                candidate_hash="",
            )
        elif change == "criteria":
            replace(registration, criteria=(replace(_CRITERIA[0], threshold=0.9),))
        else:
            replace(registration, namespace="actual")
    with pytest.raises(FrozenInstanceError):
        registration.__setattr__("criteria", ())


def test_at_most_comparison_and_no_fabricated_result_verdict(tmp_path: Path) -> None:
    registration = _registration(tmp_path)
    registration = replace(
        registration,
        criteria=(replace(_CRITERIA[0], comparison=CriterionComparison.AT_MOST),),
        registration_hash="",
    )
    result = evaluate_holdout_simulation(
        registration,
        _observation(registration, (MetricValue("sharpe", 0.8, MetricScope.FULL, 100),)),
    )
    assert result.verdict is SimulationVerdict.SIMULATION_NOT_MET
    with pytest.raises(ValueError, match="overall"):
        replace(result, verdict=SimulationVerdict.SIMULATION_MET)
    with pytest.raises(ValueError, match="criterion verdict"):
        replace(result.outcomes[0], verdict=SimulationVerdict.SIMULATION_MET)


def test_finite_metric_with_unavailable_reason_never_becomes_a_pass(tmp_path: Path) -> None:
    registration = _registration(tmp_path)
    metric = MetricValue(
        "sharpe",
        0.8,
        MetricScope.FULL,
        100,
        unavailable_reason=MetricUnavailableReason.ZERO_RETURN_VARIANCE,
    )
    result = evaluate_holdout_simulation(registration, _observation(registration, (metric,)))
    assert result.verdict is SimulationVerdict.UNAVAILABLE
    assert result.outcomes[0].reason == "metric_unavailable"
    assert (
        result.outcomes[0].metric_unavailable_reason is MetricUnavailableReason.ZERO_RETURN_VARIANCE
    )
    with pytest.raises(ValueError, match="cannot coexist"):
        replace(result.outcomes[0], verdict=SimulationVerdict.SIMULATION_MET, reason=None)


def test_unknown_metric_unavailable_reason_is_rejected_at_observation_boundary(
    tmp_path: Path,
) -> None:
    registration = _registration(tmp_path)
    metric = MetricValue("sharpe", 0.8, MetricScope.FULL, 100)
    # 기존 MetricValue owner는 바꾸지 않고 모의 입력 경계만 검증한다.
    object.__setattr__(metric, "unavailable_reason", "unknown-reason")
    with pytest.raises(ValueError, match="must be typed"):
        _observation(registration, (metric,))
