"""Outgoing port: 끝난 백테스트 실행의 결과를 읽는다 (결과 설명 spec R2).

구현은 bootstrap이 `BacktestRunService.result`를 감싸 주입한다. `StrategyCompilerPort`와 같은
방식이라 `assistant_chat`이 `backtest_run` 유스케이스에 의존하지 않는다. 포트가 돌려주는 값은
domain 타입(`BacktestRunResult`)이고, 모델에게 무엇을 얼마나 보일지는 application이 정한다.

검증 근거(US-DM-12)도 같은 포트로 읽는다. 조회 시점 계열 원장의 DSR 근거와, 이 실행의 trial 을
후보로 고를 때 굳힌 스냅숏을 시점을 나눠 싣는다 — 두 근거를 섞으면 지금 N 으로 그때의 판단을
설명하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.analytics.facade.metrics import MetricValue
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult
from strategy_workbench.domain.experiment.facade.statistics import DeflatedSharpeEvidence

__all__ = ["BacktestResultPort", "ResultSelectionSnapshot", "ResultValidationFacts"]


@dataclass(frozen=True)
class ResultSelectionSnapshot:
    """이 실행의 trial 을 후보로 고를 때 기록한 계열 스냅숏(검증 랩 spec D9).

    옛 기록은 칸이 비어 있다.
    """

    experiment_id: str
    selected_at: datetime
    trial_count: int | None
    deflated_sharpe: float | None
    # 고른 trial 이 속한 시도의 원장 대표 샤프(세션 단위). DSR 의 분자가 아닐 수 있다.
    ledger_representative_sharpe: float | None


@dataclass(frozen=True)
class ResultValidationFacts:
    """결과 검증 카드와 같은 근거. `observed_at` 은 원장을 읽은 시각이지 완료·선택 시각이 아니다."""

    observed_at: datetime
    # 검증 카드와 같은 저장된 FULL PSR(없으면 미기록). 고르는 규칙은 검증 조회가 소유한다.
    psr: MetricValue | None
    deflated_sharpe: DeflatedSharpeEvidence
    selections: tuple[ResultSelectionSnapshot, ...]


class BacktestResultPort(Protocol):
    def completed_result(self, run_id: str) -> BacktestRunResult | None:
        """완료된 실행의 결과. 모르는 실행이거나 아직 끝나지 않았거나 실패했거나 결과 파일을 못
        읽으면 None.

        None 하나로 묶는 이유는 어시스턴트 쪽 대응이 같기 때문이다 — 모두 설명할 결과가 없다.
        """
        ...

    def validation_facts(self, run_id: str) -> ResultValidationFacts | None:
        """완료된 실행의 검증 근거. 결과를 못 읽으면 None — 결과 없이 근거만 설명하지 않는다."""
        ...
