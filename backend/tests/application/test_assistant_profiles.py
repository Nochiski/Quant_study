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

# 공급자 SDK의 인증 오류 본문이 흔히 담는 모양. 어디에도 새면 안 되는 문자열이다.
LEAKY_SDK_TEXT = "Incorrect API key provided: sk-proj-SECRET123."

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
    failed = ProbeResult(ok=False, latency_ms=12, failure=ProbeFailure.AUTH)
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
        # 아래 넷은 전부 127.0.0.1로 연결되지만 `ipaddress`가 주소로 인정하지 않는 표기다.
        ("https://2130706433/v1", "last label must be alphabetic"),
        ("https://0x7f000001/v1", "last label must be alphabetic"),
        ("https://017700000001/v1", "last label must be alphabetic"),
        ("https://localhost./v1", "loopback"),
        ("https://proxy.local/v1", "loopback"),
        ("https://gateway.internal/v1", "loopback"),
        ("https://LOCALHOST/v1", "loopback"),
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


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8080/v1", "http://localhost:8080", "http://192.168.0.10:8080"],
)
def test_the_insecure_escape_hatch_allows_a_local_proxy(base_url: str) -> None:
    service, _, _ = _service(allow_insecure_base_url=True)

    profile = service.create(
        kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001", base_url=base_url
    )

    assert profile.base_url == base_url


@pytest.mark.parametrize("base_url", ["http://evil.example.com/v1", "https://api.example.com/v1"])
def test_the_insecure_escape_hatch_still_refuses_public_hosts(base_url: str) -> None:
    """예외의 뜻은 "로컬 프록시"다. 켜 둔 환경에서 오타 하나가 공개 호스트로 키를 보내면 안 된다."""
    service, repository, secrets = _service(allow_insecure_base_url=True)

    with pytest.raises(ProviderBaseUrlRejectedError, match="loopback or private"):
        service.create(
            kind=ProviderKind.ANTHROPIC,
            label="Claude",
            secret="sk-fake-0001",
            base_url=base_url,
        )

    assert repository.list() == ()
    assert secrets.secrets == {}


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


# -- [P1] 비밀 스크럽 --------------------------------------------------------------------------


def test_a_probe_result_cannot_carry_a_provider_supplied_sentence() -> None:
    """`ProbeResult`에는 SDK 문자열을 넣을 자리가 없다 — message는 사유에서 유도된다."""
    result = ProbeResult(ok=False, failure=ProbeFailure.AUTH)

    assert LEAKY_SDK_TEXT not in result.message
    assert result.message == ProbeResult(ok=False, failure=ProbeFailure.AUTH).message
    assert "sk-proj" not in repr(result)


def test_a_probe_failure_never_reaches_the_exception_string() -> None:
    """HTTP 관례가 `message=str(error)`라 예외 문자열이 그대로 응답 본문이 된다."""
    service, _, _ = _service(
        probe_result=ProbeResult(ok=False, latency_ms=3, failure=ProbeFailure.AUTH)
    )

    with pytest.raises(ProviderProbeFailedError) as raised:
        service.create(kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-proj-SECRET123")

    rendered = str(raised.value)
    assert "sk-proj" not in rendered
    assert LEAKY_SDK_TEXT not in rendered
    assert "failure='auth'" in rendered
    # 사람이 읽을 문장은 사유에서 고른다.
    assert raised.value.result.failure is ProbeFailure.AUTH


def test_a_probe_result_must_name_its_reason() -> None:
    with pytest.raises(ValueError, match="failure=None"):
        ProbeResult(ok=False)

    with pytest.raises(ValueError, match="must not carry a failure reason"):
        ProbeResult(ok=True, failure=ProbeFailure.AUTH)


# -- [P2] 활성 승계 ----------------------------------------------------------------------------


def test_deleting_the_active_profile_promotes_the_most_recent_survivor() -> None:
    service, _, _ = _service()
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    second = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")

    service.delete(first.profile_id)

    active = service.active()
    assert active is not None
    assert active.profile_id == second.profile_id


def test_a_profile_created_while_none_is_active_becomes_active() -> None:
    """키 교체 시나리오: 하나뿐인 프로파일을 지우고 다시 만든다."""
    service, _, _ = _service()
    only = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    service.delete(only.profile_id)

    replacement = service.create(kind=ProviderKind.ANTHROPIC, label="A2", secret="sk-fake-0002")

    assert replacement.active is True
    active = service.active()
    assert active is not None
    assert active.profile_id == replacement.profile_id


def test_deleting_the_last_profile_leaves_no_active() -> None:
    service, repository, _ = _service()
    only = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")

    service.delete(only.profile_id)

    assert repository.list() == ()
    assert service.active() is None


def test_deleting_an_inactive_profile_leaves_the_active_one_alone() -> None:
    service, _, _ = _service()
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    second = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")

    service.delete(second.profile_id)

    active = service.active()
    assert active is not None
    assert active.profile_id == first.profile_id


# -- [P2] 생성 실패 시 주인 없는 키가 남지 않는다 ------------------------------------------------


class _FailingSecretStore(InMemoryProviderSecretStore):
    """키 쓰기가 실패하는 저장소(디스크 오류·권한 오류를 흉내 낸다)."""

    def put(self, profile_id: str, secret: str) -> None:
        raise OSError(f"secret store is unavailable — profile_id={profile_id!r}")


def test_a_failed_secret_write_rolls_the_profile_back() -> None:
    repository = InMemoryProviderProfileRepository()
    secrets = _FailingSecretStore()
    service = ProviderProfileService(
        repository,
        secrets,
        {ProviderKind.ANTHROPIC: ScriptedProvider(kind=ProviderKind.ANTHROPIC)},
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("profile"),
    )

    with pytest.raises(OSError, match="secret store is unavailable"):
        service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")

    assert repository.list() == ()
    assert secrets.secrets == {}
