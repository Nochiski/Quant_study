"""완료된 실행의 초기자본 기준 계좌 결과를 읽는 순수 투영."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Literal

from strategy_workbench.domain.strategy.facade.provenance import StrategyProvenance

from ._models import BacktestRunResult, DataWarning


class AccountReportStatus(StrEnum):
    AVAILABLE = "available"
    EMPTY_SNAPSHOTS = "empty_snapshots"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class AccountReportSummary:
    initial_cash: float
    first_snapshot_date: date
    last_snapshot_date: date
    final_cash: float
    final_equity: float
    pnl: float
    total_return: float
    snapshot_count: int

    def __post_init__(self) -> None:
        amounts = (
            self.initial_cash,
            self.final_cash,
            self.final_equity,
            self.pnl,
            self.total_return,
        )
        if (
            not all(isfinite(value) for value in amounts)
            or self.initial_cash <= 0
            or self.snapshot_count < 1
            or self.first_snapshot_date > self.last_snapshot_date
            or (self.snapshot_count == 1 and self.first_snapshot_date != self.last_snapshot_date)
            or (self.snapshot_count > 1 and self.first_snapshot_date == self.last_snapshot_date)
        ):
            raise ValueError(
                "계좌 요약의 유한 금액·양수 초기자본·세션 범위 계약을 위반했습니다: "
                f"amounts={amounts!r} snapshot_count={self.snapshot_count} "
                f"first_snapshot_date={self.first_snapshot_date} "
                f"last_snapshot_date={self.last_snapshot_date}"
            )


@dataclass(frozen=True)
class AccountReportProvenance:
    run_fingerprint: str
    engine_version: str
    strategy_hash: str
    data_snapshot_id: str
    target_tape_hash: str
    metric_registry_version: str
    environment_hash: str
    strategy_provenance: StrategyProvenance
    schema_version: str
    factor_evaluation_version: str | None


@dataclass(frozen=True)
class AccountReport:
    run_id: str
    status: AccountReportStatus
    summary: AccountReportSummary | None
    detail: str | None
    provenance: AccountReportProvenance
    warnings: tuple[DataWarning, ...]
    evaluation_basis: Literal["initial_capital"] = "initial_capital"
    interpretation: str = (
        "외부 입출금 없는 단일 실행의 초기자본 대비 비용 후 계좌 결과입니다. "
        "초기자본에는 기준 날짜를 붙이지 않으며, 수익률은 최종자산 / 초기자본 − 1입니다. "
        "비용은 최종자산에 이미 반영되어 다시 차감하지 않습니다. "
        "기존 전체 구간(FULL) 수익률은 첫 계좌 기록을 기준으로 하므로 "
        "이 수익률과 다를 수 있습니다. "
        "최종자산은 보유 평가를 포함하며 청산 현금이나 정지·상폐 정산 완료를 뜻하지 않습니다. "
    )

    def __post_init__(self) -> None:
        available = self.status is AccountReportStatus.AVAILABLE
        if (
            not isinstance(self.status, AccountReportStatus)
            or (available != (self.summary is not None))
            or (available and self.detail is not None)
            or (not available and not self.detail)
            or self.evaluation_basis != "initial_capital"
        ):
            raise ValueError(
                "계좌 보고서의 상태·요약·사유 계약을 위반했습니다: "
                f"run_id={self.run_id} status={self.status!r} "
                f"has_summary={self.summary is not None} detail={self.detail!r} "
                f"evaluation_basis={self.evaluation_basis!r}"
            )


def project_account_report(result: BacktestRunResult) -> AccountReport:
    """비용을 재차감하지 않으며, 원본 출처·경고의 검증 결과를 갱신하지 않는다."""
    manifest = result.manifest
    provenance = AccountReportProvenance(
        run_fingerprint=manifest.run_fingerprint,
        engine_version=manifest.engine_version,
        strategy_hash=manifest.strategy_hash,
        data_snapshot_id=manifest.data_snapshot_id,
        target_tape_hash=manifest.target_tape_hash,
        metric_registry_version=manifest.metric_registry_version,
        environment_hash=manifest.environment_hash,
        strategy_provenance=manifest.strategy_provenance,
        schema_version=manifest.schema_version,
        factor_evaluation_version=manifest.factor_evaluation_version,
    )

    def unavailable(status: AccountReportStatus, detail: str) -> AccountReport:
        return AccountReport(
            run_id=manifest.run_id,
            status=status,
            summary=None,
            detail=f"{detail} run_id={manifest.run_id}",
            provenance=provenance,
            warnings=manifest.warnings,
        )

    initial_cash = manifest.run_spec.initial_cash
    if not isfinite(initial_cash) or initial_cash <= 0:
        return unavailable(
            AccountReportStatus.INVALID_INPUT,
            "초기자본은 유한한 양수여야 합니다: "
            f"initial_cash={initial_cash!r} expected=finite_positive",
        )
    snapshots = result.artifacts.snapshots
    if not snapshots:
        return unavailable(
            AccountReportStatus.EMPTY_SNAPSHOTS,
            "계좌 snapshot이 없어 최종 계좌 결과를 계산할 수 없습니다: snapshot_count=0",
        )
    previous_session: date | None = None
    for index, snapshot in enumerate(snapshots):
        if previous_session is not None and snapshot.session <= previous_session:
            return unavailable(
                AccountReportStatus.INVALID_INPUT,
                "계좌 snapshot 세션은 중복 없이 오름차순이어야 합니다: "
                f"index={index} previous_session={previous_session} session={snapshot.session}",
            )
        for field, amount in (("cash", snapshot.cash), ("equity", snapshot.equity)):
            if not isfinite(amount):
                return unavailable(
                    AccountReportStatus.INVALID_INPUT,
                    "계좌 snapshot 금액은 유한해야 합니다: "
                    f"index={index} session={snapshot.session} field={field} "
                    f"value={amount!r} expected=finite",
                )
        previous_session = snapshot.session
    final = snapshots[-1]
    pnl = final.equity - initial_cash
    total_return = final.equity / initial_cash - 1
    if not isfinite(pnl) or not isfinite(total_return):
        return unavailable(
            AccountReportStatus.INVALID_INPUT,
            "초기자본 기준 손익·수익률 계산 결과가 유한하지 않습니다: "
            f"initial_cash={initial_cash!r} final_equity={final.equity!r} "
            f"pnl={pnl!r} total_return={total_return!r} expected=finite",
        )
    return AccountReport(
        run_id=manifest.run_id,
        status=AccountReportStatus.AVAILABLE,
        summary=AccountReportSummary(
            initial_cash=initial_cash,
            first_snapshot_date=snapshots[0].session,
            last_snapshot_date=final.session,
            final_cash=final.cash,
            final_equity=final.equity,
            pnl=pnl,
            total_return=total_return,
            snapshot_count=len(snapshots),
        ),
        detail=None,
        provenance=provenance,
        warnings=manifest.warnings,
    )
