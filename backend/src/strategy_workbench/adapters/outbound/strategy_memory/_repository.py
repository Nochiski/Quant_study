from __future__ import annotations

from threading import RLock
from typing import TypeVar

from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    RevisionSummary,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
    StrategyRevisionRecord,
    StrategySummary,
)

T = TypeVar("T")


class InMemoryStrategyRepository:
    """Process-local revision store; the reference implementation of the repository contract."""

    def __init__(self) -> None:
        self._items: dict[str, list[StrategyRevisionRecord]] = {}
        self._lock = RLock()

    def add(self, record: StrategyRevisionRecord) -> None:
        with self._lock:
            if record.strategy_id in self._items:
                raise StrategyRevisionConflictError(
                    f"strategy already exists — strategy_id={record.strategy_id}"
                )
            if record.revision != 1:
                raise StrategyRevisionConflictError(
                    f"first revision must be 1 — revision={record.revision}"
                )
            self._items[record.strategy_id] = [record]

    def get(self, strategy_id: str, revision: int | None = None) -> StrategyRevisionRecord:
        with self._lock:
            revisions = self._revisions(strategy_id)
            if revision is None:
                return revisions[-1]
            if revision < 1 or revision > len(revisions):
                raise StrategyNotFoundError(
                    f"strategy revision not found — strategy_id={strategy_id} revision={revision}"
                )
            return revisions[revision - 1]

    def append(self, record: StrategyRevisionRecord, *, expected_revision: int) -> None:
        with self._lock:
            revisions = self._revisions(record.strategy_id)
            actual_revision = revisions[-1].revision
            if actual_revision != expected_revision:
                raise StrategyRevisionConflictError(
                    "strategy revision conflict — "
                    f"strategy_id={record.strategy_id} expected={expected_revision} "
                    f"actual={actual_revision}"
                )
            if record.revision != expected_revision + 1:
                raise StrategyRevisionConflictError(
                    "next revision is not monotonic — "
                    f"expected={expected_revision + 1} actual={record.revision}"
                )
            revisions.append(record)

    def list_strategies(self, page: PageRequest) -> Page[StrategySummary]:
        with self._lock:
            summaries = [
                StrategySummary(
                    strategy_id=strategy_id,
                    title=revisions[-1].spec.title,
                    latest_revision=revisions[-1].revision,
                    spec_hash=revisions[-1].spec_hash,
                    updated_at=revisions[-1].provenance.created_at,
                )
                for strategy_id, revisions in sorted(self._items.items())
            ]
        return _page(summaries, page)

    def history(self, strategy_id: str, page: PageRequest) -> Page[RevisionSummary]:
        with self._lock:
            summaries = [
                RevisionSummary(
                    strategy_id=strategy_id,
                    revision=record.revision,
                    spec_hash=record.spec_hash,
                    origin=record.provenance.origin,
                    created_at=record.provenance.created_at,
                    source_format=record.source.format if record.source else None,
                    source_hash=record.source.source_hash if record.source else None,
                    change_note=record.provenance.change_note,
                )
                for record in self._revisions(strategy_id)
            ]
        return _page(summaries, page)

    def _revisions(self, strategy_id: str) -> list[StrategyRevisionRecord]:
        revisions = self._items.get(strategy_id)
        if revisions is None:
            raise StrategyNotFoundError(f"strategy not found — strategy_id={strategy_id}")
        return revisions


def _page(items: list[T], page: PageRequest) -> Page[T]:
    return Page(
        items=tuple(items[page.offset : page.offset + page.limit]),
        total=len(items),
        offset=page.offset,
        limit=page.limit,
    )
