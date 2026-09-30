"""run 기록 저장소 포트 (검증 랩 spec D3).

run 의 목록·상태·요청은 이 저장소가 정본이다. 서비스는 상태가 바뀔 때(접수·시작·취소 요청·종결)만
쓰고, 진행률 이벤트는 쓰지 않는다 — 진행 중 run 의 진행률은 서비스 메모리가 가진다. 같은 파일의 시도
원장(`TrialLedgerPort`)도 이 저장소가 구현한다.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.application.strategy_design.facade.ports import Page, PageRequest
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    BacktestRunState,
    StrategyProvenance,
)

from .trial_ledger import TrialLedgerPort


class BacktestRunNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class BacktestRunSummary:
    """One accepted run and the strategy meaning resolved before it started."""

    run: BacktestRunState
    strategy_provenance: StrategyProvenance


class BacktestRunRepositoryPort(TrialLedgerPort, Protocol):
    def add(
        self,
        summary: BacktestRunSummary,
        request: BacktestRunSpec,
        *,
        lineage_id: str | None,
        trial_key: str,
    ) -> None:
        """접수한 run 을 접수 순서의 끝에 더한다. `request` 는 다시 제출할 수 있는 원본이다.

        run 이 속한 계열(없으면 None)과 시도 키를 같은 트랜잭션으로 원장에 적는다 — 원장에 없는
        run 이 생기면 N 이 조용히 줄어든다.
        """
        ...

    def update(self, state: BacktestRunState) -> None:
        """이미 있는 run 의 상태를 바꾼다. 없으면 `BacktestRunNotFoundError`."""
        ...

    def get(self, run_id: str) -> BacktestRunSummary: ...

    def request(self, run_id: str) -> BacktestRunSpec: ...

    def list(
        self, page: PageRequest, *, strategy_id: str | None = None
    ) -> Page[BacktestRunSummary]:
        """최근 접수 순. `strategy_id` 는 저장 리비전 provenance 로 거른다."""
        ...

    def unfinished(self) -> tuple[BacktestRunState, ...]:
        """종결(`completed`·`failed`·`cancelled`)되지 않은 run 의 상태."""
        ...

    def states(self, run_ids: Collection[str]) -> dict[str, BacktestRunState]:
        """저장된 run 들의 마지막 상태(한 번에 읽는다). 없는 run 은 빠진다."""
        ...
