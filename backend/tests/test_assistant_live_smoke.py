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
from typing import Any

import pytest

from strategy_workbench.domain.assistant.facade.models import (
    DEFAULT_MAX_SEARCH_USES,
    ProbeFailure,
    ProviderKind,
)

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


def _probe(smoke: ModuleType, **overrides: Any) -> Any:  # noqa: ANN401  # reason: 스크립트 모듈은 동적 import라 타입이 없다
    defaults: dict[str, Any] = {
        "model": "fake-model-1",
        "ok": True,
        "latency_ms": 120,
        "wrong_key_failure": ProbeFailure.AUTH.value,
        "unknown_model_failure": ProbeFailure.MODEL_NOT_FOUND.value,
    }
    return smoke.ProbeEvidence(**(defaults | overrides))


def _turn(smoke: ModuleType, **overrides: Any) -> Any:  # noqa: ANN401  # reason: 위와 같음
    defaults: dict[str, Any] = {
        "stream_raised": False,
        "event_kinds": ("TextDelta", "Done"),
        "tool_rounds": 0,
        "search_limit": DEFAULT_MAX_SEARCH_USES,
        "search_activities": 0,
        "search_with_sources": 0,
        "search_with_query": 0,
        "events_after_last_search": 0,
        "thinking_with_text": 0,
        "call_output_tokens": (),
        "turn_input_tokens": 0,
        "turn_output_tokens": 0,
        "proposal_accepted": False,
        "failure_code": None,
        "warnings": (),
    }
    return smoke.TurnEvidence(**(defaults | overrides))


def _verdicts(smoke: ModuleType, kind: ProviderKind, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401  # reason: 위와 같음
    probe = overrides.pop("probe", _probe(smoke))
    return {
        verdict.item.key: verdict
        for verdict in smoke.verdicts_for(kind, probe, _turn(smoke, **overrides))
    }


# -- 게이트 -------------------------------------------------------------------------------------


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


# -- 판정 규칙 ----------------------------------------------------------------------------------


def test_every_checklist_item_has_a_verdict_rule(smoke: ModuleType) -> None:
    """항목을 추가하고 판정 규칙을 빼먹으면 스크립트가 KeyError로 죽는다."""
    for kind in ProviderKind:
        keys = [item.key for item in smoke.CHECKLIST[kind]]
        assert keys
        assert list(_verdicts(smoke, kind)) == keys


def test_the_retired_output_format_item_is_not_on_any_checklist(smoke: ModuleType) -> None:
    """A-05 리뷰에서 로컬 재현 가능한 결함으로 판정되어 live smoke 대상에서 빠졌다."""
    keys = {item.key for kind in ProviderKind for item in smoke.CHECKLIST[kind]}

    assert "output_format_none" not in keys


def test_the_probe_question_names_the_constant_that_owns_the_token_limit(
    smoke: ModuleType,
) -> None:
    """확인할 것은 "A-05·A-06이 올린 값에서도 거부되는가"다.

    문장이 값을 말해 주면 읽는 사람이 무엇을 시험하는지 안다. 다만 정본은 adapter 상수이므로
    문장이 그 상수 이름을 같이 대야, 값이 또 바뀌었을 때 어디를 볼지 알 수 있다.
    """
    for kind in ProviderKind:
        probe_items = [item for item in smoke.CHECKLIST[kind] if item.key == "probe_reason_mapping"]
        assert probe_items, kind
        for item in probe_items:
            named = item.question.lower()
            assert "max_tokens" in named or "max_output_tokens" in named, kind


def test_a_surface_the_run_never_exercised_is_reported_as_undecided(smoke: ModuleType) -> None:
    verdicts = _verdicts(smoke, ProviderKind.ANTHROPIC)

    assert verdicts["thinking_display_summarized"].observed is None
    assert verdicts["search_result_fields"].observed is None
    assert verdicts["thinking_signature_roundtrip"].observed is None
    assert verdicts["turn_budget_measurements"].observed is None
    assert verdicts["thinking_display_summarized"].marker == "[?]"


def test_one_tool_round_is_enough_to_exercise_the_thinking_block_round_trip(
    smoke: ModuleType,
) -> None:
    """도구를 한 번 부르면 공급자 호출이 두 번이고, 두 번째가 첫 응답의 사고 블록을 되돌린다."""
    verdicts = _verdicts(smoke, ProviderKind.ANTHROPIC, tool_rounds=1)

    assert verdicts["thinking_signature_roundtrip"].observed is True


def test_a_provider_failure_after_a_tool_round_fails_the_round_trip_check(
    smoke: ModuleType,
) -> None:
    """재전송이 거부되면 화면에는 `Failure(PROVIDER)`만 보인다. 그 증상이 이 항목의 실패다."""
    verdicts = _verdicts(
        smoke,
        ProviderKind.ANTHROPIC,
        tool_rounds=2,
        failure_code="provider",
        warnings=("anthropic turn failed — error_type=BadRequestError",),
    )

    assert verdicts["thinking_signature_roundtrip"].observed is False
    assert "BadRequestError" in verdicts["thinking_signature_roundtrip"].note


def test_a_probe_that_cannot_tell_a_bad_key_from_an_unknown_one_is_a_failure(
    smoke: ModuleType,
) -> None:
    """정상 키의 `ok`만 보면 `AUTH`와 `UNKNOWN`이 뒤바뀐 매핑을 놓친다."""
    verdicts = _verdicts(
        smoke,
        ProviderKind.ANTHROPIC,
        probe=_probe(smoke, wrong_key_failure=ProbeFailure.UNKNOWN.value),
    )

    assert verdicts["probe_reason_mapping"].observed is False


def test_a_probe_that_maps_all_three_reasons_passes(smoke: ModuleType) -> None:
    verdicts = _verdicts(smoke, ProviderKind.OPENAI)

    assert verdicts["probe_reason_mapping"].observed is True
    assert verdicts["default_model_exists"].observed is True


def test_an_exercised_search_that_came_back_empty_is_a_failure_not_a_question(
    smoke: ModuleType,
) -> None:
    """검색은 했는데 출처가 안 실렸다면 필드 매핑이 틀린 것이다 — 판정 불가가 아니다."""
    verdicts = _verdicts(
        smoke,
        ProviderKind.ANTHROPIC,
        search_activities=2,
        search_with_query=2,
        search_with_sources=0,
    )

    assert verdicts["search_result_fields"].observed is False
    assert verdicts["search_result_fields"].marker == "[fail]"


def test_the_search_budget_check_stays_undecided_until_the_limit_is_reached(
    smoke: ModuleType,
) -> None:
    verdicts = _verdicts(
        smoke,
        ProviderKind.ANTHROPIC,
        search_activities=2,
        search_with_query=2,
        search_with_sources=2,
        events_after_last_search=3,
    )

    assert verdicts["search_budget_turn_continues"].observed is None


def test_a_turn_that_kept_going_past_the_search_limit_passes_the_budget_check(
    smoke: ModuleType,
) -> None:
    verdicts = _verdicts(
        smoke,
        ProviderKind.OPENAI,
        search_activities=DEFAULT_MAX_SEARCH_USES,
        search_with_query=DEFAULT_MAX_SEARCH_USES,
        search_with_sources=DEFAULT_MAX_SEARCH_USES,
        events_after_last_search=4,
    )

    assert verdicts["search_budget_notice_reaction"].observed is True


def test_a_turn_that_died_at_the_search_limit_fails_the_budget_check(smoke: ModuleType) -> None:
    """상한에 닿자마자 턴이 끝나면 통지 대신 실패를 보낸 것이다."""
    verdicts = _verdicts(
        smoke,
        ProviderKind.OPENAI,
        search_activities=DEFAULT_MAX_SEARCH_USES,
        search_with_query=DEFAULT_MAX_SEARCH_USES,
        search_with_sources=DEFAULT_MAX_SEARCH_USES,
        events_after_last_search=1,
        failure_code="provider",
    )

    assert verdicts["search_budget_notice_reaction"].observed is False


def test_the_measurement_item_reports_the_numbers_plan_needs(smoke: ModuleType) -> None:
    """이 항목은 통과·실패가 아니라 기록이다. 숫자가 없으면 기록할 것도 없다."""
    verdicts = _verdicts(
        smoke,
        ProviderKind.ANTHROPIC,
        tool_rounds=4,
        call_output_tokens=(1200, 800, 2400),
        turn_input_tokens=41000,
        turn_output_tokens=4400,
        proposal_accepted=True,
    )

    note = verdicts["turn_budget_measurements"].note
    assert verdicts["turn_budget_measurements"].observed is True
    assert "1200" in note
    assert "4400" in note
    assert "4" in note


@pytest.mark.skipif(
    os.environ.get("STRATEGY_WORKBENCH_LIVE_SMOKE", "").strip() != "1",
    reason="실연결 smoke는 STRATEGY_WORKBENCH_LIVE_SMOKE=1과 공급자 키가 있을 때만 돈다",
)
def test_the_live_smoke_runs_against_every_configured_provider(smoke: ModuleType) -> None:
    plan = smoke.plan_smoke(dict(os.environ))
    if not plan.runnable:
        pytest.skip(f"공급자 키가 없다: {dict(plan.skipped)}")

    assert smoke.main([]) == 0
