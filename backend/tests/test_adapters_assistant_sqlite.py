"""`assistant_sqlite` 저장 adapter 계약 테스트 (설계 spec D5, A-03).

포트 계약의 기대 동작은 `tests/application/_assistant_fakes.py`의 인메모리 가짜가 이미 고정해
두었다. 여기서는 같은 계약을 SQLite 구현이 지키는지, 그리고 파일 저장소에서만 드러나는 사실
(재기동 후 보존, 부분 유니크 인덱스, 트랜잭션 안 sequence 부여, 이벤트 JSON 왕복)을 본다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import get_args

import pytest

from strategy_workbench.adapters.outbound.assistant_sqlite._event_codec import (
    decode_event,
    encode_event,
)
from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    AssistantStorageError,
    SQLiteChatSessionRepository,
    SQLiteProviderProfileRepository,
)
from strategy_workbench.application.assistant_chat.facade.chat import ChatSession, DocumentRef
from strategy_workbench.application.assistant_chat.facade.ports import (
    ChatSessionNotFoundError,
    ProviderProfileNotFoundError,
    TurnNotFoundError,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    ChatRole,
    Done,
    Failure,
    FailureCode,
    Proposal,
    ProposalCompileResult,
    ProposalDiagnostic,
    ProviderKind,
    ProviderProfile,
    SearchActivity,
    Source,
    StrategyProposal,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResultSummary,
    Turn,
    TurnStatus,
    Usage,
)

NOW = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)


@pytest.fixture
def database() -> Iterator[AssistantDatabase]:
    with AssistantDatabase() as opened:
        yield opened


def _profile(
    profile_id: str = "profile-1",
    *,
    kind: ProviderKind = ProviderKind.ANTHROPIC,
    active: bool = False,
    base_url: str | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        profile_id=profile_id,
        kind=kind,
        label=f"라벨 {profile_id}",
        model="claude-opus-5",
        base_url=base_url,
        created_at=NOW,
        active=active,
    )


def _session(
    session_id: str = "session-1",
    *,
    document_ref: DocumentRef | None = None,
) -> ChatSession:
    return ChatSession(
        session_id=session_id,
        document_ref=document_ref or DocumentRef(strategy_id=None, revision=None, draft_id="d-1"),
        provider_profile_id="profile-1",
        created_at=NOW,
        title="모멘텀 전략 상담",
    )


def _turn(turn_id: str = "turn-1", *, session_id: str = "session-1", accepted: int = -1) -> Turn:
    return Turn(
        turn_id=turn_id,
        session_id=session_id,
        status=TurnStatus.RUNNING,
        accepted_sequence=accepted,
        started_at=NOW,
        finished_at=None,
    )


def _event_samples() -> dict[type, ChatEvent]:
    """union 멤버 타입마다 왕복에 쓸 표본 하나.

    키 집합이 `get_args(ChatEvent)`와 같은지는 아래 테스트가 단언한다. 멤버가 늘면 `_every_event`가
    `KeyError`로 먼저 멈추고, 표본만 빠뜨리면 그 테스트가 어느 타입인지 이름으로 말한다.
    """
    proposal = StrategyProposal(
        title="KRX 모멘텀",
        summary="12개월 모멘텀 상위 20종목.",
        rationale="근거 문장 — 출처 인용 포함.",
        sources=(Source(title="논문", url="https://example.test/paper"),),
        source_text="schema_version: '1.1'\n",
        source_format="yaml",
        compile=ProposalCompileResult(
            ok=False,
            spec_hash=None,
            diagnostics=(
                ProposalDiagnostic(
                    code="strategy.factor.unknown",
                    pointer="/factors/0/factor_id",
                    message="알 수 없는 팩터 식별자",
                    severity="error",
                ),
            ),
        ),
    )
    samples: tuple[ChatEvent, ...] = (
        TextDelta(text="안녕하세요"),
        ThinkingSummary(text="요약된 사고"),
        ToolCall(call_id="call-1", name="validate_strategy_yaml", arguments={"source": "a: 1"}),
        ToolResultSummary(call_id="call-1", name="validate_strategy_yaml", ok=True, summary="통과"),
        SearchActivity(query="KRX 모멘텀", sources=(Source(title="기사", url="https://a.test/1"),)),
        Proposal(proposal=proposal),
        Usage(input_tokens=1200, output_tokens=340),
        Done(stop_reason="end_turn"),
        Failure(code=FailureCode.TOOL_ROUNDS_EXCEEDED, message="도구 호출이 너무 많습니다"),
    )
    return {type(event): event for event in samples}


def _every_event() -> tuple[ChatEvent, ...]:
    """`ChatEvent` union 전부를 union 선언 순서대로. 목록이 아니라 union이 내용을 정한다."""
    samples = _event_samples()
    return tuple(samples[member] for member in get_args(ChatEvent))


# -- 프로파일 저장소 -------------------------------------------------------------------------


def test_profiles_round_trip_in_registration_order(database: AssistantDatabase) -> None:
    repository = SQLiteProviderProfileRepository(database)

    repository.add(_profile("profile-b", base_url="https://gateway.test/v1"))
    repository.add(_profile("profile-a", kind=ProviderKind.OPENAI))

    assert [profile.profile_id for profile in repository.list()] == ["profile-b", "profile-a"]
    stored = repository.get("profile-b")
    assert stored == _profile("profile-b", base_url="https://gateway.test/v1")
    assert repository.get("profile-a").kind is ProviderKind.OPENAI


def test_missing_profile_is_a_port_error(database: AssistantDatabase) -> None:
    repository = SQLiteProviderProfileRepository(database)

    with pytest.raises(ProviderProfileNotFoundError):
        repository.get("absent")
    with pytest.raises(ProviderProfileNotFoundError):
        repository.delete("absent")
    with pytest.raises(ProviderProfileNotFoundError):
        repository.set_active("absent")


def test_set_active_leaves_exactly_one_active_profile(database: AssistantDatabase) -> None:
    repository = SQLiteProviderProfileRepository(database)
    repository.add(_profile("profile-a", active=True))
    repository.add(_profile("profile-b"))

    repository.set_active("profile-b")

    assert [profile.active for profile in repository.list()] == [False, True]


def test_a_second_active_profile_is_refused_by_the_partial_unique_index(
    database: AssistantDatabase,
) -> None:
    """활성 프로파일이 둘이 되는 상태는 저장소가 표현조차 못 하게 막는다."""
    repository = SQLiteProviderProfileRepository(database)
    repository.add(_profile("profile-a", active=True))

    with pytest.raises(AssistantStorageError):
        repository.add(_profile("profile-b", active=True))

    assert [profile.profile_id for profile in repository.list()] == ["profile-a"]


def test_profiles_survive_reopening_the_database_file(tmp_path: Path) -> None:
    path = tmp_path / "assistant.sqlite3"
    with AssistantDatabase(path) as first:
        SQLiteProviderProfileRepository(first).add(_profile("profile-a", active=True))

    with AssistantDatabase(path) as second:
        restored = SQLiteProviderProfileRepository(second).list()

    assert [profile.profile_id for profile in restored] == ["profile-a"]
    assert restored[0].active is True


def test_a_foreign_sqlite_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id TEXT)")

    with pytest.raises(AssistantStorageError):
        AssistantDatabase(path)


# -- 세션·메시지 ------------------------------------------------------------------------------


def test_sessions_round_trip_and_list_by_document_reference(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    draft_ref = DocumentRef(strategy_id=None, revision=None, draft_id="d-1")
    saved_ref = DocumentRef(strategy_id="s-1", revision=3, draft_id=None)
    repository.create(_session("session-draft", document_ref=draft_ref))
    repository.create(_session("session-saved", document_ref=saved_ref))

    assert repository.get("session-draft") == _session("session-draft", document_ref=draft_ref)
    assert [item.session_id for item in repository.list_for_document(draft_ref)] == [
        "session-draft"
    ]
    assert [item.session_id for item in repository.list_for_document(saved_ref)] == [
        "session-saved"
    ]


def test_messages_keep_conversation_order(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.append_message("session-1", ChatMessage(ChatRole.USER, "질문", NOW))
    repository.append_message("session-1", ChatMessage(ChatRole.ASSISTANT, "답변", NOW))

    stored = repository.messages("session-1")

    assert [(message.role, message.text) for message in stored] == [
        (ChatRole.USER, "질문"),
        (ChatRole.ASSISTANT, "답변"),
    ]


def test_missing_session_is_a_port_error(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)

    with pytest.raises(ChatSessionNotFoundError):
        repository.get("absent")
    with pytest.raises(ChatSessionNotFoundError):
        repository.append_message("absent", ChatMessage(ChatRole.USER, "질문", NOW))
    with pytest.raises(ChatSessionNotFoundError):
        repository.messages("absent")
    with pytest.raises(ChatSessionNotFoundError):
        repository.last_sequence("absent")
    with pytest.raises(ChatSessionNotFoundError):
        repository.events("absent")


# -- 턴 ---------------------------------------------------------------------------------------


def test_turns_round_trip_and_record_their_final_state(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn(accepted=4))

    finished = Turn(
        turn_id="turn-1",
        session_id="session-1",
        status=TurnStatus.COMPLETED,
        accepted_sequence=4,
        started_at=NOW,
        finished_at=datetime(2026, 9, 20, 9, 35, tzinfo=UTC),
    )
    assert repository.update_turn(finished) == finished
    assert repository.get_turn("turn-1") == finished


def test_a_cancelled_turn_may_still_have_no_finish_time(database: AssistantDatabase) -> None:
    """러너는 취소를 먼저 기록하고 종료 시각은 스레드가 끝날 때 채운다(`_turns.py`의 `cancel`)."""
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn())

    cancelled = repository.update_turn(
        Turn(
            turn_id="turn-1",
            session_id="session-1",
            status=TurnStatus.CANCELLED,
            accepted_sequence=-1,
            started_at=NOW,
            finished_at=None,
        )
    )

    assert cancelled.status is TurnStatus.CANCELLED
    assert cancelled.finished_at is None


def test_missing_turn_is_a_port_error(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())

    with pytest.raises(TurnNotFoundError):
        repository.get_turn("absent")
    with pytest.raises(TurnNotFoundError):
        repository.update_turn(_turn("absent"))
    with pytest.raises(TurnNotFoundError):
        repository.append_events("absent", (Done(stop_reason="end_turn"),))


def test_a_turn_for_an_unknown_session_is_refused(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)

    with pytest.raises(ChatSessionNotFoundError):
        repository.create_turn(_turn(session_id="absent"))


# -- 이벤트 -----------------------------------------------------------------------------------


def test_append_events_numbers_the_session_monotonically(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn("turn-1"))
    repository.create_turn(_turn("turn-2"))

    first = repository.append_events("turn-1", (TextDelta(text="가"), TextDelta(text="나")))
    second = repository.append_events("turn-2", (Done(stop_reason="end_turn"),))

    assert first == (0, 1)
    assert second == (2,)
    assert repository.last_sequence("session-1") == 2


def test_sequences_do_not_collide_across_sessions(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session("session-1"))
    repository.create(_session("session-2"))
    repository.create_turn(_turn("turn-1", session_id="session-1"))
    repository.create_turn(_turn("turn-2", session_id="session-2"))

    repository.append_events("turn-1", (TextDelta(text="가"),))

    assert repository.append_events("turn-2", (TextDelta(text="나"),)) == (0,)
    assert repository.last_sequence("session-2") == 0


def test_last_sequence_is_minus_one_before_any_event(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())

    assert repository.last_sequence("session-1") == -1


def test_events_after_sequence_are_ordered(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn())
    repository.append_events(
        "turn-1", (TextDelta(text="가"), TextDelta(text="나"), Done(stop_reason="end_turn"))
    )

    resumed = repository.events("session-1", after_sequence=0)

    assert [item.sequence for item in resumed] == [1, 2]
    assert [item.turn_id for item in resumed] == ["turn-1", "turn-1"]
    assert resumed[-1].event == Done(stop_reason="end_turn")


def test_every_chat_event_round_trips_through_storage(database: AssistantDatabase) -> None:
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn())
    events = _every_event()

    repository.append_events("turn-1", events)

    assert tuple(item.event for item in repository.events("session-1")) == events


def test_an_unknown_event_tag_in_the_file_is_a_storage_error(tmp_path: Path) -> None:
    """손상·다운그레이드된 행을 조용히 건너뛰면 이력에 구멍이 생긴다. 읽기에서 멈춘다."""
    path = tmp_path / "assistant.sqlite3"
    with AssistantDatabase(path) as database:
        repository = SQLiteChatSessionRepository(database)
        repository.create(_session())
        repository.create_turn(_turn())
        repository.append_events("turn-1", (TextDelta(text="가"),))

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE chat_events SET event_type = 'telepathy'")

    with AssistantDatabase(path) as reopened:
        with pytest.raises(AssistantStorageError):
            SQLiteChatSessionRepository(reopened).events("session-1")


def test_concurrent_appends_on_one_file_produce_disjoint_sequences(tmp_path: Path) -> None:
    """서로 다른 연결 두 개가 같은 세션에 동시에 붙여도 번호가 겹치거나 비지 않는다.

    `BEGIN IMMEDIATE`와 `(session_id, sequence)` 기본키가 함께 집행한다. 겹치면 재개 클라이언트가
    한 이벤트를 두 번 반영하고, 비면 영영 오지 않는 번호를 기다린다.
    """
    path = tmp_path / "assistant.sqlite3"
    with AssistantDatabase(path) as setup:
        repository = SQLiteChatSessionRepository(setup)
        repository.create(_session())
        repository.create_turn(_turn("turn-1"))
        repository.create_turn(_turn("turn-2"))

    barrier = Barrier(2)

    def append(turn_id: str) -> tuple[int, ...]:
        with AssistantDatabase(path) as database:
            worker = SQLiteChatSessionRepository(database)
            barrier.wait(timeout=10)
            return worker.append_events(turn_id, (TextDelta(text=turn_id), Done(stop_reason="x")))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(append, "turn-1"), pool.submit(append, "turn-2")]
        assigned = sorted(sequence for future in futures for sequence in future.result())

    assert assigned == [0, 1, 2, 3]
    with AssistantDatabase(path) as reopened:
        stored = SQLiteChatSessionRepository(reopened).events("session-1")
    assert [item.sequence for item in stored] == [0, 1, 2, 3]


def test_a_document_reference_row_cannot_name_both_a_strategy_and_a_draft(tmp_path: Path) -> None:
    """세션 행의 불변식은 `DocumentRef`와 같아야 한다.

    DB가 둘 다 담은 행을 허용하면 읽을 때 `DocumentRef` 생성이 `ValueError`로 터져, 포트가
    약속하지 않은 예외가 호출부로 샌다.
    """
    path = tmp_path / "assistant.sqlite3"
    with AssistantDatabase(path) as database:
        SQLiteChatSessionRepository(database).create(_session())

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, ordinal, strategy_id, revision, draft_id,
                    provider_profile_id, created_at, title
                ) VALUES ('both', 1, 's-1', 3, 'd-1', 'profile-1', '2026-09-20T00:00:00+00:00', 't')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, ordinal, strategy_id, revision, draft_id,
                    provider_profile_id, created_at, title
                ) VALUES ('draft-rev', 2, NULL, 3, 'd-1', 'profile-1',
                          '2026-09-20T00:00:00+00:00', 't')
                """
            )


def test_every_chat_event_union_member_has_a_round_trip_sample() -> None:
    """표본 목록이 union과 어긋나면 여기서 먼저 깨진다.

    인코딩 쪽 누락은 `assert_never`가 타입 단계에서 잡지만, 디코딩은 태그 문자열 → 타입이라
    타입 체커가 볼 수 없다. 그 구멍을 이 단언이 막는다.
    """
    covered = set(_event_samples())

    assert covered == set(get_args(ChatEvent)), (
        "ChatEvent union 멤버와 왕복 표본이 다르다 — "
        f"표본 없음={sorted(member.__name__ for member in set(get_args(ChatEvent)) - covered)} "
        f"union 밖={sorted(member.__name__ for member in covered - set(get_args(ChatEvent)))}"
    )


def test_every_union_member_decodes_from_its_own_stored_tag() -> None:
    """태그는 멤버마다 달라야 한다. 두 멤버가 한 태그를 쓰면 되돌릴 때 한쪽이 다른 쪽이 된다."""
    encoded = {type(event): encode_event(event) for event in _every_event()}

    tags = [tag for tag, _payload in encoded.values()]
    assert len(set(tags)) == len(tags), f"태그가 겹친다 — tags={sorted(tags)}"
    for member, (tag, payload) in encoded.items():
        decoded = decode_event(tag, payload)
        assert type(decoded) is member
        assert decoded == _event_samples()[member]


def test_every_failure_code_round_trips() -> None:
    """`FailureCode`가 늘어도 저장·복원이 따라온다. 모르는 코드는 조용히 넘기지 않고 멈춘다."""
    for code in FailureCode:
        tag, payload = encode_event(Failure(code=code, message="사유 문장"))

        assert decode_event(tag, payload) == Failure(code=code, message="사유 문장")

    with pytest.raises(AssistantStorageError):
        decode_event("failure", '{"code":"telepathy","message":"x"}')


def test_update_turn_does_not_move_the_resume_anchor_or_the_start_time(
    database: AssistantDatabase,
) -> None:
    """`accepted_sequence`·`started_at`은 턴의 정체다. 갱신이 이 둘을 건드리면 재개가 깨진다.

    `-1`로 덮이면 재접속 클라이언트가 세션을 처음부터 다시 받고, 더 큰 값으로 덮이면 그 사이
    이벤트를 영영 못 본다. `started_at`이 덮이면 사이드바의 턴 순서가 뒤집힌다.
    """
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.create_turn(_turn(accepted=7))
    finished_at = datetime(2026, 9, 20, 9, 35, tzinfo=UTC)

    returned = repository.update_turn(
        Turn(
            turn_id="turn-1",
            session_id="session-1",
            status=TurnStatus.COMPLETED,
            accepted_sequence=-1,
            started_at=datetime(1999, 1, 1, tzinfo=UTC),
            finished_at=finished_at,
        )
    )

    stored = repository.get_turn("turn-1")
    assert stored.accepted_sequence == 7
    assert stored.started_at == NOW
    assert stored.status is TurnStatus.COMPLETED
    assert stored.finished_at == finished_at
    assert returned == stored, "돌려주는 값은 인자가 아니라 저장된 행이어야 한다"


def test_the_strategy_revision_database_is_refused(tmp_path: Path) -> None:
    """두 DB를 서로 열지 않게 하는 방어의 핵심 가지다.

    `_APPLICATION_ID`가 나중에 전략 쪽 값과 겹치게 바뀌면 여기서 깨진다.
    """
    path = tmp_path / "strategy-revisions.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id = 0x5357524B")  # "SWRK" — strategy_sqlite
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(AssistantStorageError, match="another application"):
        AssistantDatabase(path)


def test_a_non_utc_aware_timestamp_is_stored_as_utc(database: AssistantDatabase) -> None:
    """offset이 0이 아닌 aware 값도 손실 없이 UTC로 옮겨진다. 거부하는 것은 naive뿐이다."""
    seoul = timezone(timedelta(hours=9))
    repository = SQLiteChatSessionRepository(database)
    repository.create(_session())
    repository.append_message(
        "session-1",
        ChatMessage(ChatRole.USER, "질문", datetime(2026, 9, 20, 18, 30, tzinfo=seoul)),
    )

    assert repository.messages("session-1")[0].created_at == NOW

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.append_message(
            "session-1", ChatMessage(ChatRole.USER, "질문", datetime(2026, 9, 20, 9, 30))
        )
