"""P2-01: 실행 설정(`RunEnvironment`) 값 타입·canonical hash·1.1 브리지·런타임 스키마."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest._models import _execution_constraint_rows
from strategy_workbench.domain.backtest.facade.environment import (
    RUN_ENVIRONMENT_CONSTRAINTS,
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


def test_environment_hash_splits_on_every_variable_field() -> None:
    """`market`·`frequency`·`timing` 은 값이 하나뿐이라 변주할 수 없다. 나머지 7 필드를 덮는다."""
    base = _environment()
    variants = (
        replace(base, start=date(2019, 1, 1)),
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


def test_environment_hash_ignores_int_versus_float_notation() -> None:
    """`fee_bps=15` 와 `15.0` 은 `==` 로 같은 실행 설정인데 canonical JSON 은 `15`/`15.0` 으로
    갈린다. 정규화가 없으면 같은 설정이 매니페스트에서 두 개의 hash 를 갖는다."""
    integral = RunEnvironment(
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
        universe_id="KOSPI200",
        fee_bps=15,
        slippage_bps=10,
        participation_rate=1,
    )
    decimal = replace(integral, fee_bps=15.0, slippage_bps=10.0, participation_rate=1.0)

    assert integral == decimal
    assert environment_hash(integral) == environment_hash(decimal)
    assert isinstance(integral.fee_bps, float)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("participation_rate", 50.0),
        ("participation_rate", 0.0),
        ("fee_bps", -1.0),
        ("slippage_bps", -0.5),
        ("universe_id", "   "),
    ],
)
def test_out_of_range_values_are_rejected_at_construction(field_name: str, value: object) -> None:
    with pytest.raises(ValueError) as error:
        replace(_environment(), **{field_name: value})

    assert field_name in str(error.value)


def test_reversed_dates_are_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="end must be on or after start"):
        replace(_environment(), start=date(2021, 1, 1), end=date(2020, 1, 1))


def test_missing_constraint_row_names_the_pointer_instead_of_a_bare_key_error() -> None:
    """포인터 rename 은 이 모듈의 import 를 깨뜨린다 — 즉 부팅이 죽는다. 맥락 없는 KeyError 로
    떨어지지 않고 사라진 포인터와 현재 카탈로그를 실어야 추적할 수 있다."""
    with pytest.raises(LookupError) as error:
        # 부팅 시점 방어라 공개 심볼이 없다. 모듈 경로로 직접 부른다.
        _execution_constraint_rows(("fee_bps", "no_such_field"))

    message = str(error.value)
    assert "missing=['/execution/no_such_field']" in message
    assert "/execution/fee_bps" in message


def test_schema_bounds_come_from_the_same_rows_the_model_validates_with() -> None:
    """스키마 범위와 `__post_init__` 검증이 다른 수치를 쓰면 스키마가 허용하는 값을 모델이
    거부한다. 두 경로가 같은 제약 행을 읽는지 고정한다."""
    properties = run_environment_schema()["properties"]

    assert properties["participation_rate"]["exclusiveMinimum"] == (
        RUN_ENVIRONMENT_CONSTRAINTS["participation_rate"].minimum
    )
    assert properties["participation_rate"]["maximum"] == (
        RUN_ENVIRONMENT_CONSTRAINTS["participation_rate"].maximum
    )
    assert properties["fee_bps"]["minimum"] == RUN_ENVIRONMENT_CONSTRAINTS["fee_bps"].minimum
    assert (
        properties["slippage_bps"]["minimum"] == RUN_ENVIRONMENT_CONSTRAINTS["slippage_bps"].minimum
    )


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
    assert properties["fee_bps"]["type"] == "number"
    assert properties["fee_bps"]["default"] == 15.0
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
