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
    impact_scales,
    participation_volumes,
)
from strategy_workbench.domain.backtest.facade.runs import ENGINE_RULES_VERSION
from tests.test_core_parity import ENGINE_SCENARIOS, _session_scenarios

_PINNED = (
    "backtest-engine-v2",
    "e647a32fbc815186d6af8037489b66065032c362afbd3eb9ab0883ceb943c43d",
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
    # ADV(20행)·σ(수익률 20개) 창이 차도록 25세션. 거래대금이 세션마다 달라 창 길이가 결과를 가른다.
    bars = [(date(2024, 1, day), "A", 100.0 + day % 7, 1e6 * day) for day in range(1, 26)]
    environment = RunEnvironment(
        start=date(2024, 1, 1),
        end=date(2024, 1, 25),
        universe_id="u",
        impact_model=ImpactModel.SQRT,
        participation_basis=ParticipationBasis.ADV20,
    )
    scales = impact_scales(environment, bars, set()) or {}
    volumes = participation_volumes(environment, bars) or {}
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
