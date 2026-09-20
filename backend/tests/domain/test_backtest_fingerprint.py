"""P1-09: the run fingerprint identifies what ran, not how the strategy was referenced."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_hash,
)
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
        InMemoryStrategyRepository(), new_id=lambda: "unused"
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
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    environment = RunEnvironment(
        start=date(2021, 1, 1), end=date(2026, 8, 31), universe_id="krx.common-stock"
    )
    return RunManifest(
        run_id="run-001",
        created_at=created,
        completed_at=created,
        engine_core=ExecutionCore.PYTHON,
        engine_version="test",
        run_fingerprint="fingerprint",
        run_spec=BacktestRunSpec(strategy, environment=environment),
        strategy_hash=strategy_hash,
        data_snapshot_id="snap",
        target_tape_hash="tape",
        metric_registry_version="metrics",
        initial_cash=100.0,
        annualization_days=252,
        fee_bps=environment.fee_bps,
        slippage_bps=environment.slippage_bps,
        participation_rate=environment.participation_rate,
        environment=environment,
        environment_hash=environment_hash(environment),
        strategy_provenance=StrategyProvenance(
            StrategySourceKind.SAVED_REVISION, spec_hash, "1.2", "s1", 3, "b" * 64
        ),
    )


def test_manifest_records_one_strategy_hash_under_two_names() -> None:
    """DEFECT-105: `strategy_hash` comes from the compiled tape and `strategy_provenance
    .spec_hash` from the resolved request. Nothing forced them to agree, so a manifest could
    name two different strategies for one run and every existing assertion still passed."""
    assert _manifest("a" * 64, "a" * 64).strategy_hash == "a" * 64

    with pytest.raises(ValueError, match="manifest records two strategies for one run"):
        _manifest("a" * 64, "c" * 64)


def test_manifest_rejects_a_cost_model_that_diverges_from_its_environment() -> None:
    """비용 축이 두 곳에 기록되므로(`environment` 와 평면 필드) 어긋나면 리포트가 읽는 축에 따라
    같은 run 의 수수료가 달라진다. 평면 필드 제거는 P2-03 이고, 그때까지는 대조가 막는다."""
    base = _manifest("a" * 64, "a" * 64)

    with pytest.raises(ValueError, match="manifest records two execution cost models"):
        replace(base, fee_bps=base.environment.fee_bps + 1.0)

    with pytest.raises(ValueError) as error:
        replace(base, participation_rate=0.5)
    message = str(error.value)
    assert "run_id=run-001" in message
    assert "participation_rate: manifest=0.5" in message
    assert f"environment={base.environment.participation_rate!r}" in message


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
