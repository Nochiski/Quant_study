"""어시스턴트 의존 그래프의 배선 (설계 spec D5/D6, WORKFLOW A-04).

HTTP 계약은 `tests/integration/test_assistant_http_api.py`가 본다. 여기서 보는 것은 composition
root가 **무엇을 어디서 읽어 무엇을 조립하는가**다: 환경 변수 이름, 저장소 밖으로 강제되는 비밀
파일, 공급자 레지스트리에 없는 종류의 상태, authoring compile을 감싼 `StrategyCompilerPort`.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.secrets_local.facade.store import default_secrets_path
from strategy_workbench.application.assistant_chat.facade.ports import LlmProviderPort
from strategy_workbench.application.assistant_chat.facade.profiles import (
    ProviderNotInstalledError,
)
from strategy_workbench.bootstrap import _assistant as assistant_module
from strategy_workbench.bootstrap._assistant import (
    _AuthoringStrategyCompiler,  # pyright: ignore[reportPrivateUsage]  # reason: composition root가 소유한 port 구현이라 공개 facade가 없다
)
from strategy_workbench.bootstrap.facade.container import (
    PROVIDER_ADAPTER_FACTORIES,
    PROVIDER_SDK_MODULES,
    SIDECAR_SUFFIXES,
    AssistantSettings,
    BackendContainer,
    FilePermissionError,
    build_assistant_services,
    build_container,
    is_missing_provider_sdk,
    restrict_to_current_user,
)
from strategy_workbench.bootstrap.facade.http import (
    ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV,
    ASSISTANT_DB_PATH_ENV,
    ASSISTANT_SECRETS_PATH_ENV,
    DEFAULT_ASSISTANT_DB_PATH,
    build_http_app,
    runtime_assistant_settings,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    ToolCall,
    ToolResult,
    TurnRequest,
)
from strategy_workbench.domain.factor.facade.registry import (
    FactorRegistry,
    build_default_factor_registry,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strategy_documents"


def test_the_container_builds_the_three_assistant_services_together(tmp_path: Path) -> None:
    container = build_container(
        assistant=AssistantSettings(db_path=None, secrets_path=tmp_path / "secrets.json")
    )

    assert container.assistant_profiles is not None
    assert container.assistant_chat is not None
    assert container.assistant_turns is not None


def test_a_kind_with_no_registered_factory_is_listed_as_not_installed(tmp_path: Path) -> None:
    """레지스트리에 없는 종류도 숨기지 않고 "설치 필요"로 화면까지 전달된다.

    화면은 선언된 종류를 **전부** 보여 주어야 한다 — 사용자가 왜 그 공급자를 못 고르는지
    알아야 한다.

    레지스트리를 **주입해서** 본다. 전역 `PROVIDER_ADAPTER_FACTORIES`를 읽으면 이 테스트가
    "지금 누가 등록돼 있는가"에 묶여, A-06이 `openai`를 등록할 때 같이 고쳐야 한다. 여기서
    보려는 것은 "등록되지 않은 종류의 표현"이고 그건 등록 현황과 무관하다.

    "등록은 됐는데 SDK가 없다"는 경우는 `_refuse_import` 계열 테스트가 따로 본다.
    """
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={},
        )
    )

    availability = {item.kind: item for item in container.assistant_profiles.available_kinds()}

    assert set(availability) == set(ProviderKind)
    assert not any(item.installed for item in availability.values())
    assert not any(item.default_model for item in availability.values())


def test_the_default_registry_offers_the_installed_anthropic_adapter(tmp_path: Path) -> None:
    """기본 레지스트리에 A-05 adapter가 등록돼 있고, SDK가 있으면 설치됨으로 해소된다.

    SDK가 있어야 뜻이 있는 단언이라 `importorskip`으로 이 테스트만 건너뛴다. 모듈 최상단에서
    adapter facade를 import하면 extra 없이는 이 파일 전체가 수집되지 않는다 — A-06도 같은
    자리에 `llm_openai`를 더하므로 이 모양을 따른다.
    """
    pytest.importorskip("anthropic", reason="공급자 SDK는 optional extra `llm`이다")
    from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import DEFAULT_MODEL

    assert ProviderKind.ANTHROPIC in PROVIDER_ADAPTER_FACTORIES
    container = build_container(
        assistant=AssistantSettings(db_path=None, secrets_path=tmp_path / "secrets.json")
    )

    availability = {item.kind: item for item in container.assistant_profiles.available_kinds()}

    assert availability[ProviderKind.ANTHROPIC].installed is True
    assert availability[ProviderKind.ANTHROPIC].default_model == DEFAULT_MODEL


def test_the_secrets_file_may_not_live_inside_the_repository() -> None:
    """저장소 안 경로는 어댑터가 생성 시점에 거절한다 — 평문 키가 git에 들어가는 사고 차단."""
    inside_repository = Path(__file__).resolve().parents[2] / "backend" / "secrets.json"
    container = _container_parts()

    with pytest.raises(ValueError, match="outside the repository"):
        build_assistant_services(
            settings=AssistantSettings(db_path=None, secrets_path=inside_repository),
            equity_data=container.equity_data,
            factor_registry=_factor_registry(),
            strategy_authoring=container.strategy_authoring,
        )


def test_the_runtime_settings_come_from_the_documented_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ASSISTANT_DB_PATH_ENV, str(tmp_path / "assistant.sqlite3"))
    monkeypatch.setenv(ASSISTANT_SECRETS_PATH_ENV, str(tmp_path / "secrets.json"))
    monkeypatch.setenv(ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV, "1")

    settings = runtime_assistant_settings()

    assert settings.db_path == tmp_path / "assistant.sqlite3"
    assert settings.secrets_path == tmp_path / "secrets.json"
    assert settings.allow_insecure_base_url is True


def test_the_runtime_settings_fall_back_to_the_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        ASSISTANT_DB_PATH_ENV,
        ASSISTANT_SECRETS_PATH_ENV,
        ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    settings = runtime_assistant_settings()

    assert settings.db_path == DEFAULT_ASSISTANT_DB_PATH
    # `None`은 "OS 규칙이 정하는 사용자 설정 디렉터리"라는 뜻이고, 그 계산의 owner는 어댑터다.
    assert settings.secrets_path is None
    assert settings.allow_insecure_base_url is False
    assert default_secrets_path().name == "secrets.json"


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1 x"])
def test_only_an_exact_one_opens_the_insecure_base_url_escape_hatch(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """오타가 허용 범위를 넓히면 안 된다 — 이 플래그는 평문 http로 키를 보내게 만든다."""
    monkeypatch.setenv(ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV, value)

    assert runtime_assistant_settings().allow_insecure_base_url is False


def test_the_http_app_exposes_the_assistant_routes() -> None:
    paths = build_http_app().openapi()["paths"]

    assert "/api/v1/assistant/providers" in paths
    assert "/api/v1/assistant/sessions/{session_id}/events" in paths
    assert "/api/v1/assistant/sessions/{session_id}/turns/{turn_id}/cancel" in paths


def test_the_injected_compiler_reuses_the_authoring_compile_contract() -> None:
    """`StrategyCompilerPort`는 검증 규칙을 다시 구현하지 않고 authoring 결과를 옮기기만 한다."""
    compiler = _AuthoringStrategyCompiler(_container_parts().strategy_authoring)

    compiled = compiler.compile((FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8"))

    assert compiled.ok is True
    assert compiled.spec_hash
    assert [item for item in compiled.diagnostics if item.severity == "error"] == []


def test_the_injected_compiler_hands_back_diagnostics_instead_of_raising() -> None:
    compiler = _AuthoringStrategyCompiler(_container_parts().strategy_authoring)

    compiled = compiler.compile((FIXTURES / "quality_momentum.invalid.yaml").read_text("utf-8"))

    assert compiled.ok is False
    assert compiled.spec_hash is None
    assert compiled.diagnostics


def _container_parts() -> BackendContainer:
    return build_container(assistant=AssistantSettings(db_path=None))


def _factor_registry() -> FactorRegistry:
    return build_default_factor_registry()


# -- 공급자 레지스트리 (지연 해소) --------------------------------------------------------------


def _refuse_import() -> LlmProviderPort:
    """optional extra가 없는 환경의 팩토리. A-05·A-06의 팩토리가 이 모양을 지켜야 한다.

    `import anthropic`이 실제로 내는 예외와 같은 모양이다 — `ModuleNotFoundError`에
    `name="anthropic"`.
    """
    raise ModuleNotFoundError("No module named 'anthropic'", name="anthropic")


def _refuse_submodule_import() -> LlmProviderPort:
    """SDK가 지연 import를 쓰면 실패가 하위 모듈에서 난다. 그것도 미설치다."""
    raise ModuleNotFoundError("No module named 'anthropic.types'", name="anthropic.types")


def _typo_import() -> LlmProviderPort:
    """우리 adapter 안의 오타 import. 미설치가 아니라 버그다."""
    raise ModuleNotFoundError(
        "No module named 'strategy_workbench.adapters.outbound.llm_anthropik'",
        name="strategy_workbench.adapters.outbound.llm_anthropik",
    )


def _broken_sdk_install() -> LlmProviderPort:
    """설치는 됐는데 SDK가 자기 의존성을 못 찾는다. 미설치가 아니라 깨진 설치다."""
    raise ImportError("cannot import name 'BaseModel' from 'pydantic'", name="anthropic")


def _missing_submodule_of_an_installed_sdk() -> LlmProviderPort:
    """설치된 패키지의 없는 하위 모듈을 부른다. SDK 부재가 아니라 우리 오타다.

    `pytest`를 SDK 자리에 세워 "최상위 모듈은 분명히 있다"를 이 환경에서 보장한다 — optional
    extra인 `anthropic`은 CI에 없을 수 있어 그걸로는 이 갈래를 재현할 수 없다.
    """
    raise ModuleNotFoundError(
        "No module named 'pytest.definitely_not_here'", name="pytest.definitely_not_here"
    )


class _CountingFactory:
    """호출 횟수를 세는 팩토리. 시작할 때 불리지 않는다는 것을 보기 위해서다."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> LlmProviderPort:
        self.calls += 1
        return _StubProvider()


class _StubProvider:
    """`LlmProviderPort` 최소 구현. 레지스트리 해소만 보므로 호출은 일어나지 않는다."""

    kind = ProviderKind.ANTHROPIC

    def default_model(self) -> str:
        return "stub-model-1"

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        return ProbeResult(ok=True, latency_ms=1)

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        yield Done(stop_reason="end_turn")


def test_a_provider_whose_sdk_is_missing_does_not_stop_the_backend(tmp_path: Path) -> None:
    """미설치 SDK가 시작을 막으면 어시스턴트를 쓰지도 않는 사용자의 백엔드가 통째로 안 뜬다."""
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: _refuse_import},
        )
    )

    availability = {item.kind: item for item in container.assistant_profiles.available_kinds()}

    assert availability[ProviderKind.ANTHROPIC].installed is False
    assert availability[ProviderKind.ANTHROPIC].default_model is None


def test_an_uninstalled_provider_refuses_profile_creation_instead_of_crashing(
    tmp_path: Path,
) -> None:
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: _refuse_import},
        )
    )

    with pytest.raises(ProviderNotInstalledError):
        container.assistant_profiles.create(
            kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-not-used-0001"
        )


def test_a_missing_sdk_submodule_still_counts_as_not_installed(tmp_path: Path) -> None:
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: _refuse_submodule_import},
        )
    )

    availability = {item.kind: item for item in container.assistant_profiles.available_kinds()}

    assert availability[ProviderKind.ANTHROPIC].installed is False


@pytest.mark.parametrize("factory", [_typo_import, _broken_sdk_install])
def test_an_import_failure_that_is_not_a_missing_sdk_is_not_swallowed(
    factory: Callable[[], LlmProviderPort], tmp_path: Path
) -> None:
    """오타 import나 깨진 설치를 "설치 필요"로 둔갑시키면 진짜 원인이 화면에서 사라진다.

    사용자는 이미 설치한 SDK를 다시 설치하려 들고, 우리 버그는 로그 한 줄로만 남는다.
    """
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: factory},
        )
    )

    with pytest.raises(ImportError):
        container.assistant_profiles.available_kinds()


def test_the_sdk_module_table_covers_every_provider_kind() -> None:
    """kind를 늘리면 표도 늘어야 한다. 빠지면 그 kind의 미설치가 전부 예외로 샌다."""
    assert set(PROVIDER_SDK_MODULES) == set(ProviderKind)
    assert PROVIDER_SDK_MODULES[ProviderKind.ANTHROPIC] == "anthropic"
    assert PROVIDER_SDK_MODULES[ProviderKind.OPENAI] == "openai"


def test_a_missing_submodule_of_an_installed_sdk_is_our_bug_not_a_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`anthropic`은 깔려 있는데 그 하위 모듈이 없으면 오타다.

    이름만 보고 미설치로 낮추면 설정 화면이 "설치 필요"라고만 말하고, 사용자는 이미 설치한
    SDK를 다시 설치하려 든다. 실제로 설치된 패키지(`pytest`)를 이 kind의 SDK 자리에 세워
    "최상위는 있는데 하위가 없는" 상태를 만든다.
    """
    # 표 자체를 갈아 끼운다. `MappingProxyType`이라 항목만 바꿀 수는 없다.
    monkeypatch.setattr(
        assistant_module, "PROVIDER_SDK_MODULES", {ProviderKind.ANTHROPIC: "pytest"}
    )

    assert not is_missing_provider_sdk(
        ProviderKind.ANTHROPIC,
        ModuleNotFoundError("", name="pytest.definitely_not_here"),
    )
    # 최상위가 없으면 같은 모양이라도 미설치다.
    monkeypatch.setattr(
        assistant_module, "PROVIDER_SDK_MODULES", {ProviderKind.ANTHROPIC: "definitely_not_a_pkg"}
    )
    assert is_missing_provider_sdk(
        ProviderKind.ANTHROPIC,
        ModuleNotFoundError("", name="definitely_not_a_pkg.client"),
    )


def test_an_installed_sdk_with_a_missing_submodule_is_not_swallowed_by_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assistant_module, "PROVIDER_SDK_MODULES", {ProviderKind.ANTHROPIC: "pytest"}
    )
    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: _missing_submodule_of_an_installed_sdk},
        )
    )

    with pytest.raises(ModuleNotFoundError):
        container.assistant_profiles.available_kinds()


def test_a_missing_sdk_is_told_apart_from_our_own_import_bug() -> None:
    """판정 함수 자체. 위 배선 테스트가 이 규칙 위에 선다."""
    assert is_missing_provider_sdk(
        ProviderKind.ANTHROPIC, ModuleNotFoundError("", name="anthropic")
    )
    assert is_missing_provider_sdk(
        ProviderKind.ANTHROPIC, ModuleNotFoundError("", name="anthropic.types")
    )
    # 이름이 겹쳐 보이는 남의 패키지는 하위 모듈이 아니다.
    assert not is_missing_provider_sdk(
        ProviderKind.ANTHROPIC, ModuleNotFoundError("", name="anthropic_extras")
    )
    # 다른 kind의 SDK는 이 kind의 미설치가 아니다.
    assert not is_missing_provider_sdk(
        ProviderKind.ANTHROPIC, ModuleNotFoundError("", name="openai")
    )
    # 이름이 없으면 단정하지 않는다.
    assert not is_missing_provider_sdk(ProviderKind.ANTHROPIC, ModuleNotFoundError("boom"))


def test_the_factory_is_called_once_and_only_when_a_provider_is_needed(tmp_path: Path) -> None:
    """공급자 SDK import는 비싸고, 어시스턴트를 안 쓰는 시작 경로가 그 값을 치를 이유가 없다."""
    factory = _CountingFactory()

    container = build_container(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: factory},
        )
    )

    assert factory.calls == 0
    container.assistant_profiles.available_kinds()
    container.assistant_profiles.available_kinds()
    assert factory.calls == 1


# -- DB 파일 권한 (A-03 리뷰가 A-04로 넘긴 항목) ----------------------------------------------


class _RecordingRunner:
    """`icacls` 대신 인자만 받아 적는 러너. 실제 ACL을 건드리지 않고 규칙을 본다."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> None:
        self.commands.append(list(command))


def test_the_windows_branch_breaks_inheritance_and_grants_only_the_current_user(
    tmp_path: Path,
) -> None:
    """플랫폼을 주입해 검사한다 — 규칙은 어느 기계에서 돌든 같아야 한다."""
    database = tmp_path / "assistant.sqlite3"
    database.write_bytes(b"")
    runner = _RecordingRunner()

    touched = restrict_to_current_user(
        database, platform="win32", run_command=runner, windows_account=r"DOMAIN\tester"
    )

    assert touched == (database,)
    assert runner.commands == [
        ["icacls", str(database), "/inheritance:r", "/grant:r", r"DOMAIN\tester:F"]
    ]


def test_existing_sqlite_sidecars_are_locked_down_with_the_database(tmp_path: Path) -> None:
    """쓰기 중에 생기는 `-journal`에도 같은 페이지 내용이 들어간다."""
    database = tmp_path / "assistant.sqlite3"
    database.write_bytes(b"")
    journal = tmp_path / "assistant.sqlite3-journal"
    journal.write_bytes(b"")
    runner = _RecordingRunner()

    touched = restrict_to_current_user(
        database, platform="win32", run_command=runner, windows_account="tester"
    )

    assert touched == (database, journal)
    assert [command[1] for command in runner.commands] == [str(database), str(journal)]
    assert SIDECAR_SUFFIXES[0] == "-journal"


def test_restricting_a_missing_file_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    with pytest.raises(FilePermissionError, match="does not exist"):
        restrict_to_current_user(tmp_path / "absent.sqlite3", platform="linux")


def test_a_failing_acl_command_stops_startup(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    database.write_bytes(b"")

    def refuse(command: Sequence[str]) -> None:
        raise FilePermissionError(f"could not restrict the file ACL — command={list(command)}")

    with pytest.raises(FilePermissionError):
        restrict_to_current_user(database, platform="win32", run_command=refuse)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 모드 비트는 Windows에 없다")
def test_the_posix_branch_leaves_the_database_readable_only_by_its_owner(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    database.write_bytes(b"")

    restrict_to_current_user(database, platform="linux")

    assert stat.S_IMODE(os.stat(database).st_mode) == 0o600


def test_building_the_container_with_a_real_path_locks_the_database_file(tmp_path: Path) -> None:
    """배선까지 포함한 확인: 파일이 만들어지고, 그 파일에 권한이 걸린 채 시작이 끝난다."""
    database = tmp_path / "nested" / "assistant.sqlite3"

    build_container(
        assistant=AssistantSettings(db_path=database, secrets_path=tmp_path / "secrets.json")
    )

    assert database.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(database).st_mode) == 0o600
