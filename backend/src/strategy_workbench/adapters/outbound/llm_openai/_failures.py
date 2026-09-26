"""SDK 예외 → `FailureCode` / `ProbeFailure` (설계 spec D2/D4).

## 예외 문자열은 한 글자도 나가지 않는다

`openai.AuthenticationError`의 `str()`에는 응답 본문이 들어 있고, 그 본문에는
`Incorrect API key provided: sk-proj-AbC…`처럼 키 조각이 그대로 담긴다. `Failure.message`는
HTTP·SSE를 타고 화면까지 가고 이력에도 저장된다. 한 번 새면 회수 경로가 없다.

그래서 이 모듈은 예외에서 **타입 이름만** 꺼낸다. 진단에 필요한 나머지(어느 공급자, 어느 모델)는
예외가 아니라 호출자가 이미 알고 있는 값으로 채운다. 로그도 같은 규칙이다 — `logger.exception`은
예외의 `str()`을 그대로 기록하므로 쓰지 않는다.

## 왜 adapter가 매핑하는가

`LlmProviderPort` 계약상 예외를 그대로 올려도 application이 `Failure(PROVIDER)`로 흡수한다. 다만
그러면 인증 실패도 요금 한도도 네트워크 단절도 전부 `PROVIDER` 한 칸으로 뭉개져, 화면이
"키를 확인하세요"와 "잠시 후 다시"를 구분할 수 없다(spec D4: 예외 종류를 `FailureCode`로 매핑).
그래서 adapter가 **아는 예외만** 이벤트로 옮기고, 모르는 예외는 그대로 올려 application의
`PROVIDER` 경로에 맡긴다.
"""

from __future__ import annotations

import openai

from strategy_workbench.domain.assistant.facade.models import (
    Failure,
    FailureCode,
    ProbeFailure,
)

__all__ = ["failure_for", "probe_failure_for"]

_MESSAGES = {
    FailureCode.AUTH: "공급자가 API 키를 거부했습니다",
    FailureCode.RATE_LIMIT: "공급자 요금 한도에 걸렸습니다",
    FailureCode.NETWORK: "공급자에 연결하지 못했습니다",
    FailureCode.PROVIDER: "공급자 호출이 실패했습니다",
}


def _code_for(error: Exception) -> FailureCode | None:
    """아는 예외의 `FailureCode`. 모르면 `None`(호출자가 예외를 그대로 올린다).

    가장 좁은 것부터 본다. SDK에서 `APITimeoutError`는 `APIConnectionError`의 하위 타입이고
    `RateLimitError`·`AuthenticationError`는 `APIStatusError`의 하위 타입이라, 순서를 뒤집으면
    넓은 쪽이 전부 먹는다.
    """
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        return FailureCode.AUTH
    if isinstance(error, openai.RateLimitError):
        return FailureCode.RATE_LIMIT
    if isinstance(error, openai.APIConnectionError):
        # `APITimeoutError`도 여기다. 둘 다 "응답을 받기 전에 끊겼다"이고 재시도가 답이다.
        return FailureCode.NETWORK
    if isinstance(error, openai.APIStatusError | openai.OpenAIError):
        return FailureCode.PROVIDER
    return None


def failure_for(error: Exception, *, model: str) -> Failure | None:
    """턴 도중에 난 SDK 예외를 `Failure` 이벤트로. 모르는 예외면 `None`.

    Args:
        error: SDK가 올린 예외.
        model: 프로파일의 모델 이름. 예외가 아니라 호출자가 이미 아는 값이라 넣어도 안전하다.

    Returns:
        매핑된 `Failure`, 또는 이 adapter가 분류하지 못하는 예외면 `None`.
    """
    code = _code_for(error)
    if code is None:
        return None
    return Failure(
        code=code,
        message=(
            f"{_MESSAGES[code]} — kind=openai model={model} error_type={type(error).__name__}"
        ),
    )


def probe_failure_for(error: Exception) -> ProbeFailure:
    """연결 테스트 실패 사유. `ProbeResult`는 문장을 스스로 고르므로 사유만 돌려준다.

    `NotFoundError`(404)만 모델 이름 오타로 본다. 키·모델·네트워크·요금 한도를 구분하는 것이
    설정 화면의 "연결 테스트"가 존재하는 이유다.
    """
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProbeFailure.AUTH
    if isinstance(error, openai.NotFoundError):
        return ProbeFailure.MODEL_NOT_FOUND
    if isinstance(error, openai.RateLimitError):
        return ProbeFailure.RATE_LIMIT
    if isinstance(error, openai.APIConnectionError):
        return ProbeFailure.NETWORK
    return ProbeFailure.UNKNOWN
