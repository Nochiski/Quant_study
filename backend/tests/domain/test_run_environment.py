"""P2-01: 실행 설정(`RunEnvironment`) 값 타입·canonical hash·1.1 브리지·런타임 스키마."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_from_legacy_spec,
    environment_hash,
    resolve_environment,
    run_environment_canonical_json,
    run_environment_schema,
    run_environment_schema_hash,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.specification import (
    DataFrequency,
    ExecutionTiming,
    Market,
    StrategySpec,
)


def _template() -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()


def _environment() -> RunEnvironment:
    return RunEnvironment(start=date(2020, 1, 1), end=date(2020, 12, 31), universe_id="KOSPI200")


def test_defaults_match_the_design_contract() -> None:
    environment = _environment()

    assert environment.market is Market.KRX
    assert environment.frequency is DataFrequency.DAILY
    assert environment.timing is ExecutionTiming.NEXT_OPEN
    assert environment.missing is MissingPolicy.DROP
    assert (
        environment.participation_rate,
        environment.fee_bps,
        environment.slippage_bps,
    ) == (0.1, 15.0, 10.0)


def test_canonical_json_is_sorted_and_compact() -> None:
    encoded = run_environment_canonical_json(_environment())

    assert encoded.startswith('{"end":"2020-12-31","fee_bps":15.0,')
    assert ", " not in encoded and '": ' not in encoded


def test_environment_hash_splits_on_every_field() -> None:
    base = _environment()
    variants = (
        replace(base, end=date(2021, 12, 31)),
        replace(base, universe_id="KOSDAQ150"),
        replace(base, fee_bps=30.0),
        replace(base, slippage_bps=0.0),
        replace(base, participation_rate=1.0),
        replace(base, missing=MissingPolicy.ZERO),
    )

    hashes = {environment_hash(item) for item in (base, *variants)}
    assert len(hashes) == len(variants) + 1
    assert all(len(item) == 64 for item in hashes)


def test_bridge_reads_data_execution_and_the_first_factor_missing_policy() -> None:
    spec = _template()
    graph = spec.factors[0].graph

    environment = environment_from_legacy_spec(spec)

    assert environment.market is spec.data.market
    assert environment.frequency is spec.data.frequency
    assert (environment.start, environment.end) == (spec.data.start, spec.data.end)
    assert environment.universe_id == spec.data.universe_id
    assert environment.timing is spec.execution.timing
    assert environment.participation_rate == spec.execution.participation_rate
    assert environment.fee_bps == spec.execution.fee_bps
    assert environment.slippage_bps == spec.execution.slippage_bps
    assert environment.missing is graph.missing_policy


def test_bridge_without_factors_falls_back_to_the_model_default() -> None:
    spec = replace(_template(), factors=())

    assert environment_from_legacy_spec(spec).missing is MissingPolicy.DROP


def test_explicit_environment_wins_over_the_legacy_document() -> None:
    spec = _template()
    explicit = replace(_environment(), fee_bps=99.0)

    assert resolve_environment(spec, explicit) is explicit
    assert resolve_environment(spec, None) == environment_from_legacy_spec(spec)


def test_schema_publishes_type_default_and_enum_for_every_field() -> None:
    schema = run_environment_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert set(properties) == {
        "market",
        "frequency",
        "start",
        "end",
        "universe_id",
        "timing",
        "participation_rate",
        "fee_bps",
        "slippage_bps",
        "missing",
    }
    assert schema["required"] == ["start", "end", "universe_id"]
    assert properties["market"]["enum"] == ["KRX"]
    assert properties["missing"]["enum"] == [member.value for member in MissingPolicy]
    assert properties["fee_bps"] == {"type": "number", "default": 15.0}
    assert properties["start"]["format"] == "date"
    assert properties["universe_id"]["x-catalog"] == "universe"


def test_schema_hash_is_stable_and_splits_on_content() -> None:
    schema = run_environment_schema()

    assert run_environment_schema_hash(schema) == run_environment_schema_hash(
        run_environment_schema()
    )
    assert run_environment_schema_hash({**schema, "title": "Other"}) != (
        run_environment_schema_hash(schema)
    )
