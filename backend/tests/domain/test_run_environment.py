"""P2-01·P2-03·V1-01(검증 랩): 실행 설정(`RunEnvironment`) 값 타입·canonical hash·필수 규칙·
연구 구간 잠금·런타임 스키마."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from backtest_engine import RunConfig
from strategy_workbench.domain.backtest.facade.environment import (
    RUN_ENVIRONMENT_CONSTRAINTS,
    STATUTORY_SELL_TAX_BPS,
    DataFrequency,
    ExecutionTiming,
    Market,
    MissingRunEnvironmentError,
    ResearchWindowViolationError,
    RunEnvironment,
    SellTax,
    environment_hash,
    require_environment,
    run_environment_canonical_json,
    run_environment_schema,
    run_environment_schema_hash,
    sell_tax_schedule,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


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
    """`market`·`frequency`·`timing` 은 값이 하나뿐이라 변주할 수 없다. 나머지 9 필드를 덮는다."""
    base = _environment()
    variants = (
        replace(base, start=date(2019, 1, 1)),
        replace(base, end=date(2021, 12, 31)),
        replace(base, universe_id="KOSDAQ150"),
        replace(base, fee_bps=30.0),
        replace(base, slippage_bps=0.0),
        replace(base, participation_rate=1.0),
        replace(base, missing=MissingPolicy.ZERO),
        replace(base, sell_tax=SellTax.NONE),
        replace(base, sell_tax=SellTax.CUSTOM, sell_tax_bps=20.0),
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
        "sell_tax_bps": "/sell_tax_bps",
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


def test_environment_measured_from_the_research_floor_is_returned_unchanged() -> None:
    """연구 하한 2020-01-02 당일부터 측정하는 실행 설정은 그대로 통과한다(spec D1)."""
    explicit = replace(_environment(), start=date(2020, 1, 2))

    assert require_environment(explicit, requested_by="test") is explicit


@pytest.mark.parametrize(
    "start",
    [
        date(2020, 1, 1),  # 하한 전날(신정 휴장일)
        date(2019, 12, 31),  # 봉인 구간 마지막 날
        date(2015, 12, 31),  # 봉인 앞 구간도 측정하지 않는다
    ],
)
def test_measurement_before_the_research_floor_is_refused(start: date) -> None:
    """spec D1: 판정은 "측정 시작일 ≥ 2020-01-02" 하나다. 봉인 구간을 한 세션이라도 측정하면
    홀드아웃이 이미 열람된 것과 같다."""
    with pytest.raises(ResearchWindowViolationError) as info:
        require_environment(
            replace(_environment(), start=start),
            requested_by="backtest.run('퀄리티 모멘텀')",
        )

    message = str(info.value)
    assert info.value.code == "run_environment.research_window"
    assert "requested_by=backtest.run('퀄리티 모멘텀')" in message
    assert f"expected=start>=2020-01-02 got=start={start}" in message
    assert "2016-01-01~2019-12-31은 홀드아웃 봉인 구간" in message


def test_pre_research_environment_is_still_a_constructible_value() -> None:
    """잠금은 실행 관문의 판정이지 값 규칙이 아니다. 엔진 직접 테스트·벤치 스크립트·은퇴 문서
    업그레이드 응답이 봉인 구간 환경을 값으로 만들 수 있어야 한다(spec D1)."""
    sealed = RunEnvironment(start=date(2016, 1, 1), end=date(2019, 12, 31), universe_id="KOSPI200")

    assert (sealed.start, sealed.end) == (date(2016, 1, 1), date(2019, 12, 31))


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
        "sell_tax",
        "sell_tax_bps",
        "missing",
    }
    assert schema["required"] == ["start", "end", "universe_id"]
    assert properties["market"]["enum"] == ["KRX"]
    assert properties["missing"]["enum"] == [member.value for member in MissingPolicy]
    assert properties["sell_tax"]["enum"] == [member.value for member in SellTax]
    assert properties["sell_tax"]["default"] == "krx_statutory"
    assert properties["sell_tax_bps"]["default"] is None
    assert properties["sell_tax_bps"]["minimum"] == 0.0
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


def test_run_environment_schema_fixture_is_current() -> None:
    """frontend 실행 설정 패널 테스트가 읽는 사본이 실제 스키마와 같다(P3-02, BACKLOG-013)."""
    fixture = json.loads((FIXTURES / "run-environment-schema.json").read_text(encoding="utf-8"))
    assert fixture == run_environment_schema(), (
        "run-environment-schema.json is stale; regenerate with: "
        "uv run python tools/export_runtime_schema.py"
    )


def test_sell_tax_defaults_to_the_statutory_table() -> None:
    environment = _environment()

    assert (environment.sell_tax, environment.sell_tax_bps) == (SellTax.KRX_STATUTORY, None)
    assert sell_tax_schedule(environment) == STATUTORY_SELL_TAX_BPS[Market.KRX]
    assert sell_tax_schedule(replace(environment, sell_tax=SellTax.NONE)) == ()
    custom = replace(environment, sell_tax=SellTax.CUSTOM, sell_tax_bps=12)
    assert custom.sell_tax_bps == 12.0
    assert sell_tax_schedule(custom) == ((date.min, 12.0),)


@pytest.mark.parametrize(
    ("sell_tax", "sell_tax_bps"),
    [
        (SellTax.CUSTOM, None),
        (SellTax.KRX_STATUTORY, 20.0),
        (SellTax.NONE, 0.0),
        (SellTax.CUSTOM, -1.0),
    ],
)
def test_sell_tax_rate_is_only_and_always_given_for_custom(
    sell_tax: SellTax, sell_tax_bps: float | None
) -> None:
    """세율 칸은 `custom` 에서만 읽힌다. 다른 방식에 값이 있으면 매니페스트만 보고 무엇이 적용됐는지
    알 수 없다."""
    with pytest.raises(ValueError, match="sell_tax_bps") as error:
        replace(_environment(), sell_tax=sell_tax, sell_tax_bps=sell_tax_bps)

    assert getattr(error.value, "field", None) == "sell_tax_bps"


@pytest.mark.parametrize(
    ("session", "rate"),
    [
        # 합계 세율(증권거래세 + 코스피 농어촌특별세)이 시행일에 바뀐다. 손으로 옮긴 법정 값이다.
        (date(2019, 6, 2), 0.0030),
        (date(2019, 6, 3), 0.0025),
        (date(2020, 12, 31), 0.0025),
        (date(2021, 1, 1), 0.0023),
        (date(2023, 1, 2), 0.0020),
        (date(2024, 1, 2), 0.0018),
        (date(2025, 12, 30), 0.0015),
        (date(2026, 1, 2), 0.0020),
    ],
)
def test_statutory_rate_changes_on_each_effective_date(session: date, rate: float) -> None:
    config = RunConfig(
        run_id="tax",
        initial_cash=1.0,
        sell_tax_schedule=sell_tax_schedule(_environment()),
    )

    assert config.sell_tax_rate(session) == pytest.approx(rate)

