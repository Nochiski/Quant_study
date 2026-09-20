"""세션·턴 사용량 집계 (WORKFLOW A-07).

집계는 이벤트 이력에서 파생되는 값이고 저장되지 않는다. 그래서 고정할 것은 "이력이 이렇게
생겼을 때 합이 얼마인가"뿐이며, 여기 테스트는 저장소도 HTTP도 거치지 않는다.
"""

from __future__ import annotations

from dataclasses import fields

from strategy_workbench.application.assistant_chat.facade.usage import (
    TokenTotals,
    aggregate_usage,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    SearchActivity,
    SequencedEvent,
    Source,
    TextDelta,
    Usage,
)


def _history(*entries: tuple[str, ChatEvent]) -> tuple[SequencedEvent, ...]:
    return tuple(
        SequencedEvent(sequence=index, turn_id=turn_id, event=event)
        for index, (turn_id, event) in enumerate(entries)
    )


def _search(query: str) -> SearchActivity:
    return SearchActivity(query=query, sources=(Source(title="t", url="https://example.test"),))


def test_an_empty_history_aggregates_to_zero_with_no_turns() -> None:
    usage = aggregate_usage(())

    assert usage.tokens == TokenTotals()
    assert usage.turns == ()
    assert usage.search_uses == 0
    assert usage.provider_calls == 0


def test_one_turn_sums_every_usage_event_it_emitted() -> None:
    """adapter는 공급자 호출마다 `Usage`를 하나씩 낸다. 턴 합은 그 호출들의 합이다."""
    history = _history(
        ("turn-1", Usage(input_tokens=1200, output_tokens=300, cache_read_tokens=400)),
        ("turn-1", TextDelta(text="…")),
        (
            "turn-1",
            Usage(input_tokens=1500, output_tokens=900, cache_write_tokens=50),
        ),
        ("turn-1", Done(stop_reason="end_turn")),
    )

    usage = aggregate_usage(history)

    assert usage.tokens == TokenTotals(
        input_tokens=2700, output_tokens=1200, cache_read_tokens=400, cache_write_tokens=50
    )
    # 캐시 성분은 입력과 겹치지 않으므로 총입력은 세 칸의 합이다(도메인 `Usage` 불변식).
    assert usage.tokens.total_input_tokens == 3150
    assert usage.provider_calls == 2
    assert [turn.turn_id for turn in usage.turns] == ["turn-1"]
    assert usage.turns[0].tokens == usage.tokens


def test_the_session_total_is_the_sum_of_its_turns() -> None:
    history = _history(
        ("turn-1", Usage(input_tokens=100, output_tokens=10)),
        ("turn-2", Usage(input_tokens=200, output_tokens=20)),
        ("turn-2", Usage(input_tokens=300, output_tokens=30)),
    )

    usage = aggregate_usage(history)

    assert usage.tokens == TokenTotals(input_tokens=600, output_tokens=60)
    assert [turn.tokens.input_tokens for turn in usage.turns] == [100, 500]
    assert usage.provider_calls == 3


def test_search_activity_is_counted_per_turn_and_for_the_session() -> None:
    history = _history(
        ("turn-1", _search("a")),
        ("turn-1", _search("b")),
        ("turn-2", _search("c")),
    )

    usage = aggregate_usage(history)

    assert usage.search_uses == 3
    assert [turn.search_uses for turn in usage.turns] == [2, 1]


def test_a_turn_that_spent_nothing_still_appears_with_zeroes() -> None:
    """0으로 남기지 않고 빼면 화면이 "아직 안 썼다"와 "그런 턴이 없다"를 구분하지 못한다."""
    history = _history(
        ("turn-1", TextDelta(text="답변만")),
        ("turn-1", Done(stop_reason="end_turn")),
    )

    usage = aggregate_usage(history)

    assert [turn.turn_id for turn in usage.turns] == ["turn-1"]
    assert usage.turns[0].tokens == TokenTotals()
    assert usage.turns[0].provider_calls == 0


def test_turns_keep_the_order_they_first_appear_in_the_history() -> None:
    """sequence가 단조 증가하므로 첫 등장 순서가 턴이 시작된 순서다."""
    history = _history(
        ("turn-1", Usage(input_tokens=1, output_tokens=1)),
        ("turn-2", Usage(input_tokens=1, output_tokens=1)),
        ("turn-1", Usage(input_tokens=1, output_tokens=1)),
        ("turn-3", Usage(input_tokens=1, output_tokens=1)),
    )

    usage = aggregate_usage(history)

    assert [turn.turn_id for turn in usage.turns] == ["turn-1", "turn-2", "turn-3"]
    assert usage.turns[0].provider_calls == 2


def test_the_total_input_is_the_sum_of_every_input_component() -> None:
    """입력은 분리형이다 — 성분은 겹치지 않고 총합은 그 합이다.

    캐시 성분이 생기면 이 테스트가 먼저 깨져야 한다. 성분을 늘리고 `total_input_tokens`의 항을
    빠뜨리면 화면이 캐시가 걸린 턴의 입력을 실제보다 적게 보여 준다.
    """
    components = ("input_tokens", "cache_read_tokens", "cache_write_tokens")
    totals = TokenTotals(
        input_tokens=1200, output_tokens=300, cache_read_tokens=800, cache_write_tokens=40
    )

    assert {field.name for field in fields(TokenTotals)} == {*components, "output_tokens"}
    assert totals.total_input_tokens == sum(getattr(totals, name) for name in components)
    assert totals.total_input_tokens == 2040


def test_adding_components_first_gives_the_same_total_as_adding_totals() -> None:
    """`__add__`가 성분만 더해도 되는 근거다. 파생을 언제 계산하든 같은 수여야 한다."""
    left = TokenTotals(input_tokens=100, output_tokens=10, cache_read_tokens=60)
    right = TokenTotals(input_tokens=250, output_tokens=20, cache_write_tokens=15)

    assert (left + right).total_input_tokens == left.total_input_tokens + right.total_input_tokens


def test_token_totals_add_without_touching_the_summation_loop() -> None:
    """토큰 종류를 늘릴 때 고치는 곳이 `__add__` 하나라는 계약을 고정한다."""
    assert TokenTotals(
        input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_tokens=4
    ) + TokenTotals(
        input_tokens=10, output_tokens=20, cache_read_tokens=30, cache_write_tokens=40
    ) == TokenTotals(input_tokens=11, output_tokens=22, cache_read_tokens=33, cache_write_tokens=44)
