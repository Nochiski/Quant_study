"""P2-01·P2-03·V1-01·V2-01·V2-02·V2-03(검증 랩): 실행 설정(`RunEnvironment`) 값 타입·canonical
hash·필수 규칙·연구 구간 잠금·런타임 스키마·매도 거래세·참여 기준·√ 시장충격."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtest_engine import RunConfig
from strategy_workbench.domain.backtest.facade.environment import (
    RUN_ENVIRONMENT_CONSTRAINTS,
    STATUTORY_SELL_TAX_BPS,
    DataFrequency,
    ExecutionTiming,
    ImpactModel,
    Market,
    MissingRunEnvironmentError,
    ParticipationBasis,
    ResearchWindowViolationError,
    RunEnvironment,
    SellTax,
    cost_history_sessions,
    environment_hash,
    impact_scales,
    participation_volumes,
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
    assert (environment.impact_model, environment.impact_coefficient) == (
        ImpactModel.FIXED_BPS,
        1.0,
    )
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
    """`market`·`frequency`·`timing` 은 값이 하나뿐이라 변주할 수 없다. 나머지 12 필드를 덮는다."""
    base = _environment()
    variants = (
        replace(base, start=date(2019, 1, 1)),
        replace(base, end=date(2021, 12, 31)),
        replace(base, universe_id="KOSDAQ150"),
        replace(base, fee_bps=30.0),
        replace(base, slippage_bps=0.0),
        replace(base, participation_rate=1.0),
        replace(base, participation_basis=ParticipationBasis.ADV20),
        replace(base, impact_model=ImpactModel.SQRT),
        replace(base, impact_coefficient=0.5),
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
        ("impact_coefficient", -0.1),
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
        "impact_coefficient": "/impact_coefficient",
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
        "participation_basis",
        "fee_bps",
        "slippage_bps",
        "impact_model",
        "impact_coefficient",
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
    assert properties["participation_basis"]["enum"] == ["session_volume", "adv20"]
    assert properties["participation_basis"]["default"] == "session_volume"
    assert properties["impact_model"]["enum"] == ["fixed_bps", "sqrt"]
    assert properties["impact_model"]["default"] == "fixed_bps"
    assert properties["impact_coefficient"]["default"] == 1.0
    assert properties["impact_coefficient"]["minimum"] == 0.0
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
        # 합계 세율(증권거래세 + 코스피 농어촌특별세)은 결제일(체결일 + 2거래일)이 시행일에 닿는 첫
        # 체결일에 바뀐다. 경계 전날과 당일을 손으로 옮긴 법정 값이다.
        (date(2019, 5, 29), 0.0030),
        (date(2019, 5, 30), 0.0025),
        (date(2020, 12, 28), 0.0025),
        (date(2020, 12, 29), 0.0023),
        (date(2022, 12, 27), 0.0023),
        (date(2022, 12, 28), 0.0020),
        (date(2023, 12, 26), 0.0020),
        (date(2023, 12, 27), 0.0018),
        (date(2024, 12, 26), 0.0018),
        (date(2024, 12, 27), 0.0015),
        (date(2025, 12, 26), 0.0015),
        (date(2025, 12, 29), 0.0020),
    ],
)
def test_statutory_rate_changes_on_the_first_trade_date_that_settles_after_enactment(
    session: date, rate: float
) -> None:
    config = RunConfig(
        run_id="tax",
        initial_cash=1.0,
        sell_tax_schedule=sell_tax_schedule(_environment()),
    )

    assert config.sell_tax_rate(session) == pytest.approx(rate)


def test_session_volume_basis_leaves_the_engine_on_session_volume() -> None:
    """기본값(`session_volume`)은 워밍업을 읽지 않고 기준 거래량도 넘기지 않는다 — 기존 실행
    그대로다."""
    environment = _environment()

    assert environment.participation_basis is ParticipationBasis.SESSION_VOLUME
    assert cost_history_sessions(environment) == 0
    assert participation_volumes(environment, [(date(2024, 1, 2), "A", 100.0, 1_000.0)]) is None


def test_adv20_volume_is_the_prior_rows_average_value_over_the_decision_close() -> None:
    """판단일(직전 행)까지의 거래대금 평균 ÷ 판단일 종가, 내림. 종목마다 따로 센다.

    A: 첫 행은 앞선 행이 없어 0주. 1/3 은 1,000 ÷ 100 = 10주. 1/4 는 (1,000 + 3,000) / 2 ÷ 200 =
    10주. 1/5 는 1/4 거래대금이 없어 평균에서 빠지므로 2,000 ÷ 50 = 40주. B 는 A 와 섞이지 않는다:
    1/3 은 7,000 ÷ 70 = 100주.
    """
    rows = [
        (date(2024, 1, 2), "A", 100.0, 1_000.0),
        (date(2024, 1, 2), "B", 70.0, 7_000.0),
        (date(2024, 1, 3), "A", 200.0, 3_000.0),
        (date(2024, 1, 3), "B", 10.0, 9_999.0),
        (date(2024, 1, 4), "A", 50.0, None),
        (date(2024, 1, 5), "A", 40.0, 8_000.0),
    ]

    volumes = participation_volumes(
        replace(_environment(), participation_basis=ParticipationBasis.ADV20), reversed(rows)
    )

    assert volumes == {
        (date(2024, 1, 2), "A"): 0,
        (date(2024, 1, 2), "B"): 0,
        (date(2024, 1, 3), "A"): 10,
        (date(2024, 1, 3), "B"): 100,
        (date(2024, 1, 4), "A"): 10,
        (date(2024, 1, 5), "A"): 40,
    }


def test_adv20_averages_only_the_last_twenty_rows_including_warmup() -> None:
    """첫 측정 세션의 평균은 워밍업 행에서 20행을 채운다. 거래대금 100 × k(k = 1..22), 종가 10.

    21번째 행: k = 1..20 평균 1,050 ÷ 10 = 105주. 22번째 행: k = 2..21 평균 1,150 ÷ 10 = 115주 —
    가장 오래된 행이 창에서 빠진다.
    """
    environment = replace(_environment(), participation_basis=ParticipationBasis.ADV20)
    sessions = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(22)]
    rows = [(session, "A", 10.0, 100.0 * k) for k, session in enumerate(sessions, 1)]

    volumes = participation_volumes(environment, rows)

    assert cost_history_sessions(environment) == 20
    assert volumes is not None
    assert (volumes[(sessions[20], "A")], volumes[(sessions[21], "A")]) == (105, 115)


def test_fixed_bps_impact_leaves_the_engine_on_fixed_slippage() -> None:
    """기본값(`fixed_bps`)은 충격 척도를 넘기지 않고 워밍업도 늘리지 않는다 — 기존 실행 그대로다."""
    environment = _environment()

    assert environment.impact_model is ImpactModel.FIXED_BPS
    assert cost_history_sessions(environment) == 0
    assert impact_scales(environment, [(date(2024, 1, 2), "A", 100.0, 1_000.0)], set()) is None


def test_sqrt_scale_is_k_times_prior_return_stdev_over_root_adv() -> None:
    """척도 = k × σ일 / √ADV. σ·ADV 모두 판단일(직전 행)까지만 본다.

    k = 0.5, A 의 종가 100 → 110 → 99 → 99 → 1, 거래대금 1,000 → 1,000 → 3,000 → 1 → 1.
    - 1/2·1/3·1/4: 앞선 수익률이 2개 미만이라 0.
    - 1/5: 수익률 +10%·−10%, 표본 표준편차 √0.02, ADV (1,000 + 1,000 + 3,000) / 3 ÷ 99 = 16주.
      척도 0.5 × √0.02 / 4.
    - 1/8: 수익률 +10%·−10%·0%, 표본 표준편차 0.1, ADV 5,001 / 4 ÷ 99 = 12주. 척도 0.5 × 0.1 / √12.
      체결 세션(1/8)의 종가 1 은 쓰지 않는다.
    B 는 A 와 섞이지 않는다(수익률이 없어 0).
    """
    rows = [
        (date(2024, 1, 2), "A", 100.0, 1_000.0),
        (date(2024, 1, 2), "B", 50.0, 9_999.0),
        (date(2024, 1, 3), "A", 110.0, 1_000.0),
        (date(2024, 1, 4), "A", 99.0, 3_000.0),
        (date(2024, 1, 5), "A", 99.0, 1.0),
        (date(2024, 1, 8), "A", 1.0, 1.0),
    ]
    environment = replace(_environment(), impact_model=ImpactModel.SQRT, impact_coefficient=0.5)

    scales = impact_scales(environment, reversed(rows), set())

    assert scales is not None
    assert {key: scale for key, scale in scales.items() if key[0] < date(2024, 1, 5)} == {
        (date(2024, 1, 2), "A"): 0.0,
        (date(2024, 1, 2), "B"): 0.0,
        (date(2024, 1, 3), "A"): 0.0,
        (date(2024, 1, 4), "A"): 0.0,
    }
    assert scales[(date(2024, 1, 5), "A")] == pytest.approx(0.5 * math.sqrt(0.02) / 4)
    assert scales[(date(2024, 1, 8), "A")] == pytest.approx(0.5 * 0.1 / math.sqrt(12))


def test_sqrt_volatility_uses_the_last_twenty_returns_and_skips_corporate_action_sessions() -> None:
    """σ 창은 수익률 20개다. 종가 100 → 200(분할 전 원주가 점프) 뒤 ±1% 가 번갈아 오는 행 23개.

    - 23번째 행(판단일 22번째): 창은 수익률 2..21번째 20개. 첫 수익률(+100%)이 빠져 σ 가 ±1% 만의
      표본 표준편차다 — 창이 20보다 길면 +100% 가 남아 σ 가 수십 배 커진다.
    - 분할 세션을 자본변동으로 넘기면 +100% 는 처음부터 창에 들어가지 않는다 — 3번째 행 척도가
      ±1% 두 개만 본 값이다.
    """
    sessions = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(23)]
    closes = [100.0, 200.0]
    for index in range(21):
        closes.append(closes[-1] * (1.01 if index % 2 == 0 else 0.99))
    rows = [
        (session, "A", close, 1_000_000.0) for session, close in zip(sessions, closes, strict=True)
    ]
    environment = replace(_environment(), impact_model=ImpactModel.SQRT)

    plain = impact_scales(environment, rows, set())
    adjusted = impact_scales(environment, rows, {(sessions[1], "A")})

    assert cost_history_sessions(environment) == 21
    assert plain is not None and adjusted is not None

    def expected(index: int, first_return: int) -> float:
        returns = [closes[i] / closes[i - 1] - 1 for i in range(first_return, index)]
        adv = math.floor(
            sum(value for *_, value in rows[max(index - 20, 0) : index])
            / min(index, 20)
            / closes[index - 1]
        )
        return statistics.stdev(returns) / math.sqrt(adv)

    assert plain[(sessions[22], "A")] == pytest.approx(expected(22, 2))
    assert plain[(sessions[3], "A")] == pytest.approx(expected(3, 1))
    assert adjusted[(sessions[4], "A")] == pytest.approx(expected(4, 2))
    assert plain[(sessions[4], "A")] > 10 * adjusted[(sessions[4], "A")]
