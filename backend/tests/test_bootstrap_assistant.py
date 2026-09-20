"""어시스턴트 의존 그래프의 배선 (설계 spec D5/D6, WORKFLOW A-04).

HTTP 계약은 `tests/integration/test_assistant_http_api.py`가 본다. 여기서 보는 것은 composition
root가 **무엇을 어디서 읽어 무엇을 조립하는가**다: 환경 변수 이름, 저장소 밖으로 강제되는 비밀
파일, 공급자 레지스트리가 비었을 때의 상태, authoring compile을 감싼 `StrategyCompilerPort`.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.secrets_local.facade.store import default_secrets_path
from strategy_workbench.bootstrap._assistant import (
    _AuthoringStrategyCompiler,  # pyright: ignore[reportPrivateUsage]  # reason: composition root가 소유한 port 구현이라 공개 facade가 없다
)
from strategy_workbench.bootstrap.facade.container import (
    PROVIDER_ADAPTER_FACTORIES,
    SIDECAR_SUFFIXES,
    AssistantSettings,
    BackendContainer,
    FilePermissionError,
    build_assistant_services,
    build_container,
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
from strategy_workbench.domain.assistant.facade.models import ProviderKind
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


def test_an_empty_provider_registry_reports_every_kind_as_not_installed(tmp_path: Path) -> None:
    """A-05·A-06 전에는 레지스트리가 비어 있고, 그 사실이 화면까지 그대로 전달된다."""
    assert PROVIDER_ADAPTER_FACTORIES == {}
    container = build_container(
        assistant=AssistantSettings(db_path=None, secrets_path=tmp_path / "secrets.json")
    )

    availability = container.assistant_profiles.available_kinds()

    assert {item.kind for item in availability} == set(ProviderKind)
    assert not any(item.installed for item in availability)
    assert not any(item.default_model for item in availability)


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
        database, platform="win32", run_command=runner, windows_account="DOMAIN\tester"
    )

    assert touched == (database,)
    assert runner.commands == [
        ["icacls", str(database), "/inheritance:r", "/grant:r", "DOMAIN\tester:F"]
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
