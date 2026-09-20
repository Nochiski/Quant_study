"""어시스턴트 의존 그래프 한 벌 (설계 spec D5/D6, WORKFLOW A-04).

`_container.py`가 길어지지 않게 어시스턴트 조립만 떼어 둔 composition root의 일부다. 여기서
정하는 것은 두 가지다.

1. **설정의 모양.** 경로·플래그는 `AssistantSettings`로 한 번에 받는다. 환경 변수를 실제로 읽는
   것은 서버 프로세스뿐이고(`_http.py`), 이 함수는 값만 받는다 — 테스트가 임시 경로로 같은
   그래프를 세울 수 있어야 하기 때문이다.
2. **공급자 adapter 레지스트리.** `kind → 팩토리`를 두고 A-05·A-06이 자기 항목을 채운다. 지금은
   비어 있으므로 `ProviderProfileService`가 모든 종류를 "설치 필요"로 답하고 프로파일 생성을
   거절한다(spec D4: 설치되지 않은 공급자는 프로파일을 만들 수 없다).

**비밀 파일은 저장소 밖에 둔다.** `forbidden_roots`에 저장소 루트를 넘겨, 환경 변수가 작업
트리 안을 가리키면 어댑터가 생성 시점에 거절하게 한다(평문 키가 git에 들어가는 사고를 막는
가장 싼 지점이 여기다).

**DB 파일도 현재 사용자 전용으로 잠근다**(`_file_guard`). 대화 이력에는 사용자가 모델에 보낸
전략 원문과 질문이 그대로 쌓인다. 비밀 파일만 잠그고 이력을 열어 두면 같은 장비의 다른 계정이
그걸 읽는다. 잠그는 시점이 여기인 이유는 파일이 생기는 시점을 아는 곳이 여기뿐이기 때문이다 —
`AssistantDatabase`가 경로를 받아 파일을 만들고 나서 바로 좁힌다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias
from uuid import uuid4

from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    SQLiteChatSessionRepository,
    SQLiteProviderProfileRepository,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileProviderSecretStore,
    default_secrets_path,
)
from strategy_workbench.application.assistant_chat.facade.chat import AssistantChatService
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.ports import LlmProviderPort
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.turns import AssistantTurnRunner
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.domain.assistant.facade.models import (
    ProposalCompileResult,
    ProposalDiagnostic,
    ProviderKind,
)
from strategy_workbench.domain.factor.facade.registry import FactorRegistry

from ._file_guard import restrict_to_current_user

__all__ = [
    "DEFAULT_ASSISTANT_SETTINGS",
    "PROVIDER_ADAPTER_FACTORIES",
    "AssistantServices",
    "AssistantSettings",
    "ProviderAdapterFactory",
    "build_assistant_services",
    "repository_root",
]

ProviderAdapterFactory: TypeAlias = Callable[[], LlmProviderPort]

# A-05(`llm_anthropic`)·A-06(`llm_openai`)이 자기 항목을 등록한다. 비어 있는 동안에도 설정
# 화면은 두 종류를 모두 보여 주고 "설치 필요"로 표시한다(`ProviderProfileService.available_kinds`).
PROVIDER_ADAPTER_FACTORIES: Mapping[ProviderKind, ProviderAdapterFactory] = MappingProxyType({})

# 제안 YAML은 편집기와 같은 형식으로만 들어온다. 어시스턴트는 JSON 문서를 제안하지 않는다
# (도구 스키마가 `source_format: "yaml"`을 고정한다).
_PROPOSAL_SOURCE_FORMAT = SourceFormat.YAML


@dataclass(frozen=True)
class AssistantSettings:
    """어시스턴트 그래프를 세우는 데 필요한 배포 설정.

    `db_path=None`은 프로세스 안에서만 사는 in-memory DB다(전략 revision 저장소와 같은 관례).
    `secrets_path=None`이면 OS 규칙이 정하는 사용자 설정 디렉터리를 쓴다.
    """

    db_path: str | Path | None = None
    secrets_path: str | Path | None = None
    allow_insecure_base_url: bool = False
    # `MappingProxyType`은 불변이지만 dataclass가 mutable default로 보므로 factory로 준다.
    provider_factories: Mapping[ProviderKind, ProviderAdapterFactory] = field(
        default_factory=lambda: PROVIDER_ADAPTER_FACTORIES
    )


# 인자 기본값으로 쓰는 불변 설정 한 벌. 호출마다 새로 만들 이유가 없고, ruff B008이 인자
# 기본값에서의 호출을 막는다.
DEFAULT_ASSISTANT_SETTINGS = AssistantSettings()


@dataclass(frozen=True)
class AssistantServices:
    """HTTP 어댑터가 받는 어시스턴트 서비스 셋. 셋은 항상 같이 만들어지고 같이 주입된다."""

    profiles: ProviderProfileService
    chat: AssistantChatService
    turns: AssistantTurnRunner
    database: AssistantDatabase


def repository_root() -> Path:
    """설치된 패키지 기준 저장소 루트. 비밀 파일이 들어가면 안 되는 트리다."""
    return Path(__file__).resolve().parents[4]


def build_assistant_services(
    *,
    settings: AssistantSettings,
    equity_data: EquityDataPort,
    factor_registry: FactorRegistry,
    strategy_authoring: StrategyAuthoringService,
    today: Callable[[], date] = date.today,
) -> AssistantServices:
    """프로파일·세션 저장소부터 턴 러너까지 한 그래프로 묶는다.

    두 저장소에 **같은 `AssistantDatabase` 인스턴스**를 준다. 경로를 각자 주면 in-memory 모드에서
    서로 다른 DB를 열어, 세션이 참조하는 프로파일이 없는 조합이 생긴다.
    """
    database = AssistantDatabase(settings.db_path)
    if database.database_path is not None:
        # in-memory DB는 파일이 없어 잠글 것도 없다. 실패는 삼키지 않는다 — 조용히 넘어가면
        # 이력이 느슨한 권한으로 남고 아무도 그 사실을 모른다.
        restrict_to_current_user(database.database_path)
    profile_repository = SQLiteProviderProfileRepository(database)
    session_repository = SQLiteChatSessionRepository(database)
    secrets = LocalFileProviderSecretStore(
        settings.secrets_path if settings.secrets_path is not None else default_secrets_path(),
        forbidden_roots=(repository_root(),),
    )
    providers = {kind: factory() for kind, factory in sorted(settings.provider_factories.items())}
    compiler = _AuthoringStrategyCompiler(strategy_authoring)
    profiles = ProviderProfileService(
        profile_repository,
        secrets,
        providers,
        now=_now,
        new_id=_new_id,
        allow_insecure_base_url=settings.allow_insecure_base_url,
    )
    context_builder = AssistantContextBuilder(
        equity_data=equity_data,
        factor_registry=factor_registry,
        compiler=compiler,
        today=today,
    )
    chat = AssistantChatService(
        session_repository,
        profiles,
        secrets,
        providers,
        compiler,
        context_builder,
        now=_now,
        new_id=_new_id,
    )
    return AssistantServices(
        profiles=profiles,
        chat=chat,
        turns=AssistantTurnRunner(chat, session_repository, now=_now, new_id=_new_id),
        database=database,
    )


class _AuthoringStrategyCompiler:
    """`StrategyCompilerPort` 구현: 기존 authoring compile을 감싼다.

    어시스턴트가 검증 규칙을 다시 구현하지 않게 하려는 어댑터이므로, 판단은 전혀 하지 않고
    `SourceDiagnostic` → `ProposalDiagnostic` 변환만 한다. `ok`의 정의도 authoring이 소유한
    `CompiledDocument.ok`(spec이 만들어졌는가)를 그대로 쓴다.
    """

    def __init__(self, authoring: StrategyAuthoringService) -> None:
        self._authoring = authoring

    def compile(self, source_text: str) -> ProposalCompileResult:
        compiled = self._authoring.compile(CompileRequest(source_text, _PROPOSAL_SOURCE_FORMAT))
        return ProposalCompileResult(
            ok=compiled.ok,
            spec_hash=compiled.spec_hash,
            diagnostics=tuple(
                ProposalDiagnostic(
                    code=diagnostic.code,
                    pointer=diagnostic.pointer,
                    message=diagnostic.message,
                    severity=diagnostic.severity,
                )
                for diagnostic in compiled.diagnostics
            ),
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())
