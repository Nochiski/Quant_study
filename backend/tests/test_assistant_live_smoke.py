"""live smoke의 게이트와 판정 규칙 (WORKFLOW A-07).

여기서 **네트워크를 타지 않는다.** 실제 호출은 키가 있을 때만 사람이
`scripts/assistant_live_smoke.py`로 돌린다. 이 파일이 고정하는 것은 두 가지다.

1. 키가 없는 환경에서 그 스크립트가 아무 것도 부르지 않고 사유를 말하며 끝나는가. 이 가드가
   없으면 언젠가 CI가 키 없이 공급자를 부르다 타임아웃으로 죽는다.
2. 관측 → 확인 항목 판정 규칙. 관측하지 못한 표면이 `[ok]`로 찍히면 live smoke를 돌린 사람이
   "확인했다"고 PLAN에 적게 된다 — smoke가 있으나 마나 해지는 유일한 실패 모드다.

실행 조건이 갖춰진 환경에서는 마지막 테스트가 실제 호출 한 번을 돈다.
"""

from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

from strategy_workbench.domain.assistant.facade.models import ProviderKind

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    """`scripts/`는 패키지가 아니라 실행 스크립트 디렉터리라 sys.path에 없다."""
    path = str(_SCRIPTS_DIR)
    sys.path.insert(0, path)
    try:
        return import_module("assistant_live_smoke")
    finally:
        sys.path.remove(path)


def _evidence(smoke: ModuleType, **overrides: object) -> object:
    defaults: dict[str, object] = {
        "probe_ok": True,
        "probe_latency_ms": 120,
        "stream_raised": False,
        "event_kinds": ("TextDelta", "Done"),
        "tool_rounds": 0,
        "search_activities": 0,
        "search_with_sources": 0,
        "thinking_with_text": 0,
        "proposal_accepted": False,
        "failure_code": None,
    }
    return smoke.TurnEvidence(**(defaults | overrides))


def test_the_smoke_is_disabled_until_the_environment_asks_for_it(smoke: ModuleType) -> None:
    plan = smoke.plan_smoke({"ANTHROPIC_API_KEY": "sk-not-used"}, frozenset(ProviderKind))

    assert plan.enabled is False
    assert plan.reason


def test_a_provider_without_a_key_is_skipped_with_the_variable_it_wanted(
    smoke: ModuleType,
) -> None:
    plan = smoke.plan_smoke({smoke.LIVE_SMOKE_ENV: "1"}, frozenset(ProviderKind))

    assert plan.runnable == ()
    reasons = dict(plan.skipped)
    assert set(reasons) == set(ProviderKind)
    for kind, reason in reasons.items():
        assert smoke.SECRET_ENV[kind] in reason


def test_an_uninstalled_provider_is_skipped_before_its_key_is_read(smoke: ModuleType) -> None:
    """설치되지 않은 공급자를 "키 없음"으로 묶으면 사용자가 엉뚱한 변수를 찾는다."""
    plan = smoke.plan_smoke(
        {
            smoke.LIVE_SMOKE_ENV: "1",
            "ANTHROPIC_API_KEY": "sk-not-used",
            "OPENAI_API_KEY": "sk-not-used",
        },
        frozenset({ProviderKind.ANTHROPIC}),
    )

    assert plan.runnable == (ProviderKind.ANTHROPIC,)
    assert dict(plan.skipped)[ProviderKind.OPENAI].startswith("adapter 미설치")


def test_running_the_smoke_without_the_environment_reports_and_exits_zero(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for variable in (smoke.LIVE_SMOKE_ENV, *smoke.SECRET_ENV.values()):
        monkeypatch.delenv(variable, raising=False)

    assert smoke.main([]) == 0
    assert smoke.LIVE_SMOKE_ENV in capsys.readouterr().out


def test_a_surface_the_run_never_exercised_is_reported_as_undecided(smoke: ModuleType) -> None:
    verdicts = {
        verdict.item.key: verdict
        for verdict in smoke.verdicts_for(ProviderKind.ANTHROPIC, _evidence(smoke))
    }

    assert verdicts["thinking_display_summarized"].observed is None
    assert verdicts["search_result_fields"].observed is None
    assert verdicts["thinking_signature_roundtrip"].observed is None
    assert verdicts["thinking_display_summarized"].marker == "[?]"


def test_an_exercised_surface_that_came_back_empty_is_a_failure_not_a_question(
    smoke: ModuleType,
) -> None:
    """검색은 했는데 출처가 하나도 안 실렸다면 매핑이 틀린 것이다 — 판정 불가가 아니다."""
    verdicts = {
        verdict.item.key: verdict
        for verdict in smoke.verdicts_for(
            ProviderKind.ANTHROPIC,
            _evidence(smoke, search_activities=2, search_with_sources=0),
        )
    }

    assert verdicts["search_result_fields"].observed is False
    assert verdicts["search_result_fields"].marker == "[fail]"


def test_a_completed_multi_round_turn_confirms_the_reasoning_block_round_trip(
    smoke: ModuleType,
) -> None:
    verdicts = {
        verdict.item.key: verdict
        for verdict in smoke.verdicts_for(
            ProviderKind.ANTHROPIC,
            _evidence(smoke, tool_rounds=3, thinking_with_text=1),
        )
    }

    assert verdicts["thinking_signature_roundtrip"].observed is True
    assert verdicts["thinking_display_summarized"].observed is True


def test_a_failed_turn_does_not_confirm_the_reasoning_block_round_trip(
    smoke: ModuleType,
) -> None:
    verdicts = {
        verdict.item.key: verdict
        for verdict in smoke.verdicts_for(
            ProviderKind.ANTHROPIC,
            _evidence(smoke, tool_rounds=3, failure_code="provider"),
        )
    }

    assert verdicts["thinking_signature_roundtrip"].observed is None


def test_every_checklist_item_has_a_verdict_rule(smoke: ModuleType) -> None:
    """항목을 추가하고 판정 규칙을 빼먹으면 스크립트가 KeyError로 죽는다."""
    for kind in ProviderKind:
        keys = [item.key for item in smoke.CHECKLIST[kind]]
        assert keys
        assert [verdict.item.key for verdict in smoke.verdicts_for(kind, _evidence(smoke))] == keys


@pytest.mark.skipif(
    os.environ.get("STRATEGY_WORKBENCH_LIVE_SMOKE", "").strip() != "1",
    reason="실연결 smoke는 STRATEGY_WORKBENCH_LIVE_SMOKE=1과 공급자 키가 있을 때만 돈다",
)
def test_the_live_smoke_runs_against_every_configured_provider(smoke: ModuleType) -> None:
    plan = smoke.plan_smoke(dict(os.environ))
    if not plan.runnable:
        pytest.skip(f"공급자 키가 없다: {dict(plan.skipped)}")

    assert smoke.main([]) == 0
