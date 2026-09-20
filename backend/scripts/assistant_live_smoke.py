"""실제 키로 공급자 SDK 표면을 한 번 확인하는 smoke (WORKFLOW A-07, 설계 spec D4).

## 왜 필요한가

adapter 단위 테스트는 SDK **타입**으로 만든 대본을 읽는다. 타입이 맞아도 런타임이 다르게 구는
표면이 몇 군데 있고(예: thinking 블록의 `signature`를 수동 루프에서 되돌려 보냈을 때 공급자가
받아 주는가), 그건 진짜 호출 한 번으로만 알 수 있다. 그 한 번을 CI에 넣지 않으려고 스크립트로
떼어 둔다.

## 실행

`backend/`에서, 키는 환경 변수로만 준다(명령줄 인자로 주면 셸 이력에 남는다).

    STRATEGY_WORKBENCH_LIVE_SMOKE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... \\
        uv run python scripts/assistant_live_smoke.py

`STRATEGY_WORKBENCH_LIVE_SMOKE=1`이 없으면 아무 것도 하지 않고 끝난다. 키가 없는 공급자는
건너뛴다. 설치되지 않은 공급자(optional extra `llm` 미설치)도 건너뛴다.

## 기대 출력

공급자마다 한 블록이 나온다. `probe`의 지연 시간, 턴이 흘린 이벤트 종류의 순서, 제안 접수 여부,
그리고 확인 항목 목록이다. 확인 항목은 세 가지로 나온다.

- `[ok]`    이번 실행이 그 표면을 실제로 관측했다.
- `[fail]`  관측했는데 기대와 달랐다. adapter 결함이다.
- `[?]`     이번 실행으로는 판정할 수 없다(모델이 그 경로를 타지 않았다). 다시 돌리거나 질문을
            바꿔서 그 경로를 태운다.

종료 코드는 `[fail]`이 하나라도 있으면 1, 아니면 0이다. `[?]`는 실패가 아니다 — 모델 행동은
우리가 정하지 못하므로, 관측되지 않은 것을 실패로 세면 이 스크립트가 무작위로 빨개진다.

## 비밀

키는 읽어서 `ProviderProfileService.create`에 넘기는 것 외에 어디에도 쓰지 않는다. 출력·로그에
찍지 않고, 예외도 타입 이름만 적는다(spec D2: SDK 예외 본문에 키 조각이 섞여 올 수 있다).
비밀 파일과 대화 DB는 임시 디렉터리에 만들고 끝나면 지운다 — 사용자의 실제 설정 디렉터리를
건드리지 않는다.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from strategy_workbench.application.assistant_chat.facade.chat import DocumentRef, TurnContext
from strategy_workbench.bootstrap.facade.container import (
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
    build_container,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Failure,
    Proposal,
    ProviderKind,
    SearchActivity,
    ThinkingSummary,
    ToolResultSummary,
)

__all__ = [
    "CHECKLIST",
    "LIVE_SMOKE_ENV",
    "SECRET_ENV",
    "CheckItem",
    "CheckVerdict",
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

# 한 턴에 도구 호출·웹 검색·제안을 모두 태우려고 고른 질문. 모델 행동을 강제할 수는 없으므로
# 판정은 관측 기반이다(`[?]`가 나오면 이 문장을 바꾼다).
_USER_MESSAGE = (
    "지금 열려 있는 전략 문서를 먼저 읽고, 쓸 수 있는 데이터 필드 카탈로그를 확인해 줘. "
    "그다음 최근 한국 주식시장의 퀄리티·모멘텀 팩터 성과에 대한 최신 자료를 웹에서 한 번 "
    "찾아보고, 그 근거를 반영해 이 문서를 개선한 전략을 제안해 줘."
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


# A-05 PR 본문이 "A-07 live smoke에서 확인할 SDK 표면"으로 남긴 5건과, A-06이 같은 자리에 남긴
# 3건(`openai>=2.0` 하한은 설치 시점에 드러나므로 여기서 보지 않는다).
CHECKLIST: Mapping[ProviderKind, tuple[CheckItem, ...]] = {
    ProviderKind.ANTHROPIC: (
        CheckItem(
            "thinking_signature_roundtrip",
            "thinking 블록의 signature가 수동 루프의 assistant 턴 재전송에서 살아남는가",
        ),
        CheckItem(
            "output_format_none",
            "`output_format=None`을 명시해 부르는 것이 런타임에 무해한가",
        ),
        CheckItem(
            "probe_minimal_output",
            "probe의 `max_tokens=16`이 adaptive thinking에서 400을 내지 않는가",
        ),
        CheckItem(
            "thinking_display_summarized",
            '`display: "summarized"`가 실제로 thinking 텍스트를 채우는가',
        ),
        CheckItem(
            "search_result_fields",
            "검색 결과 블록의 제목·URL 매핑이 실제 응답과 맞는가",
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
            "probe_minimal_output",
            "probe의 `max_output_tokens=16`이 400을 내지 않는가",
        ),
    ),
}


@dataclass(frozen=True)
class TurnEvidence:
    """한 턴이 남긴 관측. 확인 항목의 판정은 전부 이 값만 본다."""

    probe_ok: bool
    probe_latency_ms: int | None
    stream_raised: bool
    event_kinds: tuple[str, ...]
    tool_rounds: int
    search_activities: int
    search_with_sources: int
    thinking_with_text: int
    proposal_accepted: bool
    failure_code: str | None


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
    evidence: TurnEvidence | None = None
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


def verdicts_for(kind: ProviderKind, evidence: TurnEvidence) -> tuple[CheckVerdict, ...]:
    """관측을 확인 항목에 건다. 판정 규칙이 한곳에 모여 있어야 출력과 테스트가 같은 말을 한다."""
    completed_multi_round = evidence.tool_rounds >= 2 and evidence.failure_code is None
    rules: Mapping[str, tuple[bool | None, str]] = {
        "thinking_signature_roundtrip": (
            True if completed_multi_round else None,
            f"도구 라운드 {evidence.tool_rounds}회를 실패 없이 완주"
            if completed_multi_round
            else "도구 라운드가 2회 미만이라 재전송 경로를 타지 않았다",
        ),
        "reasoning_item_roundtrip": (
            True if completed_multi_round else None,
            f"도구 라운드 {evidence.tool_rounds}회를 실패 없이 완주"
            if completed_multi_round
            else "도구 라운드가 2회 미만이라 재전송 경로를 타지 않았다",
        ),
        "output_format_none": (
            not evidence.stream_raised,
            "스트림이 예외 없이 끝났다" if not evidence.stream_raised else "스트림이 예외로 끝났다",
        ),
        "probe_minimal_output": (
            evidence.probe_ok,
            f"probe {evidence.probe_latency_ms}ms" if evidence.probe_ok else "probe 실패",
        ),
        "thinking_display_summarized": (
            True if evidence.thinking_with_text else None,
            f"비어 있지 않은 사고 요약 {evidence.thinking_with_text}건"
            if evidence.thinking_with_text
            else "사고 요약 이벤트가 없었다(모델이 생각을 내보내지 않음)",
        ),
        "search_result_fields": (
            (evidence.search_with_sources > 0) if evidence.search_activities else None,
            f"검색 {evidence.search_activities}회 중 "
            f"출처가 실린 것 {evidence.search_with_sources}회"
            if evidence.search_activities
            else "검색을 한 번도 하지 않았다",
        ),
        "search_budget_notice_reaction": (
            None,
            f"검색 {evidence.search_activities}회 — 상한에 닿지 않으면 통지 경로를 타지 않는다",
        ),
    }
    return tuple(
        CheckVerdict(item=item, observed=rules[item.key][0], note=rules[item.key][1])
        for item in CHECKLIST[kind]
    )


def _evidence_from(
    events: Sequence[ChatEvent],
    *,
    probe_ok: bool,
    probe_latency_ms: int | None,
    stream_raised: bool,
) -> TurnEvidence:
    failure = next((event for event in events if isinstance(event, Failure)), None)
    searches = [event for event in events if isinstance(event, SearchActivity)]
    return TurnEvidence(
        probe_ok=probe_ok,
        probe_latency_ms=probe_latency_ms,
        stream_raised=stream_raised,
        event_kinds=tuple(type(event).__name__ for event in events),
        tool_rounds=sum(1 for event in events if isinstance(event, ToolResultSummary)),
        search_activities=len(searches),
        search_with_sources=sum(1 for event in searches if event.sources),
        thinking_with_text=sum(
            1 for event in events if isinstance(event, ThinkingSummary) and event.text.strip()
        ),
        proposal_accepted=any(isinstance(event, Proposal) for event in events),
        failure_code=failure.code.value if failure is not None else None,
    )


def _run_provider(kind: ProviderKind, secret: str, workspace: Path) -> ProviderSmoke:
    """한 공급자에 대해 probe → 짧은 턴 → 제안까지 한 번 돌린다."""
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=workspace / f"{kind.value}-secrets.json",
        )
    )
    try:
        profile = container.assistant_profiles.create(
            kind=kind, label=f"live-smoke-{kind.value}", secret=secret
        )
    except Exception as error:
        return ProviderSmoke(
            kind=kind,
            status=SmokeStatus.FAILED,
            detail=f"프로파일 생성이 실패했다 — error_type={type(error).__name__}",
        )
    probe = container.assistant_profiles.test(profile.profile_id)
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
    evidence = _evidence_from(
        events,
        probe_ok=probe.ok,
        probe_latency_ms=probe.latency_ms,
        stream_raised=stream_raised,
    )
    verdicts = verdicts_for(kind, evidence)
    failed = any(verdict.observed is False for verdict in verdicts)
    return ProviderSmoke(
        kind=kind,
        status=SmokeStatus.FAILED if failed else SmokeStatus.OK,
        detail=detail,
        evidence=evidence,
        verdicts=verdicts,
    )


def _report(smoke: ProviderSmoke, write: Callable[[str], None]) -> None:
    write("")
    write(f"== {smoke.kind.value} — {smoke.status.value} ({smoke.detail})")
    evidence = smoke.evidence
    if evidence is not None:
        write(f"   probe: ok={evidence.probe_ok} latency_ms={evidence.probe_latency_ms}")
        write(f"   이벤트: {' → '.join(evidence.event_kinds) or '없음'}")
        write(
            f"   도구 라운드={evidence.tool_rounds} 검색={evidence.search_activities} "
            f"제안 접수={evidence.proposal_accepted} 실패={evidence.failure_code or '없음'}"
        )
    for verdict in smoke.verdicts:
        write(f"   {verdict.marker} {verdict.item.question}")
        write(f"       {verdict.note}")


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
