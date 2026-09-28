"""은퇴 문서의 실행 설정 원문 → `RunEnvironment` (spec D6·D7, P2-09).

1.1 → 1.2 업그레이드가 문서에서 떼어 낸 `data`·`execution`·`missing_policy` 값을 실행 설정으로
바꾼다. 규칙의 owner 는 `RunEnvironment` 를 가진 `domain/backtest` 다. 읽을 수 없는 값은 예외가
아니라 결과 값(`problems`)으로 돌려준다 — 업그레이드 자체는 성공하고 사용자가 실행 설정을 직접
채우면 되는 예상된 실패다.
"""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.backtest.facade.environment import (
    DEFAULT_MISSING_POLICY,
    RetiredEnvironment,
    RunEnvironment,
    environment_from_retired_settings,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.document import RetiredExecutionSettings

DATA = {
    "market": "KRX",
    "start": "2021-01-01",
    "end": "2026-08-31",
    "universe_id": "krx.common-stock",
    "frequency": "daily",
}


def _settings(
    data: dict[str, object] | None = None,
    execution: dict[str, object] | None = None,
    missing: object | None = "drop",
) -> RetiredExecutionSettings:
    return RetiredExecutionSettings(
        data_section=DATA if data is None else data,
        execution_section={"timing": "next_open", "fee_bps": 15.0}
        if execution is None
        else execution,
        missing_policy=missing,
        missing_policy_pointer=None if missing is None else "/factors/0/graph/missing_policy",
    )


def test_a_complete_1_1_setting_becomes_the_run_environment_it_described() -> None:
    result = environment_from_retired_settings(
        _settings(
            execution={"timing": "next_open", "fee_bps": 7, "slippage_bps": 3.5}, missing="zero"
        )
    )

    assert result.problems == ()
    assert result.environment == RunEnvironment(
        start=date(2021, 1, 1),
        end=date(2026, 8, 31),
        universe_id="krx.common-stock",
        fee_bps=7.0,
        slippage_bps=3.5,
        missing=MissingPolicy.ZERO,
    )


def test_keys_the_document_left_out_take_the_1_1_defaults() -> None:
    """1.1 모델의 기본값과 `RunEnvironment` 기본값은 같다 — 생략한 값이 실행 설정 기본값이 된다."""
    result = environment_from_retired_settings(
        _settings(
            data={"start": "2021-01-01", "end": "2021-12-31", "universe_id": "u"},
            execution={},
            missing=None,
        )
    )

    assert result.environment == RunEnvironment(
        start=date(2021, 1, 1), end=date(2021, 12, 31), universe_id="u"
    )
    assert result.environment is not None
    assert result.environment.missing is DEFAULT_MISSING_POLICY


@pytest.mark.parametrize(
    ("data", "execution", "missing", "pointer"),
    [
        pytest.param({**DATA, "start": "2021/01/01"}, None, "drop", "/data/start", id="bad-date"),
        pytest.param({**DATA, "market": "NYSE"}, None, "drop", "/data/market", id="bad-enum"),
        pytest.param({**DATA, "universe_id": 3}, None, "drop", "/data/universe_id", id="bad-text"),
        pytest.param(DATA, {"fee_bps": "15bp"}, "drop", "/execution/fee_bps", id="bad-number"),
        pytest.param(DATA, {"fee_bps": True}, "drop", "/execution/fee_bps", id="bool-number"),
        pytest.param(
            DATA, {"order_style": "market"}, "drop", "/execution/order_style", id="unknown"
        ),
        pytest.param(DATA, None, "sometimes", "/factors/0/graph/missing_policy", id="bad-missing"),
        pytest.param(
            {k: v for k, v in DATA.items() if k != "universe_id"},
            None,
            "drop",
            "/data/universe_id",
            id="required",
        ),
        pytest.param({**DATA, "end": "2020-01-01"}, None, "drop", "", id="range"),
        pytest.param(DATA, {"participation_rate": 2.0}, "drop", "", id="bound"),
    ],
)
def test_an_unreadable_setting_is_a_problem_value_not_an_exception(
    data: dict[str, object], execution: dict[str, object] | None, missing: object, pointer: str
) -> None:
    result = environment_from_retired_settings(_settings(data, execution, missing))

    assert result.environment is None
    assert [problem.pointer for problem in result.problems] == [pointer]
    assert "실행 설정" in result.problems[0].message and " — " in result.problems[0].message


def test_an_absent_data_section_names_every_required_field() -> None:
    result = environment_from_retired_settings(_settings(data={}, execution={}, missing=None))

    assert result.environment is None
    assert [problem.pointer for problem in result.problems] == [
        "/data/start",
        "/data/end",
        "/data/universe_id",
    ]


def test_the_result_cannot_carry_both_an_environment_and_problems() -> None:
    environment = RunEnvironment(start=date(2021, 1, 1), end=date(2021, 1, 2), universe_id="u")
    problems = environment_from_retired_settings(_settings(data={})).problems

    with pytest.raises(ValueError, match="exactly one"):
        RetiredEnvironment(environment, problems)
    with pytest.raises(ValueError, match="exactly one"):
        RetiredEnvironment(None, ())
