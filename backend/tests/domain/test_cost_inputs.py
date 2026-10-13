from dataclasses import replace
from datetime import date, timedelta

from strategy_workbench.domain.backtest.facade.environment import (
    ImpactModel,
    ParticipationBasis,
    RunEnvironment,
    cost_inputs,
    impact_input_warnings,
)
from strategy_workbench.domain.backtest.facade.runs import RawFill


def _environment() -> RunEnvironment:
    return RunEnvironment(
        start=date(2024, 1, 1), end=date(2024, 2, 1), universe_id="u", impact_model=ImpactModel.SQRT
    )


def _fill(session: date) -> RawFill:
    return RawFill("F-1", "O-1", session, "A", "buy", "1", 100, 0, 0)


def test_missing_impact_history_is_distinct_from_zero_volatility_and_zero_coefficient() -> None:
    rows = [(date(2024, 1, 1) + timedelta(days=i), "A", 100.0, 1000.0) for i in range(5)]
    for coefficient in (0.0, 1.0):
        inputs = cost_inputs(replace(_environment(), impact_coefficient=coefficient), rows, {})
        assert inputs.impact_scales is not None
        assert all(value == 0 for value in inputs.impact_scales.values())
        # 세 번째 행까지는 판단일까지 수익률이 두 개 미만이다. 네 번째 행부터는 정상 0이다.
        assert set(inputs.impact_unavailable) == {(row[0], "A") for row in rows[:3]}
        assert impact_input_warnings(inputs, [_fill(rows[-1][0])]) == ()
        warnings = impact_input_warnings(inputs, [_fill(rows[1][0]), _fill(rows[1][0])])
        assert len(warnings) == 1
        assert warnings[0].code == "cost.impact_scale_unavailable"
        assert "affected_security_sessions=1" in warnings[0].message
        assert "return_history" in warnings[0].message
        assert impact_input_warnings(inputs, []) == ()


def test_zero_adv_has_its_own_reason_after_return_history_is_sufficient() -> None:
    rows = [(date(2024, 1, 1) + timedelta(days=i), "A", 100.0 + i, 0.0) for i in range(5)]
    inputs = cost_inputs(_environment(), rows, {})
    (warning,) = impact_input_warnings(inputs, [_fill(rows[-1][0])])
    assert ":adv" in warning.message


def test_shared_adv_preserves_distinct_minimum_history() -> (
    None
):
    rows = [(date(2024, 1, 1) + timedelta(days=i), "A", 100.0 + i, 1000.0) for i in range(21)]
    inputs = cost_inputs(_environment(), reversed(rows), {})
    assert inputs.participation_volumes is not None
    assert inputs.impact_scales is not None
    assert inputs.participation_volumes[(rows[3][0], "A")] == 0
    assert inputs.impact_scales[(rows[3][0], "A")] > 0
    assert inputs.participation_volumes[(rows[-1][0], "A")] == 8  # 1000 / 119


def test_fixed_session_volume_does_not_consume_cost_rows() -> None:
    def rows():
        raise AssertionError("기본 비용 모델은 비용 행을 읽을 필요가 없다")
        yield (date(2024, 1, 1), "A", 100.0, 1000.0)

    environment = replace(
        _environment(),
        impact_model=ImpactModel.FIXED_BPS,
        participation_basis=ParticipationBasis.SESSION_VOLUME,
    )
    inputs = cost_inputs(environment, rows(), {})
    assert inputs.participation_volumes is None
    assert inputs.impact_scales is None
