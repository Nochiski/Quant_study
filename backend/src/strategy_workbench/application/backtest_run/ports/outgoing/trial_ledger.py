"""시도 원장·봉인 원장 차단 기록 포트 (검증 랩 spec D2·D11, V1-05).

실행이 어느 계열의 어느 시도인지는 접수 때 run 기록과 한 트랜잭션으로 적는다
(`BacktestRunRepositoryPort.add`). 이 포트는 그 뒤의 기록(대표 샤프·차단한 시도·계열 합치기)과 계열
단위 조회를 맡는다. 시도로 묶고 N 을 세는 규칙은 domain(`summarize_trial_ledger`)이 소유한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.backtest.facade.trials import BlockedTrialAttempt, TrialLedgerEntry


class TrialLineageAlreadyMergedError(ValueError):
    """두 계열이 이미 같은 계열이다(자기 자신과 합치기 포함)."""


@dataclass(frozen=True)
class TrialLedgerRecords:
    """계열 하나에 속한 원장 행. `lineage_id` 는 합친 뒤 남은 계열, `entries` 는 접수 순이다."""

    lineage_id: str
    merged_lineage_ids: tuple[str, ...]
    entries: tuple[TrialLedgerEntry, ...]
    blocked: tuple[BlockedTrialAttempt, ...]


class TrialLedgerPort(Protocol):
    def record_trial_result(
        self, run_id: str, *, session_sharpe: float | None, metric_registry_version: str
    ) -> None:
        """완료된 run 의 대표 샤프 후보(세션 단위)와 지표 레지스트리 판본을 적는다."""
        ...

    def record_blocked_attempt(self, attempt: BlockedTrialAttempt) -> None:
        """봉인 겹침으로 거절한 실행 요청을 봉인 원장에 적는다. 이 기록이 정본이다."""
        ...

    def trial_ledger(self, lineage_id: str) -> TrialLedgerRecords:
        """계열(합쳐진 계열 포함)의 원장 행. 합쳐져 사라진 계열을 물으면 남은 계열의 행이다."""
        ...

    def merge_lineages(self, source_id: str, target_id: str, *, merged_at: datetime) -> None:
        """`source_id` 계열을 `target_id` 계열에 합친다. 되돌릴 수 없다.

        Raises:
            TrialLineageAlreadyMergedError: 두 계열이 이미 같은 계열일 때.
        """
        ...
