from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRevisionConflictError,
)
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity


def _saved_spec():
    repository = InMemoryStrategyRepository()
    service = StrategyDesignService(
        repository,
        new_id=lambda: "strategy-1",
        today=lambda: date(2026, 9, 3),
    )
    return repository, service.create(service.template()).spec


def test_repository_keeps_immutable_revisions() -> None:
    repository, first = _saved_spec()
    second = replace(
        first,
        identity=StrategyIdentity("strategy-1", 2),
        title="수정 전략",
    )
    repository.append(second, expected_revision=1)

    assert repository.get("strategy-1", 1).title == "새 팩터 전략"
    assert repository.get("strategy-1").title == "수정 전략"


def test_repository_rejects_stale_revision() -> None:
    repository, first = _saved_spec()
    stale = replace(first, identity=StrategyIdentity("strategy-1", 2))

    with pytest.raises(StrategyRevisionConflictError, match="expected=0 actual=1"):
        repository.append(stale, expected_revision=0)


def test_repository_reports_missing_strategy() -> None:
    with pytest.raises(StrategyNotFoundError, match="strategy not found"):
        InMemoryStrategyRepository().get("missing")
