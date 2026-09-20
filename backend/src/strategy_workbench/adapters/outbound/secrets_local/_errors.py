from __future__ import annotations

from pathlib import Path


class SecretStoreStorageError(RuntimeError):
    """비밀 파일을 읽거나 안전하게 쓸 수 없다.

    **주장의 범위는 `str()`·`repr()`·`args`다.** 이 셋에는 비밀 값도, 비밀 파일 경로도 넣지
    않는다. 값이 아니라 위치의 노출도 되돌릴 수 없기 때문이다. A-04가 응답 본문이나 진단 필드로
    옮기는 값이 바로 이 셋이다 — 거기 경로가 실리면 사용자의 홈 디렉터리 구조와 API 키 파일의
    정확한 위치가 인증 없이 나간다
    (`.claude/rules/error-messages.md` 보안 예외: 외부로 내보내는 메시지의 절대 경로 금지,
    포트 docstring `provider_secrets.py`: "예외 메시지에도 profile_id만 적는다").

    **`__cause__`는 그 범위 밖이고, 일부러 그렇다.** OS 실패는 원래 예외를 체인으로 남긴다.
    `OSError.__str__`이 `filename`을 담으므로 체인과 traceback에는 경로가 보이고, 같은 규칙이
    로컬 로그의 절대 경로·스택은 명시적으로 허용한다. 체인을 끊으면 "어느 파일이 왜 안 열렸나"를
    운영자가 볼 방법이 사라진다. 대신 **A-04는 이 예외의 체인·traceback을 HTTP 응답에 싣지
    않는다** — 그쪽 acceptance가 지켜야 할 조건이다.

    경로만 프로그램으로 읽고 싶은 호출자는 `error.path` 속성을 쓴다. 속성은 `args`에 들어가지
    않아 `str()`·`repr()`·기본 직렬화 어디에도 따라 나오지 않는다.
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path
