"""P2-01·P2-03: 실행 설정(`RunEnvironment`) 값 타입·canonical hash·필수 규칙·런타임 스키마."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.domain.backtest.facade.environment import (
    RUN_ENVIRONMENT_CONSTRAINTS,
    DataFrequency,
    ExecutionTiming,
    Market,
    MissingRunEnvironmentError,
    RunEnvironment,
    environment_hash,
    require_environment,
    run_environment_canonical_json,
    run_environment_schema,
    run_environment_schema_hash,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy


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


def test_constraint_rows_point_at_the_run_environment_document() -> None:
    """P2-03: 범위 행의 owner 가 전략 제약 카탈로그에서 실행 설정으로 옮겨 왔다.

    1.2 문서에는 `execution` 섹션이 없으므로 `/execution/*` 포인터는 가리킬 곳이 없다. 행이
    전략 포인터를 그대로 들고 있으면 런타임 스키마가 실행 설정 필드에 남의 문서 경로를 싣는다.
    """
    from strategy_workbench.domain.strategy.facade.constraints import scalar_constraint_index

    assert {name: row.pointer for name, row in RUN_ENVIRONMENT_CONSTRAINTS.items()} == {
        "participation_rate": "/participation_rate",
        "fee_bps": "/fee_bps",
        "slippage_bps": "/slippage_bps",
    }
    # 진단 코드는 `strategy.*` validator 레지스트리 밖이다.
    assert all(
        row.code.startswith("run_environment.") for row in RUN_ENVIRONMENT_CONSTRAINTS.values()
    )
    assert not [row for row in scalar_constraint_index() if row.startswith("/execution/")]


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


def test_explicit_environment_is_returned_unchanged() -> None:
    explicit = _environment()

    assert require_environment(explicit, requested_by="test") is explicit


def test_missing_environment_is_refused_with_a_coded_diagnostic() -> None:
    """P2-03: 1.2 문서에는 기간·유니버스가 없다. 기본값을 지어내면 사용자가 지정한 적 없는
    구간으로 백테스트가 돌고 매니페스트가 그 값을 사실로 기록한다."""
    with pytest.raises(MissingRunEnvironmentError) as info:
        require_environment(None, requested_by="portfolio.preview('퀄리티 모멘텀')")

    message = str(info.value)
    assert info.value.code == "run_environment.required"
    # error-messages.md: 식별자와 기대 vs 실제가 메시지에 들어간다.
    assert "requested_by=portfolio.preview('퀄리티 모멘텀')" in message
    assert "expected=" in message and "got=None" in message
    assert "실행 설정을 지정하라" in message


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
