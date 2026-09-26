"""어시스턴트 SSE 프레이밍 (설계 spec D6).

HTTP 왕복은 `tests/integration/test_assistant_http_api.py`가 본다. 여기서는 스트림 제너레이터
하나를 직접 돌려, 진짜 스레드로는 재현하기 어려운 세 가지를 고정한다.

1. 조용한 구간의 keepalive 주석.
2. **턴이 끝나는 순간에 저장된 마지막 이벤트를 놓치지 않는가.** 종료 여부를 이벤트보다 먼저
   읽는 순서가 이 성질의 전부라, 순서를 뒤집으면 이 테스트만 깨진다.
3. 같은 세션의 앞선 턴 이벤트를 이 스트림이 다시 보내지 않는가.
"""

from __future__ import annotations

from strategy_workbench.adapters.inbound.http_api._assistant_routes import (
    _event_stream,  # pyright: ignore[reportPrivateUsage]  # reason: 라우트 내부 제너레이터라 공개 facade가 없다
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    SequencedEvent,
    TextDelta,
)

_SESSION = "session-1"
_TURN = "turn-1"


class _ScriptedEventSource:
    """`TurnEventSource` 가짜 구현. `is_settled()` 호출마다 다음 단계로 넘어간다.

    단계마다 "그 조회 시점에 저장소가 갖고 있던 이벤트"를 통째로 적어 두는 이유는, 스트림이
    종료 여부와 이벤트를 **같은 순간에** 읽지 않기 때문이다. 단계별 스냅샷이라야 그 사이
    저장된 이벤트가 어떻게 보이는지 재현할 수 있다.
    """

    def __init__(self, steps: list[tuple[bool, list[SequencedEvent]]]) -> None:
        self._steps = steps
        self._index = -1

    def is_settled(self, turn_id: str) -> bool:
        self._index = min(self._index + 1, len(self._steps) - 1)
        return self._steps[self._index][0]

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        stored = self._steps[max(self._index, 0)][1]
        return tuple(item for item in stored if item.sequence > after_sequence)


def _stored(sequence: int, event: ChatEvent, *, turn_id: str = _TURN) -> SequencedEvent:
    return SequencedEvent(sequence=sequence, turn_id=turn_id, event=event)


def _run(source: _ScriptedEventSource, *, keepalive_seconds: float = 15.0) -> list[str]:
    return list(
        _event_stream(
            source,
            session_id=_SESSION,
            turn_id=_TURN,
            after_sequence=-1,
            keepalive_seconds=keepalive_seconds,
            poll_seconds=0.0,
        )
    )


def test_each_event_ships_as_one_frame_carrying_its_sequence_as_the_id() -> None:
    source = _ScriptedEventSource(
        [
            (False, [_stored(0, TextDelta(text="안녕"))]),
            (
                True,
                [_stored(0, TextDelta(text="안녕")), _stored(1, Done(stop_reason="end_turn"))],
            ),
        ]
    )

    frames = _run(source)

    assert frames[0].startswith("id: 0\nevent: assistant\ndata: ")
    assert '"type":"text_delta"' in frames[0]
    assert frames[0].endswith("\n\n")
    assert frames[1].startswith("id: 1\nevent: assistant\ndata: ")
    assert '"stop_reason":"end_turn"' in frames[1]
    assert len(frames) == 2


def test_the_last_event_stored_as_the_turn_ends_is_not_dropped() -> None:
    """종료 여부를 먼저 읽는 순서가 지켜지는지 보는 회귀 테스트.

    두 번째 단계에서 턴은 이미 슬롯을 풀었지만 sequence 1 이벤트는 그 조회 뒤에야 보인다.
    이벤트를 먼저 읽고 종료를 나중에 읽으면 이 프레임이 영영 전송되지 않는다.
    """
    source = _ScriptedEventSource(
        [
            (False, []),
            (
                True,
                [_stored(0, TextDelta(text="마지막")), _stored(1, Done(stop_reason="end_turn"))],
            ),
        ]
    )

    frames = _run(source)

    assert [frame.splitlines()[0] for frame in frames] == ["id: 0", "id: 1"]


def test_a_quiet_stream_sends_a_keepalive_comment() -> None:
    source = _ScriptedEventSource(
        [
            (False, []),
            (True, [_stored(0, Done(stop_reason="end_turn"))]),
        ]
    )

    frames = _run(source, keepalive_seconds=0.0)

    assert frames[0] == ": keepalive\n\n"
    assert frames[1].startswith("id: 0\n")


def test_events_of_an_earlier_turn_are_skipped_without_stalling_the_cursor() -> None:
    """앞선 턴의 이벤트는 다시 보내지 않지만 커서는 그 번호를 지나쳐야 한다.

    커서를 올리지 않으면 같은 행을 매 폴링마다 다시 읽어 스트림이 제자리를 돈다.
    """
    source = _ScriptedEventSource(
        [
            (
                True,
                [
                    _stored(0, TextDelta(text="이전 턴"), turn_id="turn-0"),
                    _stored(1, Done(stop_reason="end_turn")),
                ],
            )
        ]
    )

    frames = _run(source)

    assert [frame.splitlines()[0] for frame in frames] == ["id: 1"]
