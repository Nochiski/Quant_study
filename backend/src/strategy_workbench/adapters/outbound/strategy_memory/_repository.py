from __future__ import annotations

from threading import RLock

from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRevisionConflictError,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


class InMemoryStrategyRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[StrategySpec]] = {}
        self._lock = RLock()

    def add(self, spec: StrategySpec) -> None:
        with self._lock:
            strategy_id = spec.identity.strategy_id
            if strategy_id in self._items:
                raise StrategyRevisionConflictError(
                    f"strategy already exists — strategy_id={strategy_id}"
                )
            if spec.identity.revision != 1:
                raise StrategyRevisionConflictError(
                    f"first revision must be 1 — revision={spec.identity.revision}"
                )
            self._items[strategy_id] = [spec]

    def get(self, strategy_id: str, revision: int | None = None) -> StrategySpec:
        with self._lock:
            revisions = self._items.get(strategy_id)
            if revisions is None:
                raise StrategyNotFoundError(f"strategy not found — strategy_id={strategy_id}")
            if revision is None:
                return revisions[-1]
            if revision < 1 or revision > len(revisions):
                raise StrategyNotFoundError(
                    f"strategy revision not found — strategy_id={strategy_id} revision={revision}"
                )
            return revisions[revision - 1]

    def append(self, spec: StrategySpec, *, expected_revision: int) -> None:
        with self._lock:
            strategy_id = spec.identity.strategy_id
            revisions = self._items.get(strategy_id)
            if revisions is None:
                raise StrategyNotFoundError(f"strategy not found — strategy_id={strategy_id}")
            actual_revision = revisions[-1].identity.revision
            if actual_revision != expected_revision:
                raise StrategyRevisionConflictError(
                    "strategy revision conflict — "
                    f"strategy_id={strategy_id} expected={expected_revision} "
                    f"actual={actual_revision}"
                )
            if spec.identity.revision != expected_revision + 1:
                raise StrategyRevisionConflictError(
                    "next revision is not monotonic — "
                    f"expected={expected_revision + 1} actual={spec.identity.revision}"
                )
            revisions.append(spec)
