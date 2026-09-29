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
    SellTax,
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
from strategy_workbench.domain.strategy.facade.specification import (
    FloatParameter,
    IntegerParameter,
    ParameterValue,
    resolve_parameter_values,
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


def test_omitted_and_explicit_default_parameter_values_share_one_fingerprint() -> None:
    """검증 랩 V3-02: 접수가 해소한 값(`resolve_parameter_values`)이 지문에 든다.

    생략한 요청과 기본값을 다른 표기(20.0)로 적은 요청은 같은 실행이고, 기본값이 아닌 값만 지문을
    가른다.
    """
    parameters = (
        IntegerParameter("lookback", 20, 10, 30, "integer"),
        FloatParameter("weight", 0.5, 0.0, 2.0, "float"),
    )
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    base = BacktestRunSpec(strategy=replace(template, parameters=parameters))

    def resolved(requested: dict[str, ParameterValue]) -> str:
        values = resolve_parameter_values(parameters, requested)
        return _fingerprint(replace(base, parameter_values=values))

    omitted = resolved({})
    assert resolved({"lookback": 20.0, "weight": 0.5}) == omitted
    assert resolved({"lookback": 21}) != omitted
    # 정규화가 빠지면 20.0 과 20 이 canonical JSON 에서 다른 표기가 되어 지문이 갈린다.
    assert (
        _fingerprint(replace(base, parameter_values={"lookback": 20.0, "weight": 0.5})) != omitted
    )


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


def test_manifest_flat_cost_check_ignores_environment_only_cost_fields() -> None:
    """매니페스트 평면 필드는 수수료·슬리피지·참여율 셋뿐이다. 실행 설정에만 있는 거래세 칸이
    대조 집합에 들어가면 `AttributeError` 가 난다(spec D7)."""
    manifest = _manifest("a" * 64, "a" * 64)
    taxed = replace(manifest.environment, sell_tax=SellTax.CUSTOM, sell_tax_bps=20.0)

    rebuilt = replace(manifest, environment=taxed, environment_hash=environment_hash(taxed))

    assert rebuilt.environment.sell_tax_bps == 20.0


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
