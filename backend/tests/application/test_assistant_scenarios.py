"""가짜 공급자 시나리오 프레임 골든 (WORKFLOW A-07).

이 골든은 B-05 e2e의 MSW가 재생할 응답이다. backend가 내는 프레임과 어긋나면 frontend는 있지도
않은 계약으로 green이 되고, 진짜 서버에 붙는 순간 깨진다. 그래서 대본을 실제 서비스에 돌린
결과와 byte로 비교한다.

시나리오 실행은 진짜 HTTP 앱과 스레드를 쓰므로 빠르지 않다. 세 개를 한 번만 만들어 모듈 단위로
나눠 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from strategy_workbench.domain.assistant.facade.models import FailureCode

from ..assistant_scenarios import (
    REGENERATE_COMMAND,
    SCENARIO_DIR,
    SCENARIOS,
    build_scenario_frames,
)


@pytest.fixture(scope="module")
def rebuilt() -> Mapping[str, str]:
    return build_scenario_frames()


def _frames(name: str) -> list[dict[str, object]]:
    payload = json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def _types(name: str) -> list[str]:
    return [frame["event"]["type"] for frame in _frames(name)]  # pyright: ignore[reportIndexIssue]  # reason: 골든은 열린 JSON이라 키 타입이 없다


def test_every_scenario_matches_its_golden_file(rebuilt: Mapping[str, str]) -> None:
    for name, text in rebuilt.items():
        golden = SCENARIO_DIR / name
        assert golden.read_text(encoding="utf-8") == text, (
            f"{name} is stale; regenerate with: {REGENERATE_COMMAND}"
        )


def test_no_scenario_file_outlives_the_script_that_produced_it(
    rebuilt: Mapping[str, str],
) -> None:
    on_disk = {path.name for path in SCENARIO_DIR.iterdir() if path.is_file()}

    assert on_disk == set(rebuilt)
    assert on_disk == {f"{name}.json" for name in SCENARIOS}


def test_every_frame_carries_the_sse_envelope_shape(rebuilt: Mapping[str, str]) -> None:
    """MSW는 이 배열을 그대로 SSE로 재생한다. `sequence`가 `id:` 줄이 된다(spec D6)."""
    for name in SCENARIOS:
        frames = _frames(name)
        assert frames, name
        assert [frame["sequence"] for frame in frames] == list(range(len(frames))), name
        for frame in frames:
            assert set(frame) == {"sequence", "turn_id", "event"}, name
            assert frame["turn_id"] == "turn-1", name
            assert "type" in frame["event"], name  # pyright: ignore[reportOperatorIssue]  # reason: 위와 같음


def test_the_simple_answer_scenario_has_no_tool_or_proposal_frames() -> None:
    """제안 카드가 없는 화면을 만드는 대본이다. 도구가 섞이면 그 화면을 못 만든다."""
    assert _types("simple_answer") == ["text_delta", "text_delta", "usage", "done"]


def test_the_tool_then_proposal_scenario_emits_the_proposal_before_its_tool_result() -> None:
    """제안 이벤트가 그 도구의 결과 요약보다 먼저 나간다(`AssistantChatService`의 큐 규칙)."""
    types = _types("tool_then_proposal")

    assert types.index("proposal") < types.index("tool_result", types.index("proposal"))
    assert types[-1] == "done"


def test_the_search_then_failure_scenario_ends_with_an_invalid_proposal_failure() -> None:
    """검색 활동 칩과 실패 표시를 같이 태우는 대본이다."""
    frames = _frames("search_then_failure")
    types = _types("search_then_failure")

    assert types[0] == "search_activity"
    assert types[-1] == "failure"
    assert frames[-1]["event"]["code"] == FailureCode.PROPOSAL_INVALID.value  # pyright: ignore[reportIndexIssue]  # reason: 위와 같음
    assert "proposal" not in types


def test_the_search_scenario_keeps_only_http_urls_in_its_sources() -> None:
    """출처 링크 렌더 안전(spec D7)을 프론트가 시험하려면 fixture가 정상 URL이어야 한다."""
    sources = _frames("search_then_failure")[0]["event"]["sources"]  # pyright: ignore[reportIndexIssue]  # reason: 위와 같음

    assert sources
    for source in sources:
        assert source["url"].startswith("https://")
