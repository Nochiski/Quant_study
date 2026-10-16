"""후보를 바꾸면서도 직전 목표 비중을 하나의 fold로 유지하는 연속 일정 컴파일러."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from strategy_workbench.domain.backtest.facade.environment import ExecutionTiming, RunEnvironment
from strategy_workbench.domain.canonical.facade.encoding import canonical_json_hash
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionScheduleStatus,
    SelectionTransition,
)
from strategy_workbench.domain.factor.facade.planning import FactorExecutionPlan, factor_graph_hash
from strategy_workbench.domain.strategy.facade.document import source_hash_of
from strategy_workbench.domain.strategy.facade.specification import (
    ParameterValue,
    StrategySpec,
    resolve_parameter_values,
    strategy_spec_hash,
)

from ._compiler import (
    _canonical_payload,
    _checkpointed,
    _compile_frame,
    _noop_checkpoint,
    _noop_progress,
    _require_finite_tree,
    _sector_unknown_warnings,
    compile_rebalance_schedule,
)
from ._models import PortfolioObservation, PortfolioWarning, TargetFrame

SCHEDULED_TAPE_VERSION = "scheduled-target-tape-v1"


@dataclass(frozen=True, kw_only=True)
class ScheduledCandidate:
    """평가된 관측과 그 불변 후보 신원. 관측 내용은 후보 ID와 분리한다.

    source와 spec의 의미 일치는 저장 revision 해소자가 검증한다. 계획 해시와 관측이
    실제 평가에서 나왔는지도 호출자 책임이며 이 타입은 실행 출처 증명서가 아니다.
    여기서는 팩터 목록·그래프·평가 판본과 완전 해소한 파라미터의 일치를 검사한다.
    """

    spec: StrategySpec
    parameter_values: tuple[tuple[str, ParameterValue], ...]
    source: str
    factor_plans: tuple[tuple[str, FactorExecutionPlan], ...]
    data_snapshot_id: str
    observations: tuple[PortfolioObservation, ...]
    candidate_id: str = field(init=False)
    strategy_hash: str = field(init=False)
    source_hash: str = field(init=False)
    factor_evaluation_version: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, StrategySpec):
            raise ValueError(
                f"scheduled candidate requires StrategySpec: type={type(self.spec).__name__}"
            )
        for name in ("parameter_values", "factor_plans", "observations"):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"scheduled candidate requires an immutable tuple: field={name}")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("scheduled candidate requires nonempty source text")
        if not isinstance(self.data_snapshot_id, str) or not self.data_snapshot_id.strip():
            raise ValueError("scheduled candidate requires a nonempty data_snapshot_id")
        requested: dict[str, ParameterValue] = {}
        for item in self.parameter_values:
            if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
                raise ValueError(f"invalid scheduled parameter entry: entry={item!r}")
            key, value = item
            if key in requested:
                raise ValueError(f"duplicate scheduled parameter: parameter_id={key}")
            requested[key] = value
        resolved = tuple(sorted(resolve_parameter_values(self.spec.parameters, requested).items()))
        object.__setattr__(self, "parameter_values", resolved)
        plans: dict[str, FactorExecutionPlan] = {}
        for item in self.factor_plans:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], FactorExecutionPlan)
            ):
                raise ValueError(f"invalid scheduled factor plan entry: entry={item!r}")
            factor_id, plan = item
            if factor_id in plans:
                raise ValueError(f"duplicate scheduled factor plan: factor_id={factor_id}")
            plans[factor_id] = plan
        expected = {factor.factor_id for factor in self.spec.factors}
        if not expected or set(plans) != expected or len(expected) != len(self.spec.factors):
            raise ValueError(
                "scheduled factor plans differ from spec: "
                f"expected={sorted(expected)} actual={sorted(plans)}"
            )
        for factor in self.spec.factors:
            plan = plans[factor.factor_id]
            expected_hash = factor_graph_hash(factor.graph)
            if plan.graph_hash != expected_hash:
                raise ValueError(
                    f"scheduled factor graph mismatch: factor_id={factor.factor_id} "
                    f"expected={expected_hash} actual={plan.graph_hash}"
                )
            if not plan.plan_hash or not plan.evaluation_version:
                raise ValueError(
                    f"scheduled factor provenance is incomplete: factor_id={factor.factor_id}"
                )
        versions = {plan.evaluation_version for plan in plans.values()}
        if len(versions) != 1:
            raise ValueError(
                f"scheduled candidate has mixed evaluation versions: versions={sorted(versions)}"
            )
        version = next(iter(versions))
        object.__setattr__(self, "factor_plans", tuple(sorted(plans.items())))
        object.__setattr__(self, "strategy_hash", strategy_spec_hash(self.spec))
        object.__setattr__(self, "source_hash", source_hash_of(self.source))
        object.__setattr__(self, "factor_evaluation_version", version)
        object.__setattr__(
            self,
            "candidate_id",
            canonical_json_hash(
                {
                    "version": "scheduled-candidate-v1",
                    "strategy_hash": self.strategy_hash,
                    "parameter_values": self.parameter_values,
                    "source_hash": self.source_hash,
                    "factor_plan_hashes": tuple(
                        (key, plan.plan_hash) for key, plan in self.factor_plans
                    ),
                    "factor_evaluation_version": version,
                }
            ),
        )
        seen: set[tuple[date, str]] = set()
        for observation in self.observations:
            if not isinstance(observation, PortfolioObservation):
                raise ValueError(f"invalid scheduled observation: candidate_id={self.candidate_id}")
            key = (observation.as_of, observation.security_id)
            if key in seen:
                raise ValueError(
                    f"duplicate scheduled observation: candidate_id={self.candidate_id} key={key}"
                )
            seen.add(key)
            _require_finite_tree(
                observation,
                stage="scheduled_observation",
                context=f"candidate_id={self.candidate_id} key={key}",
                checkpoint=_noop_checkpoint,
            )


@dataclass(frozen=True, kw_only=True)
class ScheduledFrameSource:
    """선택 근거 창과 체결일 소속 창을 구분한다. 창이 바뀌어도 근거를 소급 교체하지 않는다."""

    selection_window_index: int
    execution_window_index: int
    candidate_id: str
    strategy_hash: str
    source_hash: str
    signal_as_of: date
    execution_on: date


@dataclass(frozen=True, kw_only=True)
class ScheduledTargetTape:
    """일반 TargetTape의 단일 전략 신원과 구분하는 혼합 후보 실행 입력."""

    schedule_hash: str
    data_snapshot_id: str
    factor_evaluation_version: str
    frames: tuple[TargetFrame, ...]
    frame_sources: tuple[ScheduledFrameSource, ...]
    tape_hash: str
    warnings: tuple[PortfolioWarning, ...]
    execution_timing: str = "next_open"
    schema_version: str = SCHEDULED_TAPE_VERSION


def compile_scheduled_target_tape(
    *,
    schedule: SelectionSchedule,
    candidates: tuple[ScheduledCandidate, ...],
    environment: RunEnvironment,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> ScheduledTargetTape:
    """전체 공통 달력의 후보별 cadence와 전환 강제 프레임을 하나의 목표 비중 fold로 컴파일한다.

    cadence와 강제 전환이 같은 신호·체결 쌍이면 정확히 한 번 계산한다. 동일 후보의 다음
    창은 강제 프레임을 추가하지 않는다. carried는 실제 보유가 아닌 직전 목표 비중이다.
    """
    checkpoint()
    if schedule.status is not SelectionScheduleStatus.READY:
        raise ValueError(f"cannot compile blocked schedule: schedule_hash={schedule.schedule_hash}")
    if environment.timing is not ExecutionTiming.NEXT_OPEN:
        raise ValueError(f"scheduled tape requires next_open: actual={environment.timing}")
    if not isinstance(candidates, tuple) or any(
        not isinstance(c, ScheduledCandidate) for c in candidates
    ):
        raise ValueError("scheduled candidates must be an immutable ScheduledCandidate tuple")
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    expected_ids = {selection.candidate_id for selection in schedule.selections}
    if len(by_id) != len(candidates) or set(by_id) != expected_ids:
        raise ValueError(
            "scheduled candidate identities differ: "
            f"expected={sorted(str(c) for c in expected_ids)} "
            f"actual={sorted(by_id)} count={len(candidates)}"
        )
    snapshots = {candidate.data_snapshot_id for candidate in candidates}
    versions = {candidate.factor_evaluation_version for candidate in candidates}
    if len(snapshots) != 1 or len(versions) != 1:
        raise ValueError(
            "scheduled candidate provenance differs: "
            f"snapshots={sorted(snapshots)} versions={sorted(versions)}"
        )
    if environment.start > schedule.sessions[0] or environment.end < schedule.sessions[-1]:
        raise ValueError("scheduled calendar is outside the run environment range")
    signal_dates = tuple(transition.signal_as_of for transition in schedule.transitions)
    observations_by_candidate: dict[str, dict[date, list[PortfolioObservation]]] = {}
    cadence_by_candidate: dict[str, set[tuple[date, date]]] = {}
    for candidate in _checkpointed(candidates, checkpoint):
        by_date: dict[date, list[PortfolioObservation]] = {}
        calendar = set(schedule.sessions)
        for observation in _checkpointed(candidate.observations, checkpoint):
            if observation.as_of not in calendar:
                raise ValueError(
                    "scheduled observation is outside the calendar: "
                    f"candidate_id={candidate.candidate_id} "
                    f"as_of={observation.as_of}"
                )
            by_date.setdefault(observation.as_of, []).append(observation)
        observations_by_candidate[candidate.candidate_id] = by_date
        cadence_by_candidate[candidate.candidate_id] = set(
            compile_rebalance_schedule(
                candidate.spec, schedule.sessions, checkpoint=checkpoint
            ).pairs
        )
        for factor_id, plan in candidate.factor_plans:
            if plan.missing_policy != environment.missing.value:
                raise ValueError(
                    "scheduled factor missing policy differs: "
                    f"candidate_id={candidate.candidate_id} "
                    f"factor_id={factor_id} expected={environment.missing.value} "
                    f"actual={plan.missing_policy}"
                )
    frames: list[TargetFrame] = []
    sources: list[ScheduledFrameSource] = []
    warnings: list[PortfolioWarning] = []
    carried: dict[str, float] | None = None
    for index in _checkpointed(range(len(schedule.sessions) - 1), checkpoint):
        signal_as_of, execution_on = schedule.sessions[index : index + 2]
        selection_index = bisect_right(signal_dates, signal_as_of) - 1
        if selection_index < 0:
            continue
        transition: SelectionTransition = schedule.transitions[selection_index]
        candidate = by_id[transition.candidate_id]
        forced = transition.candidate_changed and signal_as_of == transition.signal_as_of
        if (
            not forced
            and (signal_as_of, execution_on) not in cadence_by_candidate[candidate.candidate_id]
        ):
            continue
        if schedule.candidate_at(execution_on) != candidate.candidate_id:
            raise ValueError(
                f"scheduled signal and execution candidate disagree: signal_as_of={signal_as_of} "
                f"execution_on={execution_on} candidate_id={candidate.candidate_id}"
            )
        frame_observations = tuple(
            observations_by_candidate[candidate.candidate_id].get(signal_as_of, ())
        )
        previous_weights = (
            {item.security_id: item.previous_weight for item in frame_observations}
            if carried is None
            else carried
        )
        compiled = _compile_frame(
            candidate.spec,
            signal_as_of,
            execution_on,
            frame_observations,
            previous_weights,
            checkpoint=checkpoint,
        )
        frame = compiled.frame
        _require_finite_tree(
            frame,
            stage="scheduled_frame",
            context=f"signal_as_of={signal_as_of}",
            checkpoint=checkpoint,
        )
        frames.append(frame)
        execution_window = next(
            item.window_index
            for item in schedule.selections
            if item.window.test_start <= execution_on <= item.window.test_end
        )
        sources.append(
            ScheduledFrameSource(
                selection_window_index=transition.window_index,
                execution_window_index=execution_window,
                candidate_id=candidate.candidate_id,
                strategy_hash=candidate.strategy_hash,
                source_hash=candidate.source_hash,
                signal_as_of=signal_as_of,
                execution_on=execution_on,
            )
        )
        # 같은 노드의 기존 경고 정책을 그대로 사용한다. 후보별 설정이 다를 수 있어 프레임별로 낸다.
        warnings.extend(
            _sector_unknown_warnings(
                candidate.spec,
                [(signal_as_of, len(compiled.unknown_sector_ids))]
                if compiled.unknown_sector_ids
                else [],
                1,
            )
        )
        carried = {target.security_id: target.weight for target in frame.targets}
        progress(0.9 * (index + 1) / max(len(schedule.sessions) - 1, 1))
    snapshot = next(iter(snapshots))
    version = next(iter(versions))
    payload = {
        "schema_version": SCHEDULED_TAPE_VERSION,
        "schedule_hash": schedule.schedule_hash,
        "data_snapshot_id": snapshot,
        "factor_evaluation_version": version,
        "execution_timing": environment.timing.value,
        "candidates": sorted(by_id),
        "frames": _canonical_payload(tuple(frames), checkpoint=checkpoint),
        "frame_sources": _canonical_payload(tuple(sources), checkpoint=checkpoint),
    }
    result = ScheduledTargetTape(
        schedule_hash=schedule.schedule_hash,
        data_snapshot_id=snapshot,
        factor_evaluation_version=version,
        frames=tuple(frames),
        frame_sources=tuple(sources),
        tape_hash=canonical_json_hash(payload),
        warnings=tuple(warnings),
    )
    progress(1.0)
    return result
