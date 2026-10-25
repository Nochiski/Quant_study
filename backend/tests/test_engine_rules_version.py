"""#335: 엔진 체결·비용 규칙을 바꾸는 PR 은 `ENGINE_RULES_VERSION` 을 올린다.

판본은 매니페스트 `engine_version` 과 실행 지문에 실려 결과 공유를 가른다. 두 코어 parity
시나리오의 체결 기록(reference 코어 — Rust 코어는 parity 테스트가 같음을 지킨다)과 domain 비용
규칙(법정 세율표·충격 상한, ADV·σ 창과 척도 공식을 지나는 계산 결과)을 digest 하나로 묶어 판본과
짝으로 고정한다. 규칙만 바뀌고 판본이 그대로면 실패한다 — 판본을 올리고 `_PINNED` 를 새 digest 로
고친다.
"""

from __future__ import annotations

import hashlib
from datetime import date

from strategy_workbench.domain.backtest.facade.environment import (
    MAX_IMPACT_FRACTION,
    STATUTORY_SELL_TAX_BPS,
    ImpactModel,
    ParticipationBasis,
    RunEnvironment,
    cost_inputs,
    settlement_multipliers,
)
from strategy_workbench.domain.backtest.facade.runs import ENGINE_RULES_VERSION, ExecutionCore
from tests.integration.test_backtest_session_calendar import (
    SESSIONS,
)
from tests.integration.test_backtest_session_calendar import (
    _execute as _execute_calendar_request,
)
from tests.integration.test_backtest_session_calendar import (
    _request as _calendar_request,
)
from tests.test_core_parity import ENGINE_SCENARIOS, _session_scenarios
from tests.test_executable_capacity import proportional_integer_scenario
from tests.test_execution_cash_projection import actual_cash_scenario
from tests.test_negative_cash_recovery import recovery_scenario

# 기록만 더한 변경(V4-04 2/2: 체결 행의 `cap_volume`, 목표 금액 → 수량 변환의 ROUNDING 레코드)은
# trace 가 바뀌어 digest 가 달라지지만 체결·비용 규칙은 그대로라 판본을 올리지 않고 digest 만
# 고쳤다(리드 결정). 지문은 결과 공유 캐시 키가 아니라 옛 결과가 새 기록 없이 재사용되지 않는다.
# v5는 비용의 레버리지 배수·체결가 재평가·양방향 청산 노출 해제를 견적에 반영한다.
# leveraged_short_tax_budget은 L2 숏16주→환매/롱32주 경로로 해당 규칙을 고정한다.
# #411은 주문 상태의 진단 필드를 추가하고 기존 callback·결정 순서를 보존한다.
# v6는 실제 장부의 정수 경계와 비례 축소 후 최종 수량의 여력을 다시 판정한다.
# 같은 미병합 v6 수정에서 대금→수수료→세금의 실제 현금 순서를 포함한다. 기존 28개
# scenario trace/decision은 그대로이며 actual_cash_fee_application은 새 경계를 고정한다.
# borrow_cost_negative_cash_recovery는 비용 후 음수 현금에서 매도/환매로 회복하는 경로다.
# v7는 실행 어댑터가 빈 거래일을 보존하는 경로도 벤치마크 유무별 결과로 고정한다.
# #628은 결과 번들에 엔진이 적용한 분할·병합 기록(`corporate_actions`)을 더한다. 체결·비용 규칙은
# 그대로라 판본은 두고 digest 만 고친다(기록만 더한 변경과 같은 리드 결정).
_PINNED = (
    "backtest-engine-v7",
    "f4a66e0a29dc4511282e35a54e38b1b18a2e7c7440ac7a2995f1fe60f8309611",
)


def _rules_digest() -> str:
    digest = hashlib.sha256()
    # 주문 생명주기·고정 bp 슬리피지(제품 기본 비용 경로)는 세션 시나리오에 있다.
    scenarios = {**ENGINE_SCENARIOS, **_session_scenarios()}
    for name in sorted(scenarios):
        engine, _result = scenarios[name]("python")
        digest.update(name.encode())
        digest.update(engine.event_store.trace_bytes())
        digest.update(engine.event_store.decision_tape_bytes())
    engine, _result = proportional_integer_scenario("python", 75.0)
    digest.update(b"proportional_integer_budget")
    digest.update(engine.event_store.trace_bytes())
    digest.update(engine.event_store.decision_tape_bytes())
    engine, _result = actual_cash_scenario("python")
    digest.update(b"actual_cash_fee_application")
    digest.update(engine.event_store.trace_bytes())
    digest.update(engine.event_store.decision_tape_bytes())
    engine, _result = recovery_scenario("python")
    digest.update(b"borrow_cost_negative_cash_recovery")
    digest.update(engine.event_store.trace_bytes())
    digest.update(engine.event_store.decision_tape_bytes())
    # 어댑터의 거래 달력이 DAY 주문 만료일과 다음 판단·체결일을 결정한다.
    for benchmark in (False, True):
        result = _execute_calendar_request(
            _calendar_request(
                ExecutionCore.PYTHON,
                benchmark=benchmark,
                empty_sessions=(SESSIONS[2],),
                fee_bps=100,
            )
        )
        digest.update(f"session_calendar_benchmark={benchmark}".encode())
        digest.update(repr(result.artifacts).encode())
    # ADV(20행)·σ(수익률 20개) 창이 차도록 25세션. 거래대금이 세션마다 달라 창 길이가 결과를 가른다.
    # 23일은 거래정지라 bar 가 없고 그날의 1:2 분할은 다음 행(24일)에서 정산된다(#339). 정산 행 앞에
    # 거래대금이 있는 행이 20개 넘게 있어야 참여 한도가 0 이 아니라 배수가 한도에 드러난다(#396).
    bars = [
        (date(2024, 1, day), "A", 100.0 + day % 7, 1e6 * day) for day in range(1, 26) if day != 23
    ]
    settled = settlement_multipliers(bars, [(date(2024, 1, 23), "A", 2.0)])
    environment = RunEnvironment(
        start=date(2024, 1, 1),
        end=date(2024, 1, 25),
        universe_id="u",
        impact_model=ImpactModel.SQRT,
        participation_basis=ParticipationBasis.ADV20,
    )
    scales = cost_inputs(environment, bars, settled).impact_scales or {}
    volumes = cost_inputs(environment, bars, settled).participation_volumes or {}
    rules = (
        STATUTORY_SELL_TAX_BPS,
        MAX_IMPACT_FRACTION,
        sorted(scales.items()),
        sorted(volumes.items()),
    )
    digest.update(repr(rules).encode())
    return digest.hexdigest()


def test_engine_or_cost_rule_changes_bump_the_engine_rules_version() -> None:
    assert (ENGINE_RULES_VERSION, _rules_digest()) == _PINNED, (
        "엔진·비용 규칙 digest 가 판본 짝과 다르다 — 규칙을 바꿨으면 "
        "ENGINE_RULES_VERSION 을 올리고 _PINNED 를 새 짝으로 고친다"
    )
