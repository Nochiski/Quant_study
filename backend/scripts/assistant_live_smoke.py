"""실제 키로 공급자 SDK 표면을 한 번 확인하는 smoke (WORKFLOW A-07, 설계 spec D4).

## 왜 필요한가

adapter 단위 테스트는 SDK **타입**으로 만든 대본을 읽는다. 타입이 맞아도 런타임이 다르게 구는
표면이 몇 군데 있고(예: thinking 블록의 `signature`를 수동 루프에서 되돌려 보냈을 때 공급자가
받아 주는가), 그건 진짜 호출 한 번으로만 알 수 있다. 그 한 번을 CI에 넣지 않으려고 스크립트로
떼어 둔다.

확인 항목은 A-05·A-06 구현자가 자기 PR에서 "live smoke로만 판정된다"고 남긴 목록이며, 우선순위
순으로 `CHECKLIST`에 있다.

## 실행

`backend/`에서, 키는 환경 변수로만 준다(명령줄 인자로 주면 셸 이력에 남는다).

    STRATEGY_WORKBENCH_LIVE_SMOKE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... \\
        uv run python scripts/assistant_live_smoke.py

`STRATEGY_WORKBENCH_LIVE_SMOKE=1`이 없으면 아무 것도 하지 않고 끝난다. 키가 없는 공급자는
건너뛴다. 설치되지 않은 공급자(optional extra `llm` 미설치)도 건너뛴다.

## 무엇을 부르는가

공급자마다 `probe` 3회(정상 키·틀린 키·없는 모델)와 턴 1회다. probe를 세 번 부르는 이유는 설정
화면의 "연결 테스트"가 사유를 **구분해서** 보여 주기 때문이다. 정상 키가 `ok=True`인 것만 보면
`AUTH`와 `UNKNOWN`이 뒤바뀐 매핑을 놓친다. 세 번 다 최소 출력 토큰 요청이라 비용은 무시할 수준이다.

## 기대 출력

공급자마다 한 블록이 나온다. probe 3종의 결과, 턴이 흘린 이벤트 종류의 순서, 호출별 출력 토큰과
턴 합계, 그리고 확인 항목 목록이다. 확인 항목의 표시는 세 가지다.

- `[ok]`    이번 실행이 그 표면을 실제로 관측했다.
- `[fail]`  관측했는데 기대와 달랐다. adapter 결함이다.
- `[?]`     이번 실행으로는 판정할 수 없다(모델이 그 경로를 타지 않았다). 다시 돌리거나 질문을
            바꿔서 그 경로를 태운다.

종료 코드는 `[fail]`이 하나라도 있으면 1, 아니면 0이다. `[?]`는 실패가 아니다 — 모델 행동은
우리가 정하지 못하므로, 관측되지 않은 것을 실패로 세면 이 스크립트가 무작위로 빨개진다.

`turn_budget_measurements` 항목이 찍는 숫자(도구 라운드 수, 호출별 `usage.output_tokens`, 턴
합계)는 PLAN "A-07 기본값 확정 근거"에 그대로 옮겨 적는다. 그 표의 토큰 수는 지금 추정치이고,
이 실행이 실측으로 바꿔 준다.

## 진단이 로그에만 보이는 경우

`Failure.message`에는 예외 타입 이름조차 넣지 않는 코드가 있다(spec D2: SDK 예외 본문에 키
조각이 섞여 올 수 있다). 그래서 thinking signature 거부 같은 400은 화면에 `Failure(PROVIDER)`로만
보인다. 이 스크립트는 `strategy_workbench` 로거의 WARNING을 모아 블록 끝에 함께 찍는다 — 거기
`error_type=BadRequestError`가 있으면 signature 왕복이 깨진 것이다.

## 비밀

키는 읽어서 `probe`·`ProviderProfileService.create`에 넘기는 것 외에 어디에도 쓰지 않는다.
출력·로그에 찍지 않고, 예외도 타입 이름만 적는다. 비밀 파일과 대화 DB는 임시 디렉터리에 만들고
끝나면 지운다 — 사용자의 실제 설정 디렉터리를 건드리지 않는다.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from strategy_workbench.application.assistant_chat.facade.chat import DocumentRef, TurnContext
from strategy_workbench.application.assistant_chat.facade.ports import LlmProviderPort
from strategy_workbench.bootstrap.facade.container import (
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
    build_container,
)
from strategy_workbench.domain.assistant.facade.models import (
    DEFAULT_MAX_SEARCH_USES,
    ChatEvent,
    Failure,
    ProbeFailure,
    Proposal,
    ProviderKind,
    SearchActivity,
    ThinkingSummary,
    ToolResultSummary,
    Usage,
)

__all__ = [
    "CHECKLIST",
    "LIVE_SMOKE_ENV",
    "SECRET_ENV",
    "CheckItem",
    "CheckVerdict",
    "ProbeEvidence",
    "ProviderSmoke",
    "SmokePlan",
    "SmokeStatus",
    "TurnEvidence",
    "main",
    "plan_smoke",
    "verdicts_for",
]

LIVE_SMOKE_ENV = "STRATEGY_WORKBENCH_LIVE_SMOKE"

# 공급자별 키 환경 변수. 각 SDK가 스스로 읽는 이름과 같게 둬서, 이미 그 변수를 쓰던 개발 환경이
# 그대로 통한다. 우리는 값을 읽어 프로파일에 넘길 뿐 SDK의 암묵 읽기에 기대지 않는다.
SECRET_ENV: Mapping[ProviderKind, str] = {
    ProviderKind.ANTHROPIC: "ANTHROPIC_API_KEY",
    ProviderKind.OPENAI: "OPENAI_API_KEY",
}

# probe의 사유 구분을 보려고 일부러 틀리게 주는 값. 형식만 그럴듯한 가짜이며 어느 공급자에서도
# 유효한 키가 아니다.
_WRONG_SECRET = "sk-live-smoke-deliberately-invalid"
_UNKNOWN_MODEL = "live-smoke-no-such-model-20260101"

# 한 턴에 도구 호출·웹 검색·제안을 모두 태우려고 고른 질문. 모델 행동을 강제할 수는 없으므로
# 판정은 관측 기반이다(`[?]`가 나오면 이 문장을 바꾼다).
_USER_MESSAGE = (
    "지금 열려 있는 전략 문서를 먼저 읽고, 쓸 수 있는 데이터 필드 카탈로그를 확인해 줘. "
    "그다음 최근 한국 주식시장의 퀄리티·모멘텀 팩터 성과에 대한 최신 자료를 웹에서 "
    "여러 번 찾아보고, 그 근거를 반영해 이 문서를 개선한 전략을 제안해 줘."
)

# 현재 문서로 실을 원문. 추적되는 골든 문서를 그대로 쓴다 — 여기서 YAML을 손으로 적으면 전략
# 언어의 필드 이름이 스크립트에 복제되어 스키마가 바뀔 때 조용히 stale해진다.
_CURRENT_DOCUMENT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "strategy_documents"
    / "quality_momentum.yaml"
)

_DIAGNOSTIC_LOGGER = "strategy_workbench"


class SmokeStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class CheckItem:
    """live smoke로만 확인할 수 있는 SDK 표면 하나.

    `key`는 출력·테스트가 참조하는 안정 식별자고, `question`은 사람이 읽는 문장이다.
    """

    key: str
    question: str


# A-05·A-06 구현자가 남긴 확인 목록을 우선순위 순으로 옮긴 것이다.
#
# `output_format=None` 항목이 여기 없는 이유: A-05 리뷰에서 로컬 재현 가능한 SDK 센티널 오류로
# 판정되어 adapter에서 고쳐졌다. 로컬에서 재현되는 것을 live smoke에 두면 키가 있어야만 도는
# 항목이 늘어날 뿐 잡는 것은 늘지 않는다.
#
# probe 항목이 토큰 수를 문장에 적지 않는 이유: 값은 adapter 상수가 소유하고 A-05·A-06이 그 값을
# 올리는 중이다. 숫자를 여기 복제하면 상수가 바뀌는 날 이 문장만 stale해진다.
CHECKLIST: Mapping[ProviderKind, tuple[CheckItem, ...]] = {
    ProviderKind.ANTHROPIC: (
        CheckItem(
            "thinking_signature_roundtrip",
            "thinking 블록의 signature가 수동 루프의 assistant 턴 재전송에서 살아남는가",
        ),
        CheckItem(
            "probe_reason_mapping",
            "probe(`_adapter.py`의 `PROBE_MAX_TOKENS`)가 정상 키 ok, 틀린 키 auth, "
            "없는 모델 model_not_found로 사유를 구분하는가",
        ),
        CheckItem(
            "thinking_display_summarized",
            '`display: "summarized"`가 실제로 thinking 텍스트를 채우는가',
        ),
        CheckItem(
            "search_result_fields",
            "검색 활동의 질의와 출처 제목·URL이 실제 응답에서 채워지는가",
        ),
        CheckItem(
            "search_budget_turn_continues",
            "턴 누적 검색 상한에 닿은 뒤에도 턴이 검색 없이 이어지는가",
        ),
        CheckItem(
            "turn_budget_measurements",
            "제안 1건이 나오는 보통 턴의 라운드 수와 호출별·턴 합계 출력 토큰은 얼마인가",
        ),
    ),
    ProviderKind.OPENAI: (
        CheckItem(
            "search_budget_notice_reaction",
            "검색 상한 통지(`developer` 메시지)를 받은 뒤 모델이 검색을 그만두는가",
        ),
        CheckItem(
            "reasoning_item_roundtrip",
            "추론 항목이 다음 호출 재전송에서 거부되지 않는가",
        ),
        CheckItem(
            "probe_reason_mapping",
            "probe(adapter의 최소 `max_output_tokens` 상수)가 정상 키 ok, 틀린 키 auth, "
            "없는 모델 model_not_found로 사유를 구분하는가",
        ),
        CheckItem(
            "default_model_exists",
            "adapter의 기본 모델 이름이 실제로 존재하는가",
        ),
        CheckItem(
            "turn_budget_measurements",
            "제안 1건이 나오는 보통 턴의 라운드 수와 호출별·턴 합계 출력 토큰은 얼마인가",
        ),
    ),
}


@dataclass(frozen=True)
class ProbeEvidence:
    """probe 3회의 관측. 설정 화면이 사유를 구분해 보여 주는 계약을 여기서 확인한다."""

    model: str
    ok: bool
    latency_ms: int | None
    wrong_key_failure: str | None
    unknown_model_failure: str | None


@dataclass(frozen=True)
class TurnEvidence:
    """한 턴이 남긴 관측. 확인 항목의 판정은 probe 관측과 이 값만 본다."""

    stream_raised: bool
    event_kinds: tuple[str, ...]
    tool_rounds: int
    search_limit: int
    search_activities: int
    search_with_sources: int
    search_with_query: int
    events_after_last_search: int
    thinking_with_text: int
    call_output_tokens: tuple[int, ...]
    turn_input_tokens: int
    turn_output_tokens: int
    proposal_accepted: bool
    failure_code: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CheckVerdict:
    """확인 항목 하나의 결과. `observed is None`이면 이번 실행으로 판정 불가다."""

    item: CheckItem
    observed: bool | None
    note: str

    @property
    def marker(self) -> str:
        if self.observed is None:
            return "[?]"
        return "[ok]" if self.observed else "[fail]"


@dataclass(frozen=True)
class ProviderSmoke:
    kind: ProviderKind
    status: SmokeStatus
    detail: str
    probe: ProbeEvidence | None = None
    turn: TurnEvidence | None = None
    verdicts: tuple[CheckVerdict, ...] = ()


@dataclass(frozen=True)
class SmokePlan:
    """무엇을 돌리고 무엇을 왜 건너뛰는지. 네트워크를 타기 전에 결정된다."""

    enabled: bool
    runnable: tuple[ProviderKind, ...]
    skipped: tuple[tuple[ProviderKind, str], ...]

    @property
    def reason(self) -> str:
        if not self.enabled:
            return f"{LIVE_SMOKE_ENV}=1이 아니다"
        if not self.runnable:
            return "실행할 수 있는 공급자가 없다"
        return ""


def plan_smoke(
    environment: Mapping[str, str],
    installed: frozenset[ProviderKind] | None = None,
) -> SmokePlan:
    """환경만 보고 계획을 세운다. 순수 함수이므로 네트워크 없이 테스트할 수 있다."""
    available = frozenset(PROVIDER_ADAPTER_FACTORIES) if installed is None else frozenset(installed)
    runnable: list[ProviderKind] = []
    skipped: list[tuple[ProviderKind, str]] = []
    for kind, variable in SECRET_ENV.items():
        if kind not in available:
            skipped.append((kind, "adapter 미설치 — optional extra `llm`을 설치하면 등록된다"))
            continue
        if not environment.get(variable, "").strip():
            skipped.append((kind, f"{variable}가 비어 있다"))
            continue
        runnable.append(kind)
    return SmokePlan(
        enabled=environment.get(LIVE_SMOKE_ENV, "").strip() == "1",
        runnable=tuple(runnable),
        skipped=tuple(skipped),
    )


def verdicts_for(
    kind: ProviderKind, probe: ProbeEvidence, turn: TurnEvidence
) -> tuple[CheckVerdict, ...]:
    """관측을 확인 항목에 건다. 판정 규칙이 한곳에 모여 있어야 출력과 테스트가 같은 말을 한다."""
    rules = {
        **_roundtrip_rules(turn),
        **_probe_rules(probe),
        **_search_rules(turn),
        "thinking_display_summarized": (
            True if turn.thinking_with_text else None,
            f"비어 있지 않은 사고 요약 {turn.thinking_with_text}건"
            if turn.thinking_with_text
            else "사고 요약 이벤트가 없었다 — 모델이 생각을 내보내지 않았거나 "
            "display 설정이 죽었다",
        ),
        "turn_budget_measurements": (
            True if turn.call_output_tokens else None,
            f"도구 라운드={turn.tool_rounds} 공급자 호출={len(turn.call_output_tokens)} "
            f"호출별 출력 토큰={list(turn.call_output_tokens)} "
            f"턴 합계 입력={turn.turn_input_tokens} 출력={turn.turn_output_tokens} "
            f"제안 접수={turn.proposal_accepted} — PLAN 기본값 근거 표에 옮겨 적는다"
            if turn.call_output_tokens
            else "사용량 이벤트가 없어 실측할 것이 없었다",
        ),
    }
    return tuple(
        CheckVerdict(item=item, observed=rules[item.key][0], note=rules[item.key][1])
        for item in CHECKLIST[kind]
    )


def _roundtrip_rules(turn: TurnEvidence) -> Mapping[str, tuple[bool | None, str]]:
    """사고 블록(thinking signature·추론 항목) 재전송 판정.

    도구를 한 번이라도 부르면 공급자 호출이 두 번 이상이고, 두 번째 호출이 첫 응답의 사고 블록을
    그대로 되돌려 보낸다. 거기서 거부되면 `Failure(PROVIDER)`가 난다 — 그 실패가 이 항목의
    증상이다. 사유는 `Failure.message`에 없고 로컬 로그의 `error_type`에만 있다(spec D2).
    """
    if turn.tool_rounds < 1 or turn.stream_raised:
        verdict: tuple[bool | None, str] = (
            None,
            f"도구 라운드가 {turn.tool_rounds}회라 재전송 경로를 타지 않았다"
            if not turn.stream_raised
            else "턴이 예외로 끝나 재전송 경로를 판정할 수 없다",
        )
    elif turn.failure_code == "provider":
        verdict = (
            False,
            "재전송 뒤 공급자 실패가 났다 — 아래 경고의 error_type이 BadRequestError면 "
            "사고 블록이 거부된 것이다",
        )
    else:
        verdict = (
            True,
            f"도구 라운드 {turn.tool_rounds}회를 공급자 실패 없이 완주",
        )
    return {"thinking_signature_roundtrip": verdict, "reasoning_item_roundtrip": verdict}


def _probe_rules(probe: ProbeEvidence) -> Mapping[str, tuple[bool | None, str]]:
    mapped = (
        probe.ok
        and probe.wrong_key_failure == ProbeFailure.AUTH.value
        and probe.unknown_model_failure == ProbeFailure.MODEL_NOT_FOUND.value
    )
    note = (
        f"정상 키 ok={probe.ok}({probe.latency_ms}ms) "
        f"틀린 키={probe.wrong_key_failure} 없는 모델={probe.unknown_model_failure}"
    )
    return {
        "probe_reason_mapping": (mapped, note),
        "default_model_exists": (
            probe.ok,
            f"기본 모델 {probe.model!r}로 probe {'성공' if probe.ok else '실패'}",
        ),
    }


def _search_rules(turn: TurnEvidence) -> Mapping[str, tuple[bool | None, str]]:
    if not turn.search_activities:
        missing: tuple[bool | None, str] = (None, "검색을 한 번도 하지 않았다")
        return {
            "search_result_fields": missing,
            "search_budget_turn_continues": missing,
            "search_budget_notice_reaction": missing,
        }
    fields_ok = (
        turn.search_with_sources == turn.search_activities
        and turn.search_with_query == turn.search_activities
    )
    reached_limit = turn.search_activities >= turn.search_limit
    continued = (
        (turn.events_after_last_search > 0 and turn.failure_code is None) if reached_limit else None
    )
    limit_note = (
        f"검색 {turn.search_activities}회로 상한 {turn.search_limit}에 닿았고 "
        f"이후 이벤트 {turn.events_after_last_search}건, 실패={turn.failure_code or '없음'}"
        if reached_limit
        else f"검색 {turn.search_activities}회로 상한 {turn.search_limit}에 닿지 않아 "
        "상한 뒤 동작을 보지 못했다"
    )
    return {
        "search_result_fields": (
            fields_ok,
            f"검색 {turn.search_activities}회 중 질의가 실린 것 {turn.search_with_query}회, "
            f"출처가 실린 것 {turn.search_with_sources}회",
        ),
        "search_budget_turn_continues": (continued, limit_note),
        "search_budget_notice_reaction": (continued, limit_note),
    }


@contextmanager
def _captured_warnings() -> Iterator[list[str]]:
    """우리 로거의 WARNING만 모은다. 키가 새지 않는 것은 adapter·서비스가 이미 보장한다."""
    collected: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collected.append(record.getMessage())

    handler = _Collector(level=logging.WARNING)
    logger = logging.getLogger(_DIAGNOSTIC_LOGGER)
    logger.addHandler(handler)
    try:
        yield collected
    finally:
        logger.removeHandler(handler)


def _probe_matrix(adapter: LlmProviderPort, secret: str) -> ProbeEvidence:
    """정상 키·틀린 키·없는 모델 세 번. 사유 매핑이 뒤바뀌면 여기서 드러난다."""
    model = adapter.default_model()
    healthy = adapter.probe(secret, model=model, base_url=None)
    wrong_key = adapter.probe(_WRONG_SECRET, model=model, base_url=None)
    unknown_model = adapter.probe(secret, model=_UNKNOWN_MODEL, base_url=None)
    return ProbeEvidence(
        model=model,
        ok=healthy.ok,
        latency_ms=healthy.latency_ms,
        wrong_key_failure=None if wrong_key.ok else _failure_value(wrong_key.failure),
        unknown_model_failure=None if unknown_model.ok else _failure_value(unknown_model.failure),
    )


def _failure_value(failure: ProbeFailure | None) -> str:
    return failure.value if failure is not None else ProbeFailure.UNKNOWN.value


def _turn_evidence(
    events: Sequence[ChatEvent], *, stream_raised: bool, warnings: Sequence[str]
) -> TurnEvidence:
    failure = next((event for event in events if isinstance(event, Failure)), None)
    searches = [event for event in events if isinstance(event, SearchActivity)]
    usages = [event for event in events if isinstance(event, Usage)]
    last_search = max(
        (index for index, event in enumerate(events) if isinstance(event, SearchActivity)),
        default=-1,
    )
    return TurnEvidence(
        stream_raised=stream_raised,
        event_kinds=tuple(type(event).__name__ for event in events),
        tool_rounds=sum(1 for event in events if isinstance(event, ToolResultSummary)),
        search_limit=DEFAULT_MAX_SEARCH_USES,
        search_activities=len(searches),
        search_with_sources=sum(1 for event in searches if event.sources),
        search_with_query=sum(1 for event in searches if event.query.strip()),
        events_after_last_search=(len(events) - 1 - last_search) if last_search >= 0 else 0,
        thinking_with_text=sum(
            1 for event in events if isinstance(event, ThinkingSummary) and event.text.strip()
        ),
        call_output_tokens=tuple(usage.output_tokens for usage in usages),
        turn_input_tokens=sum(usage.input_tokens for usage in usages),
        turn_output_tokens=sum(usage.output_tokens for usage in usages),
        proposal_accepted=any(isinstance(event, Proposal) for event in events),
        failure_code=failure.code.value if failure is not None else None,
        warnings=tuple(warnings),
    )


def _run_provider(kind: ProviderKind, secret: str, workspace: Path) -> ProviderSmoke:
    """한 공급자에 대해 probe 3회 → 짧은 턴 → 제안까지 한 번 돌린다."""
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=workspace / f"{kind.value}-secrets.json",
        )
    )
    with _captured_warnings() as warnings:
        try:
            probe = _probe_matrix(PROVIDER_ADAPTER_FACTORIES[kind](), secret)
            profile = container.assistant_profiles.create(
                kind=kind, label=f"live-smoke-{kind.value}", secret=secret
            )
        except Exception as error:
            return ProviderSmoke(
                kind=kind,
                status=SmokeStatus.FAILED,
                detail=f"프로파일 준비가 실패했다 — error_type={type(error).__name__}",
            )
        del profile  # 활성 프로파일은 저장소에 남고, 여기서는 더 볼 것이 없다.
        session = container.assistant_chat.create_session(
            DocumentRef(strategy_id=None, revision=None, draft_id="live-smoke"),
            title="live smoke",
        )
        context = TurnContext(
            source_text=_CURRENT_DOCUMENT.read_text(encoding="utf-8"),
            source_format="yaml",
            environment=None,
            diagnostics=(),
        )
        events: list[ChatEvent] = []
        stream_raised = False
        try:
            events.extend(container.assistant_chat.send(session.session_id, _USER_MESSAGE, context))
        except Exception as error:
            # 예외 본문에는 요청 헤더·본문 조각이 섞여 올 수 있다. 타입 이름만 남긴다(spec D2).
            stream_raised = True
            detail = f"턴이 예외로 끝났다 — error_type={type(error).__name__}"
        else:
            detail = f"이벤트 {len(events)}건"
        turn = _turn_evidence(events, stream_raised=stream_raised, warnings=warnings)
    verdicts = verdicts_for(kind, probe, turn)
    failed = any(verdict.observed is False for verdict in verdicts)
    return ProviderSmoke(
        kind=kind,
        status=SmokeStatus.FAILED if failed else SmokeStatus.OK,
        detail=detail,
        probe=probe,
        turn=turn,
        verdicts=verdicts,
    )


def _report(smoke: ProviderSmoke, write: Callable[[str], None]) -> None:
    write("")
    write(f"== {smoke.kind.value} — {smoke.status.value} ({smoke.detail})")
    if smoke.probe is not None:
        write(
            f"   probe: model={smoke.probe.model} ok={smoke.probe.ok} "
            f"latency_ms={smoke.probe.latency_ms} "
            f"wrong_key={smoke.probe.wrong_key_failure} "
            f"unknown_model={smoke.probe.unknown_model_failure}"
        )
    turn = smoke.turn
    if turn is not None:
        write(f"   이벤트: {' → '.join(turn.event_kinds) or '없음'}")
        write(
            f"   도구 라운드={turn.tool_rounds} 검색={turn.search_activities}/{turn.search_limit} "
            f"제안 접수={turn.proposal_accepted} 실패={turn.failure_code or '없음'}"
        )
        write(
            f"   출력 토큰: 호출별={list(turn.call_output_tokens)} "
            f"턴 합계 입력={turn.turn_input_tokens} 출력={turn.turn_output_tokens}"
        )
    for verdict in smoke.verdicts:
        write(f"   {verdict.marker} {verdict.item.question}")
        write(f"       {verdict.note}")
    if turn is not None and turn.warnings:
        write("   로컬 경고(화면에 안 보이는 진단):")
        for message in turn.warnings:
            write(f"       {message}")


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # 인자를 받지 않는다 — 키는 환경 변수로만 들어온다.
    plan = plan_smoke(dict(os.environ))
    write = sys.stdout.write

    def line(text: str) -> None:
        write(f"{text}\n")

    for kind, reason in plan.skipped:
        line(f"-- {kind.value} 건너뜀: {reason}")
    if not plan.enabled or not plan.runnable:
        line(f"live smoke를 돌리지 않았다: {plan.reason}")
        return 0

    workspace = Path(tempfile.mkdtemp(prefix="assistant-live-smoke-"))
    try:
        results = [
            _run_provider(kind, os.environ[SECRET_ENV[kind]], workspace) for kind in plan.runnable
        ]
    finally:
        # 비밀 파일이 임시 디렉터리에 남지 않게 한다. 실패해도 지운다.
        shutil.rmtree(workspace, ignore_errors=True)
    for result in results:
        _report(result, line)
    line("")
    failed = [result.kind.value for result in results if result.status is SmokeStatus.FAILED]
    line(f"실패한 공급자: {', '.join(failed) if failed else '없음'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
