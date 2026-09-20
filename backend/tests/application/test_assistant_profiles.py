"""ProviderProfileService 테스트 (A-01).

프로파일은 "연결이 확인된 것만 저장한다"가 계약이다. probe 없이 저장하면 사용자는 채팅을 보내
봐야 키가 틀렸다는 걸 알게 되고, 그 사이 잘못된 키가 활성 프로파일로 남는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
)
from strategy_workbench.application.assistant_chat.facade.profiles import (
    ProviderBaseUrlRejectedError,
    ProviderNotInstalledError,
    ProviderProbeFailedError,
    ProviderProfileService,
)
from strategy_workbench.domain.assistant.facade.models import (
    ProbeFailure,
    ProbeResult,
    ProviderKind,
)

from ._assistant_fakes import (
    InMemoryProviderProfileRepository,
    InMemoryProviderSecretStore,
    ScriptedProvider,
    sequential_ids,
)

FIXED_NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def _service(
    *,
    probe_result: ProbeResult | None = None,
    kinds: tuple[ProviderKind, ...] = (ProviderKind.ANTHROPIC,),
    allow_insecure_base_url: bool = False,
) -> tuple[ProviderProfileService, InMemoryProviderProfileRepository, InMemoryProviderSecretStore]:
    repository = InMemoryProviderProfileRepository()
    secrets = InMemoryProviderSecretStore()
    providers = {
        kind: ScriptedProvider(kind=kind, probe_result=probe_result, model=f"{kind.value}-default")
        for kind in kinds
    }
    service = ProviderProfileService(
        repository,
        secrets,
        providers,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("profile"),
        allow_insecure_base_url=allow_insecure_base_url,
    )
    return service, repository, secrets


# -- (j) 미설치 공급자 --------------------------------------------------------------------------


def test_available_kinds_marks_missing_adapters_as_not_installed() -> None:
    service, _, _ = _service()

    availability = {item.kind: item for item in service.available_kinds()}

    assert set(availability) == set(ProviderKind)
    assert availability[ProviderKind.ANTHROPIC].installed is True
    assert availability[ProviderKind.ANTHROPIC].default_model == "anthropic-default"
    assert availability[ProviderKind.OPENAI].installed is False
    assert availability[ProviderKind.OPENAI].default_model is None


def test_creating_a_profile_for_a_missing_adapter_is_rejected() -> None:
    service, repository, secrets = _service()

    with pytest.raises(ProviderNotInstalledError, match="openai"):
        service.create(kind=ProviderKind.OPENAI, label="Codex", secret="sk-fake-0001")

    assert repository.list() == ()
    assert secrets.secrets == {}


# -- (i) probe 필수 / 비밀 수명 -----------------------------------------------------------------


def test_a_failed_probe_stores_neither_profile_nor_secret() -> None:
    failed = ProbeResult(
        ok=False,
        message="API 키가 거부되었습니다",
        latency_ms=12,
        failure=ProbeFailure.AUTH,
    )
    service, repository, secrets = _service(probe_result=failed)

    with pytest.raises(ProviderProbeFailedError) as raised:
        service.create(kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001")

    assert raised.value.result == failed
    assert repository.list() == ()
    assert secrets.secrets == {}


def test_the_first_profile_becomes_active_and_keeps_its_secret_out_of_the_profile() -> None:
    service, repository, secrets = _service()

    profile = service.create(
        kind=ProviderKind.ANTHROPIC,
        label="Claude",
        secret="sk-fake-0001",
        base_url="https://proxy.example.com",
    )

    assert profile.active is True
    assert profile.model == "anthropic-default"
    assert profile.base_url == "https://proxy.example.com"
    assert profile.created_at == FIXED_NOW
    assert repository.list() == (profile,)
    assert secrets.secrets == {profile.profile_id: "sk-fake-0001"}
    assert "sk-fake-0001" not in repr(profile)


def test_an_explicit_model_overrides_the_adapter_default() -> None:
    service, _, _ = _service()

    profile = service.create(
        kind=ProviderKind.ANTHROPIC,
        label="Claude",
        secret="sk-fake-0001",
        model="claude-opus-5",
    )

    assert profile.model == "claude-opus-5"


def test_a_second_profile_does_not_steal_the_active_flag() -> None:
    service, _, _ = _service()
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")

    second = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")

    assert second.active is False
    active = service.active()
    assert active is not None
    assert active.profile_id == first.profile_id


def test_activate_moves_the_active_flag() -> None:
    service, _, _ = _service()
    service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    second = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")

    service.activate(second.profile_id)

    assert [profile.active for profile in service.list()] == [False, True]
    active = service.active()
    assert active is not None
    assert active.label == "B"


def test_delete_removes_the_profile_and_its_secret() -> None:
    service, repository, secrets = _service()
    profile = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")

    service.delete(profile.profile_id)

    assert repository.list() == ()
    assert secrets.secrets == {}
    with pytest.raises(ProviderProfileNotFoundError):
        service.delete(profile.profile_id)


def test_test_connection_probes_with_the_stored_secret_and_model() -> None:
    service, _, _ = _service()
    profile = service.create(
        kind=ProviderKind.ANTHROPIC,
        label="A",
        secret="sk-fake-0001",
        model="claude-opus-5",
        base_url=None,
    )

    result = service.test(profile.profile_id)

    assert result.ok is True


def test_test_connection_surfaces_a_missing_secret() -> None:
    service, _, secrets = _service()
    profile = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    secrets.secrets.clear()

    with pytest.raises(ProviderSecretMissingError, match=profile.profile_id):
        service.test(profile.profile_id)


def test_active_is_none_before_any_profile_exists() -> None:
    service, _, _ = _service()

    assert service.active() is None


# -- base_url 규칙 (spec D6) --------------------------------------------------------------------
#
# 사용자가 넣은 주소로 API 키가 그대로 전송된다. 검사를 빠뜨리면 오타 하나가 키를 남의 서버로
# 보내고, 그 사실은 어디에도 남지 않는다.


@pytest.mark.parametrize(
    ("base_url", "reason_fragment"),
    [
        ("http://api.example.com", "scheme"),
        ("ftp://api.example.com", "scheme"),
        ("https://localhost/v1", "loopback"),
        ("https://inner.localhost/v1", "loopback"),
        ("https://127.0.0.1/v1", "IP literals"),
        ("https://10.0.0.5/v1", "IP literals"),
        ("https://[::1]/v1", "IP literals"),
        ("https://user:key@api.example.com", "credentials"),
        ("https:///v1", "host is empty"),
    ],
)
def test_a_rejected_base_url_stores_nothing(base_url: str, reason_fragment: str) -> None:
    service, repository, secrets = _service()

    with pytest.raises(ProviderBaseUrlRejectedError) as raised:
        service.create(
            kind=ProviderKind.ANTHROPIC,
            label="Claude",
            secret="sk-fake-0001",
            base_url=base_url,
        )

    assert reason_fragment in raised.value.reason
    assert repository.list() == ()
    assert secrets.secrets == {}


@pytest.mark.parametrize(
    "base_url",
    [None, "https://api.example.com", "https://gateway.example.co.kr/v1"],
)
def test_an_accepted_base_url_is_stored_as_given(base_url: str | None) -> None:
    service, _, _ = _service()

    profile = service.create(
        kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001", base_url=base_url
    )

    assert profile.base_url == base_url


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8080/v1", "http://localhost:8080"])
def test_the_insecure_escape_hatch_allows_a_local_proxy(base_url: str) -> None:
    service, _, _ = _service(allow_insecure_base_url=True)

    profile = service.create(
        kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001", base_url=base_url
    )

    assert profile.base_url == base_url


def test_the_base_url_check_runs_before_the_probe() -> None:
    """거절된 주소로는 키를 한 번도 보내지 않는다 — probe 자체가 키 전송이다."""
    provider = ScriptedProvider(kind=ProviderKind.ANTHROPIC)
    service = ProviderProfileService(
        InMemoryProviderProfileRepository(),
        InMemoryProviderSecretStore(),
        {ProviderKind.ANTHROPIC: provider},
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("profile"),
    )

    with pytest.raises(ProviderBaseUrlRejectedError):
        service.create(
            kind=ProviderKind.ANTHROPIC,
            label="Claude",
            secret="sk-fake-0001",
            base_url="http://evil.example.com",
        )

    assert provider.probe_calls == []
