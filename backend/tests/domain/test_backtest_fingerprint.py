"""P1-09: the run fingerprint identifies what ran, not how the strategy was referenced."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    InlineDraft,
    SavedRevisionReference,
    backtest_run_fingerprint,
)


def _fingerprint(spec: BacktestRunSpec) -> str:
    return backtest_run_fingerprint(
        spec,
        data_snapshot_id="snap",
        target_tape_hash="tape",
        engine_version="engine",
        metric_registry_version="metrics",
    )


def test_fingerprint_ignores_how_the_strategy_was_referenced() -> None:
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()
    legacy = BacktestRunSpec(strategy=strategy)
    saved = replace(
        legacy,
        strategy_source=SavedRevisionReference("s1", 3, "a" * 64, "saved_revision"),
    )
    draft = replace(legacy, strategy_source=InlineDraft(strategy, "inline_draft", "b" * 64))
    other_draft = replace(legacy, strategy_source=InlineDraft(strategy, "inline_draft", "c" * 64))

    assert len({_fingerprint(s) for s in (legacy, saved, draft, other_draft)}) == 1
    assert _fingerprint(replace(legacy, initial_cash=1.0)) != _fingerprint(legacy)
