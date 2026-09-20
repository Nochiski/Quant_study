"""공급자 프로파일 유스케이스 (설계 spec D3).

계약 둘이 이 서비스의 존재 이유다.

1. **연결이 확인된 프로파일만 저장한다.** probe 없이 저장하면 사용자는 채팅을 보내 봐야 키가
   틀렸다는 걸 알게 되고, 그 사이 잘못된 키가 활성 프로파일로 남아 모든 턴이 실패한다. 그래서
   `create`는 probe를 먼저 돌리고, 실패하면 저장소에 아무것도 남기지 않는다.
2. **`base_url`은 검사를 통과한 값만 받는다**(spec D6). 사용자가 넣은 주소로 API 키가 그대로
   전송되므로, 검사 없이 받으면 오타 하나가 키를 남의 서버로 보낸다. `https`만 허용하고 IP
   리터럴·루프백을 막는다. 로컬 프록시 개발용 예외는 bootstrap이 켜 준다.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from strategy_workbench.domain.assistant.facade.models import (
    ProbeResult,
    ProviderKind,
    ProviderProfile,
)

from .ports.outgoing.llm_provider import LlmProviderPort
from .ports.outgoing.provider_profiles import ProviderProfileRepository
from .ports.outgoing.provider_secrets import ProviderSecretStore

__all__ = [
    "ProviderBaseUrlRejectedError",
    "ProviderAvailability",
    "ProviderNotInstalledError",
    "ProviderProbeFailedError",
    "ProviderProfileService",
]

# 호스트 이름만으로 루프백·사설이라고 단정할 수 있는 것들. DNS를 끌어오지 않고도 흔한 실수를
# 막는다. 비교 전에 후행 점을 떼고 소문자로 맞춘다.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal")

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class ProviderAvailability:
    """설정 화면이 "설치 필요"를 보여 주는 데 필요한 사실."""

    kind: ProviderKind
    installed: bool
    default_model: str | None


class ProviderNotInstalledError(LookupError):
    """해당 공급자 adapter가 등록되지 않았다(optional extra 미설치)."""

    def __init__(self, kind: ProviderKind) -> None:
        super().__init__(
            f"provider adapter is not installed — kind={kind.value!r}; "
            "install the matching optional dependency and restart the server"
        )
        self.kind = kind


class ProviderBaseUrlRejectedError(ValueError):
    """`base_url`이 spec D6 규칙을 어겼다. HTTP는 `assistant.base_url_rejected`로 옮긴다."""

    def __init__(self, base_url: str, reason: str) -> None:
        super().__init__(f"base_url rejected — base_url={base_url!r} reason={reason!r}")
        self.base_url = base_url
        self.reason = reason


class ProviderProbeFailedError(RuntimeError):
    """연결 테스트가 실패해 프로파일을 저장하지 않았다. 사유는 `result.failure`가 들고 있다.

    예외 문자열에는 열거값(kind·model·failure)만 넣는다. `result.message`를 보간하지 않는 것이
    요점이다. 이 저장소의 HTTP 관례가 `detail={"code": …, "message": str(error)}`라서, 보간하는
    순간 그 문장이 응답 본문·devtools·프록시 로그로 그대로 나간다. 사람이 읽을 문장이 필요한
    곳은 `error.result.failure`를 보고 자기 문장을 고른다.
    """

    def __init__(self, kind: ProviderKind, model: str, result: ProbeResult) -> None:
        failure = result.failure.value if result.failure is not None else "unknown"
        super().__init__(
            "provider connection probe failed — "
            f"kind={kind.value!r} model={model!r} failure={failure!r}"
        )
        self.result = result


class ProviderProfileService:
    def __init__(
        self,
        repository: ProviderProfileRepository,
        secrets: ProviderSecretStore,
        providers: Mapping[ProviderKind, LlmProviderPort],
        *,
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        allow_insecure_base_url: bool = False,
    ) -> None:
        """`allow_insecure_base_url`은 bootstrap이 환경 변수를 읽어 넘긴다.

        spec D6이 정한 예외는 `STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL=1`이고, 그
        변수를 읽는 것은 composition root(A-04 bootstrap)의 일이다. application이 `os.environ`을
        직접 읽으면 유스케이스가 배포 환경에 묶이고, 테스트가 두 경로를 모두 돌리기 어려워진다.
        """
        self._repository = repository
        self._secrets = secrets
        self._providers = providers
        self._now = now
        self._new_id = new_id
        self._allow_insecure_base_url = allow_insecure_base_url

    def available_kinds(self) -> tuple[ProviderAvailability, ...]:
        """선언된 공급자 전부와 각자의 설치 여부. 미설치 종류도 숨기지 않고 사유와 함께 보인다."""
        availability: list[ProviderAvailability] = []
        for kind in ProviderKind:
            provider = self._providers.get(kind)
            availability.append(
                ProviderAvailability(
                    kind=kind,
                    installed=provider is not None,
                    default_model=provider.default_model() if provider is not None else None,
                )
            )
        return tuple(availability)

    def list(self) -> tuple[ProviderProfile, ...]:
        return self._repository.list()

    def active(self) -> ProviderProfile | None:
        for profile in self._repository.list():
            if profile.active:
                return profile
        return None

    def create(
        self,
        *,
        kind: ProviderKind,
        label: str,
        secret: str,
        model: str | None = None,
        base_url: str | None = None,
    ) -> ProviderProfile:
        """연결 테스트를 통과한 프로파일만 저장한다. 첫 프로파일은 활성으로 시작한다."""
        provider = self._provider_for(kind)
        self._check_base_url(base_url)
        chosen_model = model or provider.default_model()
        result = provider.probe(secret, model=chosen_model, base_url=base_url)
        if not result.ok:
            raise ProviderProbeFailedError(kind, chosen_model, result)
        profile = ProviderProfile(
            profile_id=self._new_id(),
            kind=kind,
            label=label,
            model=chosen_model,
            base_url=base_url,
            created_at=self._now(),
            active=not any(item.active for item in self._repository.list()),
        )
        # 프로파일을 먼저 넣고 키를 나중에 쓴다. 키 쓰기가 실패하면 방금 넣은 프로파일을 되돌려,
        # 어떤 실패 경로에서도 "주인 없는 키"가 디스크에 남지 않게 한다(spec D1: 비밀의 수명은
        # 프로파일보다 길지 않다). 반대 순서면 `add` 실패 시 지울 방법이 없는 평문 키가 남는다.
        self._repository.add(profile)
        try:
            self._secrets.put(profile.profile_id, secret)
        except Exception:
            self._repository.delete(profile.profile_id)
            raise
        return profile

    def test(self, profile_id: str) -> ProbeResult:
        """저장된 키·모델로 연결을 다시 확인한다. 결과는 예외가 아니라 값으로 돌려준다."""
        profile = self._repository.get(profile_id)
        provider = self._provider_for(profile.kind)
        secret = self._secrets.get(profile.profile_id)
        return provider.probe(secret, model=profile.model, base_url=profile.base_url)

    def activate(self, profile_id: str) -> ProviderProfile:
        self._repository.get(profile_id)
        self._repository.set_active(profile_id)
        return self._repository.get(profile_id)

    def delete(self, profile_id: str) -> None:
        """프로파일과 그 키를 함께 지우고, 활성이었다면 다음 프로파일에 활성을 넘긴다.

        키를 먼저 지운다. 중간에 실패하면 "키 없는 프로파일"이 남는데, 그쪽이 "주인 없는 키"가
        디스크에 남는 것보다 낫다(spec D1: 비밀은 backend를 떠나지 않고 수명도 프로파일보다 길지
        않다).

        활성 승계가 없으면 프로파일이 화면에 멀쩡히 보이는데 모든 턴이
        `assistant.no_active_provider`로 거부되고, 새 프로파일을 추가해도 풀리지 않는다. 가장 흔한
        "키 교체"가 바로 그 상태를 만든다.
        """
        profile = self._repository.get(profile_id)
        self._secrets.delete(profile_id)
        self._repository.delete(profile_id)
        if not profile.active:
            return
        remaining = self._repository.list()
        if not remaining:
            return
        successor = max(remaining, key=lambda item: item.created_at)
        self._repository.set_active(successor.profile_id)

    def _provider_for(self, kind: ProviderKind) -> LlmProviderPort:
        provider = self._providers.get(kind)
        if provider is None:
            raise ProviderNotInstalledError(kind)
        return provider

    def _check_base_url(self, base_url: str | None) -> None:
        """spec D6의 base_url 규칙. 위반 사유를 `ProviderBaseUrlRejectedError.reason`에 담는다.

        호스트 이름이 사설 주소로 resolve되는 경우까지는 막지 않는다(그러려면 DNS를 봐야 하고,
        그 결과는 호출 시점마다 달라진다). 검사로 확정할 수 있는 것은 표기 그 자체다.

        `ipaddress`만으로는 부족하다. `2130706433`·`0x7f000001`·`017700000001`은 전부 127.0.0.1로
        연결되지만 점-십진 표기가 아니라 `ipaddress`가 주소로 인정하지 않는다. 그래서 마지막
        라벨(TLD)이 알파벳 2자 이상이어야 한다는 규칙을 같이 건다 — 정상 호스트명의 TLD는 숫자나
        `0x…`일 수 없다. 후행 점(`localhost.`)은 비교 전에 떼어 낸다.

        로컬 프록시 예외(`STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL`)가 켜져도 호스트는
        루프백·사설 대역으로 한정한다. 예외의 뜻이 "로컬 프록시"이므로 공개 호스트로 평문 http를
        보내는 것은 그 뜻 밖이다.
        """
        if base_url is None:
            return
        parts = urlsplit(base_url)
        allowed_schemes = ("https", "http") if self._allow_insecure_base_url else ("https",)
        if parts.scheme not in allowed_schemes:
            raise ProviderBaseUrlRejectedError(
                base_url, f"scheme must be one of {list(allowed_schemes)}, got {parts.scheme!r}"
            )
        if "@" in parts.netloc:
            raise ProviderBaseUrlRejectedError(base_url, "credentials in the URL are not accepted")
        if not parts.hostname:
            raise ProviderBaseUrlRejectedError(base_url, "host is empty")
        host = parts.hostname.rstrip(".").lower()
        if not host:
            raise ProviderBaseUrlRejectedError(base_url, "host is empty")
        address = _ip_literal(host)
        if self._allow_insecure_base_url:
            self._require_local_host(base_url, host, address)
            return
        if host in _LOOPBACK_NAMES or host.endswith(_PRIVATE_SUFFIXES):
            raise ProviderBaseUrlRejectedError(base_url, "loopback hosts are not accepted")
        if address is not None:
            raise ProviderBaseUrlRejectedError(
                base_url, f"IP literals are not accepted (is_private={address.is_private})"
            )
        if not _has_alphabetic_tld(host):
            raise ProviderBaseUrlRejectedError(
                base_url,
                "IP literals are not accepted — the last label must be alphabetic "
                f"(2+ letters), got {host.rsplit('.', 1)[-1]!r}",
            )

    def _require_local_host(self, base_url: str, host: str, address: _IpAddress | None) -> None:
        """예외 플래그가 켜졌을 때의 허용 범위: 루프백 이름과 루프백·사설 IP뿐이다."""
        if host in _LOOPBACK_NAMES or host.endswith(_PRIVATE_SUFFIXES):
            return
        if address is not None and (address.is_loopback or address.is_private):
            return
        raise ProviderBaseUrlRejectedError(
            base_url,
            f"the insecure escape hatch only accepts loopback or private hosts — host={host!r}",
        )


def _ip_literal(host: str) -> _IpAddress | None:
    """호스트가 점-십진/IPv6 리터럴이면 그 주소, 아니면 None."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _has_alphabetic_tld(host: str) -> bool:
    last_label = host.rsplit(".", 1)[-1]
    return len(last_label) >= 2 and last_label.isalpha()
