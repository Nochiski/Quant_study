"""사전등록과 합성 모의 판정. 실제 봉인 관측이나 개봉 권한을 부여하지 않는다."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

from strategy_workbench.domain.analytics.facade.metrics import (
    MetricDefinition,
    MetricScope,
    MetricUnavailableReason,
    MetricUnit,
    MetricValue,
)
from strategy_workbench.domain.canonical.facade.encoding import (
    canonical_json_hash,
    canonical_payload_json,
)
from strategy_workbench.domain.strategy.facade.document import source_hash_of
from strategy_workbench.domain.strategy.facade.provenance import SavedRevisionReference
from strategy_workbench.domain.strategy.facade.specification import (
    ParameterValue,
    StrategySpec,
    resolve_parameter_values,
    strategy_spec_hash,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy

from ._canonical import environment_hash
from ._models import RunEnvironment
from ._research_window import SEALED_END, SEALED_START

SIMULATION_NAMESPACE = "simulation"
SIMULATION_WINDOW = f"KRX:{SEALED_START.isoformat()}:{SEALED_END.isoformat()}"


def _require_frozen_value(value: object) -> None:
    """해시를 고정한 뒤 list/dict 또는 mutable dataclass로 내용을 바꾸는 입력은 거절한다."""
    if value is None or isinstance(value, (str, bool, int, float, date)):
        return
    if isinstance(value, tuple):
        for item in value:
            _require_frozen_value(item)
        return
    dataclass_parameters = getattr(type(value), "__dataclass_params__", None)
    if (
        is_dataclass(value)
        and not isinstance(value, type)
        and dataclass_parameters is not None
        and dataclass_parameters.frozen
    ):
        for item in fields(value):
            _require_frozen_value(getattr(value, item.name))
        return
    raise ValueError(f"frozen registration contains mutable input — type={type(value).__name__}")


def _nonblank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank — got={value!r}")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key — key={key!r}")
        result[key] = value
    return result


def canonical_object(text: str) -> dict[str, object]:
    """선택·자료 출처의 전체 내용을 유한하고 중복 없는 정규 JSON으로 고정한다."""
    value = json.loads(text, object_pairs_hook=_unique_pairs)
    if not isinstance(value, dict) or canonical_payload_json(value) != text:
        raise ValueError("expected a canonical JSON object")
    return cast(dict[str, object], value)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone aware — got={value!r}")


@dataclass(frozen=True)
class FrozenHoldoutCandidate:
    """출처 포트가 정확한 저장 리비전·선택 기록을 읽어 고정한 값. 현재 선택을 다시 해소하지 않는다.

    source_text와 spec의 원문 해석 정합은 source-pin 포트의 책임이다. 여기서는 저장 신원,
    원문 해시, 해소 값과 선택 기록의 결합을 검증한다.
    """

    reference: SavedRevisionReference
    spec: StrategySpec
    source_text: str
    source_hash: str
    parameter_values: tuple[tuple[str, ParameterValue], ...]
    selection_id: str
    selection_json: str
    selection_hash: str
    metric_registry_version: str
    evaluator_version: str
    data_snapshot_id: str
    data_provenance_json: str
    metric_definitions: tuple[MetricDefinition, ...]
    candidate_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "selection_id",
            "metric_registry_version",
            "evaluator_version",
            "data_snapshot_id",
        ):
            _nonblank(getattr(self, name), name)
        if not isinstance(self.reference, SavedRevisionReference) or not isinstance(
            self.spec, StrategySpec
        ):
            raise ValueError("candidate requires typed saved revision and strategy")
        _require_frozen_value(self.spec)
        _require_frozen_value(self.reference)
        _require_frozen_value(self.metric_definitions)
        if self.reference.kind != "saved_revision" or type(self.reference.revision) is not int:
            raise ValueError("candidate must name a typed saved revision")
        _nonblank(self.reference.strategy_id, "strategy_id")
        if self.reference.revision < 1 or (
            self.spec.identity.strategy_id,
            self.spec.identity.revision,
        ) != (self.reference.strategy_id, self.reference.revision):
            raise ValueError("candidate revision identity differs from its source")
        if not validate_strategy(self.spec).valid:
            raise ValueError("candidate strategy is invalid")
        if strategy_spec_hash(self.spec) != self.reference.expected_spec_hash:
            raise ValueError("candidate spec hash differs from pinned revision")
        if not self.source_text.strip() or source_hash_of(self.source_text) != self.source_hash:
            raise ValueError("candidate source hash differs from pinned text")
        if not isinstance(self.parameter_values, tuple) or any(
            not isinstance(pair, tuple) or len(pair) != 2 for pair in self.parameter_values
        ):
            raise ValueError("candidate parameter values must be immutable pairs")
        requested = dict(self.parameter_values)
        if len(requested) != len(self.parameter_values):
            raise ValueError("candidate has duplicate parameter ids")
        resolved = resolve_parameter_values(self.spec.parameters, requested)
        expected = tuple(sorted(resolved.items()))
        if canonical_payload_json(self.parameter_values) != canonical_payload_json(expected):
            raise ValueError("candidate parameter values must be fully resolved and normalized")
        selection = canonical_object(self.selection_json)
        if canonical_json_hash(selection) != self.selection_hash:
            raise ValueError("selection content hash differs from pinned selection")
        if type(selection.get("revision")) is not int:
            raise ValueError("selection revision must be an integer")
        if (
            selection.get("strategy_id") != self.reference.strategy_id
            or selection.get("revision") != self.reference.revision
        ):
            raise ValueError("selection revision differs from registered candidate")
        if canonical_payload_json(selection.get("parameter_values")) != canonical_payload_json(
            resolved
        ):
            raise ValueError("selection parameters differ from registered candidate")
        canonical_object(self.data_provenance_json)
        if not isinstance(self.metric_definitions, tuple) or not all(
            isinstance(item, MetricDefinition) for item in self.metric_definitions
        ):
            raise ValueError("metric definitions must be an immutable typed tuple")
        ids = [item.metric_id for item in self.metric_definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("metric definitions must have unique ids")
        payload = asdict(self)
        payload.pop("candidate_hash")
        expected_hash = canonical_json_hash(payload)
        if self.candidate_hash not in ("", expected_hash):
            raise ValueError("candidate binding hash differs from content")
        object.__setattr__(self, "candidate_hash", expected_hash)


class CriterionComparison(StrEnum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


@dataclass(frozen=True)
class HoldoutCriterion:
    metric_id: str
    scope: MetricScope
    unit: MetricUnit
    comparison: CriterionComparison
    threshold: float
    minimum_samples: int

    def __post_init__(self) -> None:
        _nonblank(self.metric_id, "metric_id")
        if (
            not isinstance(self.scope, MetricScope)
            or not isinstance(self.unit, MetricUnit)
            or not isinstance(self.comparison, CriterionComparison)
        ):
            raise ValueError("criterion scope/unit/comparison must be typed enums")
        if (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, (float, int))
            or not math.isfinite(self.threshold)
        ):
            raise ValueError("criterion threshold must be finite numeric")
        object.__setattr__(self, "threshold", float(self.threshold))
        if type(self.minimum_samples) is not int or self.minimum_samples < 1:
            raise ValueError("criterion minimum_samples must be a positive integer")


@dataclass(frozen=True)
class HoldoutRegistration:
    registration_id: str
    registered_at: datetime
    candidate: FrozenHoldoutCandidate
    environment: RunEnvironment
    criteria: tuple[HoldoutCriterion, ...]
    criteria_schema_version: str = "holdout-criteria-v1"
    namespace: str = SIMULATION_NAMESPACE
    window_id: str = SIMULATION_WINDOW
    registration_hash: str = ""

    def __post_init__(self) -> None:
        if str(UUID(self.registration_id)) != self.registration_id:
            raise ValueError("registration UUID must use canonical spelling")
        _aware(self.registered_at)
        if self.namespace != SIMULATION_NAMESPACE or self.window_id != SIMULATION_WINDOW:
            raise ValueError("only the fixed simulation namespace/window is supported")
        if not isinstance(self.candidate, FrozenHoldoutCandidate) or not isinstance(
            self.environment, RunEnvironment
        ):
            raise ValueError("registration requires typed frozen candidate/environment")
        _require_frozen_value(self.environment)
        if (self.environment.start, self.environment.end) != (SEALED_START, SEALED_END):
            raise ValueError("simulation environment must identify the fixed sealed window")
        if self.criteria_schema_version != "holdout-criteria-v1":
            raise ValueError("unsupported criteria schema version")
        if (
            not isinstance(self.criteria, tuple)
            or not self.criteria
            or not all(isinstance(item, HoldoutCriterion) for item in self.criteria)
        ):
            raise ValueError("registration requires immutable nonempty criteria")
        keys = [(item.metric_id, item.scope) for item in self.criteria]
        if len(keys) != len(set(keys)):
            raise ValueError("criteria must be unique by metric and scope")
        definitions = {item.metric_id: item for item in self.candidate.metric_definitions}
        for criterion in self.criteria:
            definition = definitions.get(criterion.metric_id)
            if definition is None or definition.unit is not criterion.unit:
                raise ValueError(
                    "criterion is not defined with its registered unit — "
                    f"metric={criterion.metric_id}"
                )
        payload = asdict(self)
        payload.pop("registration_hash")
        payload["environment_hash"] = environment_hash(self.environment)
        expected = canonical_json_hash(payload)
        if self.registration_hash not in ("", expected):
            raise ValueError("registration content differs from binding hash")
        object.__setattr__(self, "registration_hash", expected)


@dataclass(frozen=True)
class HoldoutSimulationObservation:
    metrics: tuple[MetricValue, ...]
    metric_definitions: tuple[MetricDefinition, ...]
    metric_registry_version: str
    evaluator_version: str
    data_snapshot_id: str
    namespace: str = SIMULATION_NAMESPACE

    def __post_init__(self) -> None:
        if self.namespace != SIMULATION_NAMESPACE:
            raise ValueError("simulation observation cannot claim real authority")
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(item, MetricValue) for item in self.metrics
        ):
            raise ValueError("simulation metrics must be an immutable typed tuple")
        if any(
            item.unavailable_reason is not None
            and not isinstance(item.unavailable_reason, MetricUnavailableReason)
            for item in self.metrics
        ):
            raise ValueError("simulation metric unavailable reason must be typed")
        if not isinstance(self.metric_definitions, tuple) or not all(
            isinstance(item, MetricDefinition) for item in self.metric_definitions
        ):
            raise ValueError("simulation metric definitions must be immutable and typed")


class SimulationVerdict(StrEnum):
    SIMULATION_MET = "simulation_met"
    SIMULATION_NOT_MET = "simulation_not_met"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CriterionOutcome:
    criterion: HoldoutCriterion
    value: float | None
    sample_count: int | None
    verdict: SimulationVerdict
    reason: str | None
    metric_unavailable_reason: MetricUnavailableReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, SimulationVerdict):
            raise ValueError("criterion verdict must be typed")
        if self.metric_unavailable_reason is not None and (
            not isinstance(self.metric_unavailable_reason, MetricUnavailableReason)
            or self.verdict is not SimulationVerdict.UNAVAILABLE
        ):
            raise ValueError("metric unavailable reason cannot coexist with an observed verdict")
        if self.verdict is SimulationVerdict.UNAVAILABLE:
            if self.reason is None:
                raise ValueError("unavailable criterion requires a reason")
        else:
            if self.reason is not None or self.value is None or not math.isfinite(self.value):
                raise ValueError(
                    "observed criterion requires a finite value without unavailable reason"
                )
            met = (
                self.value >= self.criterion.threshold
                if self.criterion.comparison is CriterionComparison.AT_LEAST
                else self.value <= self.criterion.threshold
            )
            expected = (
                SimulationVerdict.SIMULATION_MET if met else SimulationVerdict.SIMULATION_NOT_MET
            )
            if self.verdict is not expected:
                raise ValueError("criterion verdict differs from the registered comparison")


@dataclass(frozen=True)
class HoldoutSimulationResult:
    registration_hash: str
    verdict: SimulationVerdict
    outcomes: tuple[CriterionOutcome, ...]
    namespace: str = SIMULATION_NAMESPACE

    def __post_init__(self) -> None:
        if (
            self.namespace != SIMULATION_NAMESPACE
            or not isinstance(self.outcomes, tuple)
            or not self.outcomes
        ):
            raise ValueError("simulation result requires immutable nonempty outcomes")
        if not all(isinstance(item, CriterionOutcome) for item in self.outcomes):
            raise ValueError("simulation result requires typed outcomes")
        verdicts = {item.verdict for item in self.outcomes}
        expected = (
            SimulationVerdict.UNAVAILABLE
            if SimulationVerdict.UNAVAILABLE in verdicts
            else SimulationVerdict.SIMULATION_NOT_MET
            if SimulationVerdict.SIMULATION_NOT_MET in verdicts
            else SimulationVerdict.SIMULATION_MET
        )
        if self.verdict is not expected:
            raise ValueError("overall simulation verdict differs from required criteria")


def evaluate_holdout_simulation(
    registration: HoldoutRegistration, observation: HoldoutSimulationObservation
) -> HoldoutSimulationResult:
    """등록한 필수 기준 전부를 비교한다. 부재가 있으면 미충족보다 우선한다."""
    candidate = registration.candidate
    provenance_matches = (
        observation.metric_registry_version == candidate.metric_registry_version
        and observation.evaluator_version == candidate.evaluator_version
        and observation.data_snapshot_id == candidate.data_snapshot_id
    )
    pinned_definitions = {item.metric_id: item for item in candidate.metric_definitions}
    outcomes: list[CriterionOutcome] = []
    for criterion in registration.criteria:
        values = [
            item
            for item in observation.metrics
            if (item.metric_id, item.scope) == (criterion.metric_id, criterion.scope)
        ]
        definitions = [
            item for item in observation.metric_definitions if item.metric_id == criterion.metric_id
        ]
        metric = values[0] if len(values) == 1 else None
        reason = None
        if not provenance_matches:
            reason = "provenance_mismatch"
        elif len(values) != 1:
            reason = "missing_or_duplicate_metric"
        elif len(definitions) != 1 or definitions[0].unit is not criterion.unit:
            reason = "unit_mismatch"
        elif definitions[0] != pinned_definitions[criterion.metric_id]:
            reason = "definition_mismatch"
        elif metric is not None and (metric.value is None or metric.unavailable_reason is not None):
            reason = "metric_unavailable"
        elif metric is not None and (
            isinstance(metric.value, bool)
            or not isinstance(metric.value, (float, int))
            or not math.isfinite(metric.value)
        ):
            reason = "nonfinite_or_untyped_metric"
        elif metric is not None and (
            type(metric.sample_count) is not int or metric.sample_count < criterion.minimum_samples
        ):
            reason = "insufficient_samples"
        value = None if metric is None else metric.value
        verdict = SimulationVerdict.UNAVAILABLE
        if reason is None and value is not None:
            met = (
                value >= criterion.threshold
                if criterion.comparison is CriterionComparison.AT_LEAST
                else value <= criterion.threshold
            )
            verdict = (
                SimulationVerdict.SIMULATION_MET if met else SimulationVerdict.SIMULATION_NOT_MET
            )
        outcomes.append(
            CriterionOutcome(
                criterion,
                value
                if isinstance(value, (float, int))
                and not isinstance(value, bool)
                and math.isfinite(value)
                else None,
                None
                if metric is None or type(metric.sample_count) is not int
                else metric.sample_count,
                verdict,
                reason,
                None if metric is None else metric.unavailable_reason,
            )
        )
    verdicts = {item.verdict for item in outcomes}
    overall = (
        SimulationVerdict.UNAVAILABLE
        if SimulationVerdict.UNAVAILABLE in verdicts
        else SimulationVerdict.SIMULATION_NOT_MET
        if SimulationVerdict.SIMULATION_NOT_MET in verdicts
        else SimulationVerdict.SIMULATION_MET
    )
    return HoldoutSimulationResult(registration.registration_hash, overall, tuple(outcomes))


class SimulationClaimStatus(StrEnum):
    CLAIMED = "claimed"
    OBSERVED = "observed"
    INTERRUPTED_UNKNOWN = "interrupted_unknown"
    STORAGE_UNKNOWN = "storage_unknown"


@dataclass(frozen=True)
class HoldoutSimulationClaim:
    request_id: str
    registration_id: str
    registration_hash: str
    claimed_at: datetime
    status: SimulationClaimStatus = SimulationClaimStatus.CLAIMED
    completed_at: datetime | None = None
    result: HoldoutSimulationResult | None = None
    namespace: str = SIMULATION_NAMESPACE
    window_id: str = SIMULATION_WINDOW

    def __post_init__(self) -> None:
        if (
            str(UUID(self.request_id)) != self.request_id
            or str(UUID(self.registration_id)) != self.registration_id
        ):
            raise ValueError("claim UUIDs must use canonical spelling")
        if not isinstance(self.status, SimulationClaimStatus):
            raise ValueError("claim status must be typed")
        _aware(self.claimed_at)
        if self.namespace != SIMULATION_NAMESPACE or self.window_id != SIMULATION_WINDOW:
            raise ValueError("claim has no real authority")
        if self.completed_at is not None:
            _aware(self.completed_at)
        if self.status is SimulationClaimStatus.CLAIMED and (
            self.result is not None or self.completed_at is not None
        ):
            raise ValueError("pending claim cannot carry a result")
        if self.status is SimulationClaimStatus.OBSERVED and (
            self.result is None or self.completed_at is None
        ):
            raise ValueError("observed claim requires result and completion time")
        if (
            self.status not in (SimulationClaimStatus.OBSERVED, SimulationClaimStatus.CLAIMED)
            and self.result is not None
        ):
            raise ValueError("unknown observation cannot carry a fabricated result")
        if self.result is not None and (
            self.result.namespace != SIMULATION_NAMESPACE
            or self.result.registration_hash != self.registration_hash
        ):
            raise ValueError("simulation result does not match claimed registration")
