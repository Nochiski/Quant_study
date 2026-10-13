"""P1-09: the run fingerprint identifies what ran, not how the strategy was referenced."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_hash,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    InlineDraft,
    MetricWindow,
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
    # 인라인 초안의 계열도 출처 정보다(검증 랩 spec D2).
    in_lineage = replace(draft, lineage_strategy_id="s1")

    assert len({_fingerprint(s) for s in (legacy, saved, draft, other_draft, in_lineage)}) == 1
    assert _fingerprint(replace(legacy, initial_cash=1.0)) != _fingerprint(legacy)


def test_fingerprint_ignores_metric_window_labels_but_not_their_dates() -> None:
    """#335 DOMAIN-V1-02: 지표 창 표시 이름은 화면이 지은 문자열이라 실행을 가르지 않는다."""
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    oos = MetricWindow(MetricScope.OUT_OF_SAMPLE, date(2024, 1, 2), date(2024, 6, 28), "OOS 1")
    base = BacktestRunSpec(strategy=strategy, metric_windows=(oos,))

    relabelled = replace(base, metric_windows=(replace(oos, label="다른 이름"),))
    moved = replace(base, metric_windows=(replace(oos, start=date(2024, 2, 1)),))

    assert _fingerprint(relabelled) == _fingerprint(base) != _fingerprint(moved)


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
        engine_version="test",
        run_fingerprint="fingerprint",
        run_spec=BacktestRunSpec(strategy, environment=environment, initial_cash=100.0),
        strategy_hash=strategy_hash,
        data_snapshot_id="snap",
        target_tape_hash="tape",
        metric_registry_version="metrics",
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


def test_evaluation_version_separates_new_runs_without_rewriting_historical_identity() -> None:
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    spec = BacktestRunSpec(strategy=strategy)
    assert _fingerprint(spec) == "d5192042941c2873d64c76279bddb0d459d604a71acca6ec43ad65f7e79892cf"
    versions = [None, "factor-evaluation-v1", "factor-evaluation-v2"]
    identities = [
        backtest_run_fingerprint(
            spec,
            data_snapshot_id="snap",
            target_tape_hash="tape",
            engine_version="engine",
            metric_registry_version="metrics",
            factor_evaluation_version=v,
        )
        for v in versions
    ]
    assert identities[0] == _fingerprint(spec)
    assert len(set(identities)) == 3
