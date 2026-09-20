"""어시스턴트 의존 그래프 한 벌 (설계 spec D5/D6, WORKFLOW A-04).

`_container.py`가 길어지지 않게 어시스턴트 조립만 떼어 둔 composition root의 일부다. 여기서
정하는 것은 두 가지다.

1. **설정의 모양.** 경로·플래그는 `AssistantSettings`로 한 번에 받는다. 환경 변수를 실제로 읽는
   것은 서버 프로세스뿐이고(`_http.py`), 이 함수는 값만 받는다 — 테스트가 임시 경로로 같은
   그래프를 세울 수 있어야 하기 때문이다.
2. **공급자 adapter 레지스트리.** `kind → 팩토리`를 두고 A-05·A-06이 자기 항목을 채운다.
   공급자 SDK는 optional extra(`llm`)이므로 **설치된 것만** 등록한다. 등록되지 않은 종류는
   `ProviderProfileService`가 "설치 필요"로 답하고 프로파일 생성을 거절한다(spec D4).

   **팩토리는 시작할 때 부르지 않는다.** 공급자 SDK는 optional extra(`llm`)라 설치되지 않은
   환경이 정상이고, 시작 시점에 부르면 그 `ImportError`가 `build_container`를 타고 올라가
   **어시스턴트를 쓰지도 않는 사용자의 백엔드가 통째로 안 뜬다.** `_LazyProviderRegistry`가
   처음 필요할 때(설정 화면이 목록을 묻거나 프로파일을 만들 때) 한 번만 부르고, `ImportError`는
   "미설치"로 낮춰 `available_kinds()`의 `installed=False`가 된다.

**비밀 파일은 저장소 밖에 둔다.** `forbidden_roots`에 저장소 루트를 넘겨, 환경 변수가 작업
트리 안을 가리키면 어댑터가 생성 시점에 거절하게 한다(평문 키가 git에 들어가는 사고를 막는
가장 싼 지점이 여기다).

**DB 파일도 현재 사용자 전용으로 잠근다**(`_file_guard`). 대화 이력에는 사용자가 모델에 보낸
전략 원문과 질문이 그대로 쌓인다. 비밀 파일만 잠그고 이력을 열어 두면 같은 장비의 다른 계정이
그걸 읽는다. 잠그는 시점이 여기인 이유는 파일이 생기는 시점을 아는 곳이 여기뿐이기 때문이다 —
`AssistantDatabase`가 경로를 받아 파일을 만들고 나서 바로 좁힌다.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from threading import RLock
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

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ASSISTANT_SETTINGS",
    "PROVIDER_ADAPTER_FACTORIES",
    "PROVIDER_SDK_MODULES",
    "AssistantServices",
    "AssistantSettings",
    "ProviderAdapterFactory",
    "build_assistant_services",
    "is_missing_provider_sdk",
    "repository_root",
]

# 공급자 adapter 하나를 만드는 함수. A-05·A-06이 지켜야 하는 계약은 둘이다.
#
# 1. **SDK와 adapter import를 함수 본문 안에서 한다.** 모듈 최상단에서 import하면 이 파일이
#    읽히는 순간 optional extra가 없는 환경에서 `ImportError`가 나고, 지연 호출이 무의미해진다.
# 2. **미설치는 그 SDK의 `ModuleNotFoundError`로 알린다.** 아래 표가 kind별로 어떤 모듈이어야
#    하는지 정하고, 레지스트리는 그 모듈(또는 그 하위 모듈)일 때만 "미설치"로 낮춘다.
ProviderAdapterFactory: TypeAlias = Callable[[], LlmProviderPort]

# kind → optional extra가 설치하는 최상위 SDK 모듈 이름.
#
# 이 표가 없으면 **adapter 안의 오타 import까지 "미설치"로 둔갑한다.** `from ..._adaptor import X`
# 같은 우리 쪽 실수도 `ModuleNotFoundError`이고, 그걸 삼키면 설정 화면은 "설치 필요"라고만
# 말한다. 사용자는 이미 설치한 SDK를 다시 설치하려 들고, 진짜 원인인 우리 버그는 로그
# 한 줄로만 남는다. `ImportError.name`이 그 kind의 SDK(또는 하위 모듈)가 아니면 그대로 올린다.
PROVIDER_SDK_MODULES: Mapping[ProviderKind, str] = MappingProxyType(
    {
        ProviderKind.ANTHROPIC: "anthropic",
        ProviderKind.OPENAI: "openai",
    }
)


def _anthropic_adapter() -> LlmProviderPort:
    """A-05 Anthropic adapter(`llm_anthropic`). SDK 미설치는 `ModuleNotFoundError`로 알린다.

    import가 함수 본문 안에 있는 것이 위 계약 1이다. 최상단으로 올리면 이 파일이 읽히는 순간
    optional extra 없는 환경에서 터지고, 지연 레지스트리가 아무것도 막지 못한다.

    `find_spec`으로 미리 확인하지도, `ImportError`를 가로채지도 않는다. 판정은 한 곳
    (`is_missing_provider_sdk`)만 한다 — 두 곳에서 하면 한쪽만 고쳐졌을 때 "미설치"와
    "우리 버그"의 경계가 조용히 어긋난다.
    """
    from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (
        AnthropicLlmAdapter,
    )

    return AnthropicLlmAdapter()


# A-06(`llm_openai`)이 자기 항목을 여기에 더한다. 등록되지 않은 종류도 설정 화면에는 보이고
# "설치 필요"로 표시된다(`ProviderProfileService.available_kinds`).
PROVIDER_ADAPTER_FACTORIES: Mapping[ProviderKind, ProviderAdapterFactory] = MappingProxyType(
    {ProviderKind.ANTHROPIC: _anthropic_adapter}
)


def is_missing_provider_sdk(kind: ProviderKind, error: ImportError) -> bool:
    """이 `ImportError`가 "그 공급자 SDK가 안 깔렸다"는 뜻인가.

    세 가지를 모두 만족해야 참이다.

    1. `ModuleNotFoundError`다. 설치된 패키지 **안에서** 난 `ImportError`(예: SDK가 자기
       의존성을 못 찾음)는 미설치가 아니라 깨진 설치이므로 숨기지 않는다.
    2. `name`이 그 kind의 SDK 최상위 모듈이거나 그 하위 모듈이다. `anthropic.types` 같은
       하위 모듈까지 포함하는 이유는 SDK가 지연 import를 쓰면 실패가 거기서 나기 때문이다.
    3. 하위 모듈이면 **최상위 모듈이 실제로 없어야** 한다. `anthropic`은 깔려 있는데
       `anthropic.definitely_not_here`를 부르는 것은 SDK 부재가 아니라 우리 오타다. 이름만
       보고 미설치로 낮추면 설정 화면이 "설치 필요"라고만 말하고, 사용자는 이미 설치한 SDK를
       다시 설치하려 든다.

    `name`이 비어 있으면(드물지만 직접 만든 예외) 참이라고 단정하지 않는다 — 우리 버그를
    미설치로 둔갑시키는 쪽보다 시끄러운 쪽이 낫다.
    """
    if not isinstance(error, ModuleNotFoundError):
        return False
    module = PROVIDER_SDK_MODULES.get(kind)
    if module is None or not error.name:
        return False
    if error.name == module:
        return True
    if not error.name.startswith(f"{module}."):
        return False
    return not _is_importable(module)


def _is_importable(module: str) -> bool:
    """그 최상위 모듈이 이 인터프리터에 있는가.

    `find_spec`은 부모 패키지를 실제로 import하므로 그 과정에서 예외를 낼 수 있다. 여기서는
    최상위 이름만 보므로 부모가 없지만, 탐색 자체가 실패하는 경우(손상된 경로 항목 등)에는
    "있다"고 답한다 — 확신이 없을 때 미설치로 낮추지 않는 쪽이 이 함수의 기본 방향이다.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return True


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
    """**소스 체크아웃 기준** 저장소 루트. 비밀 파일이 들어가면 안 되는 트리다.

    `backend/src/strategy_workbench/bootstrap/`에서 네 단계 위다. 같은 파일의 `.local` 경로
    계산(`_http.py`의 `parents[3]`)과 같은 관례이고, 이 앱은 editable 설치(`uv sync`)로만
    돌기 때문에 성립한다. 비 editable 설치에서는 관련 없는 디렉터리를 가리키므로, 그런 배포가
    생기면 저장소 루트를 설정으로 받아야 한다.
    """
    return Path(__file__).resolve().parents[4]


class _LazyProviderRegistry(Mapping[ProviderKind, LlmProviderPort]):
    """등록된 팩토리를 **처음 필요할 때** 한 번만 부르는 공급자 맵.

    `ProviderProfileService`·`AssistantChatService`가 쓰는 조회는 `.get(kind)` 하나이고,
    "없다"가 곧 "설치 안 됨"이다. 그래서 **그 공급자 SDK가 없다는 뜻의**
    `ModuleNotFoundError`를 낸 kind만 없는 것으로 답한다(`is_missing_provider_sdk`) — 설정
    화면의 "설치 필요"가 그 값에서 나온다(spec D4). 그 밖의 import 실패는 우리 버그이므로
    그대로 올린다.

    `Mapping` 규약을 지키려고 `__contains__`·`__iter__`도 같은 해소를 탄다. `kind in registry`가
    참인데 `.get(kind)`가 `None`인 상태를 만들지 않기 위해서다.
    """

    def __init__(self, factories: Mapping[ProviderKind, ProviderAdapterFactory]) -> None:
        self._factories = dict(factories)
        self._resolved: dict[ProviderKind, LlmProviderPort | None] = {}
        self._lock = RLock()

    def __getitem__(self, kind: ProviderKind) -> LlmProviderPort:
        provider = self._resolve(kind)
        if provider is None:
            raise KeyError(kind)
        return provider

    def __iter__(self) -> Iterator[ProviderKind]:
        return iter([kind for kind in self._factories if self._resolve(kind) is not None])

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def _resolve(self, kind: ProviderKind) -> LlmProviderPort | None:
        """팩토리를 한 번만 부르고 결과(실패 포함)를 기억한다.

        실패도 캐시한다. 안 그러면 설정 화면을 열 때마다 없는 SDK를 다시 import하려 든다.
        """
        with self._lock:
            if kind in self._resolved:
                return self._resolved[kind]
            factory = self._factories.get(kind)
            if factory is None:
                self._resolved[kind] = None
                return None
            try:
                provider = factory()
            except ImportError as error:
                if not is_missing_provider_sdk(kind, error):
                    # 우리 쪽 import 실수는 미설치가 아니다. 삼키면 화면이 "설치 필요"라고만
                    # 말하고 진짜 원인은 로그에만 남는다.
                    raise
                # 사유는 로그에만 남긴다. "왜 설치 필요로 뜨지"를 답할 수 있어야 하고,
                # 화면에는 열거된 상태(`installed=False`)만 나간다.
                logger.info(
                    "provider adapter is not installed — kind=%s error_type=%s module=%s",
                    kind.value,
                    type(error).__name__,
                    error.name,
                )
                self._resolved[kind] = None
                return None
            self._resolved[kind] = provider
            return provider


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
    providers = _LazyProviderRegistry(settings.provider_factories)
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
