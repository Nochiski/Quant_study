from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    require_environment,
    require_research_window,
)
from strategy_workbench.domain.equity.facade.research_data import SNAPSHOT_CONTRACT_SEPARATOR
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    PriceLabelSpec,
    SignalEvaluationResult,
    evaluate_signal,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec, strategy_spec_hash

from .ports.outgoing.research_inputs import (
    LabelDataQuery,
    LabelDataStatus,
    PreparedSignals,
    SignalLabelData,
    SignalLabelDataPort,
    SignalPipelinePort,
    SnapshotGuardPort,
    SnapshotProof,
)


@dataclass(frozen=True)
class SignalResearchRequest:
    strategy: StrategySpec
    environment: RunEnvironment
    factor_ids: tuple[str, ...]
    label_spec: PriceLabelSpec
    evaluation_as_of: date
    label_calendar_through: date


@dataclass(frozen=True)
class SignalResearchReport:
    schema_version: str
    input_proof: SnapshotProof
    strategy_hash: str
    request: SignalResearchRequest
    signals: PreparedSignals
    label_data: SignalLabelData
    evaluations: tuple[SignalEvaluationResult, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class SignalResearchOutcome:
    status: LabelDataStatus
    report: SignalResearchReport | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LabelDataStatus):
            raise ValueError(f"unknown research outcome status — status={self.status!r}")
        if self.status is LabelDataStatus.READY:
            if self.report is None or self.detail is not None:
                raise ValueError("ready research outcome requires report and no failure reason")
        elif self.report is not None or not self.detail or not self.detail.strip():
            raise ValueError("failed research outcome requires a reason and no report")


class SignalResearchService:
    def __init__(
        self, guard: SnapshotGuardPort, pipeline: SignalPipelinePort, labels: SignalLabelDataPort
    ) -> None:
        self._guard = guard
        self._pipeline = pipeline
        self._labels = labels

    def run(self, request: SignalResearchRequest) -> SignalResearchOutcome:
        """신호 계획을 먼저 고정하고 별도 라벨을 읽은 뒤 검증된 입력의 평가 증거를 반환한다."""
        environment = require_environment(request.environment, requested_by="signal_research")
        require_research_window(request.evaluation_as_of, requested_by="signal_research")
        if not environment.start <= environment.end <= request.evaluation_as_of:
            raise ValueError(
                "signal window must end on or before evaluation_as_of — "
                f"start={environment.start} end={environment.end} "
                f"evaluation_as_of={request.evaluation_as_of}"
            )
        if request.label_calendar_through < request.evaluation_as_of:
            raise ValueError(
                "label_calendar_through must include evaluation_as_of — "
                f"label_calendar_through={request.label_calendar_through} "
                f"evaluation_as_of={request.evaluation_as_of}"
            )
        if not request.factor_ids or len(set(request.factor_ids)) != len(request.factor_ids):
            raise ValueError(
                f"factor_ids must be nonempty and unique — factor_ids={request.factor_ids!r}"
            )
        known = {factor.factor_id for factor in request.strategy.factors}
        if not set(request.factor_ids) <= known:
            raise ValueError(
                "factor ids must belong to the selected strategy — "
                f"unknown={sorted(set(request.factor_ids) - known)} expected={sorted(known)}"
            )
        proof = self._guard.verify()
        signals = self._pipeline.prepare(request.strategy, environment)
        _match_preserved_snapshot(proof, signals.data_snapshot_id)
        if signals.target_tape.data_snapshot_id != signals.data_snapshot_id:
            raise ValueError(
                "signal tape and pipeline snapshots differ — "
                f"expected={signals.data_snapshot_id!r} "
                f"actual={signals.target_tape.data_snapshot_id!r}"
            )
        signal_rows = tuple(
            row for row in signals.observations if environment.start <= row.as_of <= environment.end
        )
        if not signal_rows:
            raise ValueError(
                "signal pipeline returned no observations in the requested window — "
                f"start={environment.start} end={environment.end} "
                f"loaded_rows={len(signals.observations)}"
            )
        security_ids = tuple(
            sorted({row.security_id for row in signal_rows if row.universe_member})
        )
        if not security_ids:
            self._verify_unchanged(proof)
            return SignalResearchOutcome(
                LabelDataStatus.UNAVAILABLE,
                detail=(
                    "신호 기간에 당시 유니버스 구성 종목이 없어 비교할 수 없습니다 — "
                    f"start={environment.start} end={environment.end}"
                ),
            )
        loaded = self._labels.load(
            LabelDataQuery(
                environment.start,
                request.evaluation_as_of,
                request.label_calendar_through,
                security_ids,
            )
        )
        if loaded.status is not LabelDataStatus.READY:
            self._verify_unchanged(proof)
            return SignalResearchOutcome(loaded.status, detail=loaded.detail)
        data = loaded.data
        if data is None:
            raise ValueError(f"ready label data must carry a dataset — status={loaded.status!r}")
        _match_preserved_snapshot(proof, data.data_snapshot_id)
        if data.data_snapshot_id != signals.data_snapshot_id:
            raise ValueError(
                "signal and label snapshots differ — "
                f"expected={signals.data_snapshot_id!r} actual={data.data_snapshot_id!r}"
            )
        results = tuple(
            evaluate_signal(
                observations=signal_rows,
                factor_id=factor_id,
                signal_snapshot_id=signals.data_snapshot_id,
                price_snapshot_id=data.data_snapshot_id,
                sessions=data.sessions,
                prices=data.prices,
                listings=data.listings,
                adjustment_gaps=data.adjustment_gaps,
                label_spec=request.label_spec,
                evaluation_as_of=request.evaluation_as_of,
            )
            for factor_id in request.factor_ids
        )
        self._verify_unchanged(proof)
        report = SignalResearchReport(
            "signal-research-v1",
            proof,
            strategy_spec_hash(request.strategy),
            request,
            signals,
            data,
            results,
            (
                "가격 라벨은 분할 반영 종가 기준이며 현금배당·거래비용을 제외합니다.",
                "순위 상관과 비교 가능 비율은 실제 다음 시가 매매의 비용 후 계좌 수익이 아닙니다.",
                "상폐 회수금과 조정 공백을 임의 가격으로 채우지 않습니다.",
                "조정 공백을 지나는 라벨은 adjustment_gap으로 남기고 원래 모집단에 포함합니다.",
                "여러 기간의 결과는 탐색 증거이며 "
                "최선 기간 선택이나 통계적 유의성을 인증하지 않습니다.",
            ),
        )
        return SignalResearchOutcome(LabelDataStatus.READY, report)

    def _verify_unchanged(self, before: SnapshotProof) -> None:
        actual = self._guard.verify()
        if actual != before:
            raise ValueError(
                "preserved research input changed during evaluation — "
                f"expected={before!r} actual={actual!r}"
            )


def _match_preserved_snapshot(proof: SnapshotProof, snapshot_id: str) -> None:
    if not snapshot_id.startswith(proof.ledger_snapshot_id + SNAPSHOT_CONTRACT_SEPARATOR):
        raise ValueError(
            "adapter snapshot does not belong to the verified preserved ledger — "
            f"expected_ledger={proof.ledger_snapshot_id!r} actual={snapshot_id!r}"
        )
