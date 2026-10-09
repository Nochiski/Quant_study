"""ProviderProfileService 테스트 (A-01).

프로파일은 "연결이 확인된 것만 저장한다"가 계약이다. probe 없이 저장하면 사용자는 채팅을 보내
봐야 키가 틀렸다는 걸 알게 되고, 그 사이 잘못된 키가 활성 프로파일로 남는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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
    now: Callable[[], datetime] = lambda: FIXED_NOW,
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
        now=now,
        new_id=sequential_ids("profile"),
        allow_insecure_base_url=allow_insecure_base_url,
    )
    return service, repository, secrets


def _ticking_clock() -> Callable[[], datetime]:
    """부를 때마다 1분씩 흐르는 시계. 생성 시각이 실제로 달라지는 상황을 만든다."""
    ticks = iter(range(1, 1000))
    return lambda: FIXED_NOW + timedelta(minutes=next(ticks))


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
        ("https://127.0.0.1/v1", "loopback addresses"),
        ("https://10.0.0.5/v1", "private network addresses"),
        ("https://[::1]/v1", "loopback addresses"),
        ("https://8.8.8.8/v1", "IP literals"),
        ("https://user:key@api.example.com", "credentials"),
        ("https:///v1", "host is empty"),
        # 스킴을 빠뜨린 주소는 가장 흔한 오타다. 호스트가 멀쩡히 적혀 있는데 "호스트가 비었다"고
        # 답하면 사용자는 고칠 방향을 알 수 없다.
        ("api.openai.com/v1", "https://"),
        ("api.openai.com", "https://"),
        ("www.api.openai.com", "https://"),
        # 아래 넷은 전부 127.0.0.1로 연결되지만 `ipaddress`가 주소로 인정하지 않는 표기다.
        ("https://2130706433/v1", "last label must be alphabetic"),
        ("https://0x7f000001/v1", "last label must be alphabetic"),
        ("https://017700000001/v1", "last label must be alphabetic"),
        ("https://localhost./v1", "loopback"),
        # mDNS와 사설 전용 TLD는 루프백이 아니다. 걸린 규칙을 정확히 지목해야 사용자가 고칠 수 있다.
        ("https://proxy.local/v1", "local network names"),
        ("https://gateway.internal/v1", "local network names"),
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


@pytest.mark.parametrize("base_url", ["http://evil.example.com/v1", "http://8.8.8.8/v1"])
def test_the_insecure_escape_hatch_still_refuses_plaintext_to_public_hosts(
    base_url: str,
) -> None:
    """예외의 뜻은 "로컬 프록시"다. 켜 둔 환경에서 오타가 공개 호스트로 평문 키를 보내면 안 된다."""
    service, repository, secrets = _service(allow_insecure_base_url=True)

    with pytest.raises(ProviderBaseUrlRejectedError, match="scheme"):
        service.create(
            kind=ProviderKind.ANTHROPIC,
            label="Claude",
            secret="sk-fake-0001",
            base_url=base_url,
        )

    assert repository.list() == ()
    assert secrets.secrets == {}


@pytest.mark.parametrize(
    "base_url",
    [None, "https://api.openai.com/v1", "https://gateway.example.co.kr/v1"],
)
def test_the_insecure_escape_hatch_keeps_public_https_working(base_url: str | None) -> None:
    """플래그는 기본 정책의 상위집합이다. 켜는 순간 공개 API 프로파일이 막히면 안 된다.

    spec D6의 문장이 "http·루프백을 허용한다"는 덧셈이라, 플래그가 기본 정책을 좁히면 한 프로세스
    안에서 로컬 프록시 프로파일과 실제 공급자 API 프로파일을 함께 둘 수 없다.
    """
    service, _, _ = _service(allow_insecure_base_url=True)

    profile = service.create(
        kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001", base_url=base_url
    )

    assert profile.base_url == base_url


def test_a_rejection_reason_names_the_host_and_the_rule_that_refused_it() -> None:
    """이 문자열은 `assistant.base_url_rejected` 422 본문으로 사용자에게 그대로 나간다."""
    service, _, _ = _service()

    with pytest.raises(ProviderBaseUrlRejectedError) as raised:
        service.create(
            kind=ProviderKind.ANTHROPIC,
            label="Claude",
            secret="sk-fake-0001",
            base_url="https://box.local/v1",
        )

    assert "box.local" in raised.value.reason
    assert "local network names" in raised.value.reason
    assert "loopback" not in raised.value.reason


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
    """생존자가 둘 이상일 때 "가장 최근 생성"이 실제로 걸리는지 본다.

    생존자가 하나뿐이면 승계 규칙 자체가 걸리지 않아 이름이 약속한 것을 검증하지 못한다.
    """
    service, _, _ = _service(now=_ticking_clock())
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    middle = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")
    newest = service.create(kind=ProviderKind.ANTHROPIC, label="C", secret="sk-fake-0003")
    assert middle.created_at < newest.created_at

    service.delete(first.profile_id)

    active = service.active()
    assert active is not None
    assert active.profile_id == newest.profile_id
    assert [profile.active for profile in service.list()] == [False, True]


def test_the_succession_order_walks_back_through_the_survivors() -> None:
    service, _, _ = _service(now=_ticking_clock())
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    middle = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")
    newest = service.create(kind=ProviderKind.ANTHROPIC, label="C", secret="sk-fake-0003")

    service.delete(first.profile_id)
    service.delete(newest.profile_id)

    active = service.active()
    assert active is not None
    assert active.profile_id == middle.profile_id


def test_profiles_created_at_the_same_instant_break_the_tie_deterministically() -> None:
    """고정 시계에서도 승계자가 저장 순서에 좌우되면 안 된다.

    A-03 저장소가 `created_at`을 초 단위로 절삭하면 동률이 흔해진다. 같은 입력이 다른 활성
    프로파일을 낳으면 비결정이다. 규칙은 "가장 최근, 동률이면 `profile_id`가 큰 쪽"이다.
    """
    service, _, _ = _service()  # 고정 시계 — 세 프로파일의 created_at이 모두 같다
    first = service.create(kind=ProviderKind.ANTHROPIC, label="A", secret="sk-fake-0001")
    second = service.create(kind=ProviderKind.ANTHROPIC, label="B", secret="sk-fake-0002")
    third = service.create(kind=ProviderKind.ANTHROPIC, label="C", secret="sk-fake-0003")
    assert first.created_at == second.created_at == third.created_at

    service.delete(first.profile_id)

    active = service.active()
    assert active is not None
    assert active.profile_id == max(second.profile_id, third.profile_id)


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
