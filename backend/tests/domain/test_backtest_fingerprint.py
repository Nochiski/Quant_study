"""P1-09: the run fingerprint identifies what ran, not how the strategy was referenced."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    ExecutionCore,
    InlineDraft,
    RunManifest,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
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


def _manifest(strategy_hash: str, spec_hash: str) -> RunManifest:
    created = datetime(2026, 9, 3, tzinfo=UTC)
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()
    return RunManifest(
        run_id="run-001",
        created_at=created,
        completed_at=created,
        engine_core=ExecutionCore.PYTHON,
        engine_version="test",
        run_fingerprint="fingerprint",
        run_spec=BacktestRunSpec(strategy),
        strategy_hash=strategy_hash,
        data_snapshot_id="snap",
        target_tape_hash="tape",
        metric_registry_version="metrics",
        initial_cash=100.0,
        annualization_days=252,
        fee_bps=0.0,
        slippage_bps=0.0,
        participation_rate=1.0,
        strategy_provenance=StrategyProvenance(
            StrategySourceKind.SAVED_REVISION, spec_hash, "1.0", "s1", 3, "b" * 64
        ),
    )


def test_manifest_records_one_strategy_hash_under_two_names() -> None:
    """DEFECT-105: `strategy_hash` comes from the compiled tape and `strategy_provenance
    .spec_hash` from the resolved request. Nothing forced them to agree, so a manifest could
    name two different strategies for one run and every existing assertion still passed."""
    assert _manifest("a" * 64, "a" * 64).strategy_hash == "a" * 64

    with pytest.raises(ValueError, match="manifest records two strategies for one run"):
        _manifest("a" * 64, "c" * 64)


def test_the_mismatch_message_names_both_producers_and_the_revision() -> None:
    with pytest.raises(ValueError) as error:
        _manifest("a" * 64, "c" * 64)

    message = str(error.value)
    assert "run_id=run-001" in message
    assert f"target_tape.strategy_hash={'a' * 64}" in message
    assert f"provenance.spec_hash={'c' * 64}" in message
    assert "provenance.kind=saved_revision" in message
    assert "strategy_id=s1" in message
    assert "revision=3" in message
