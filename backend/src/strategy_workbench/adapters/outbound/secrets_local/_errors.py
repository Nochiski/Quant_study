from __future__ import annotations

from pathlib import Path


class SecretStoreStorageError(RuntimeError):
    """비밀 파일을 읽거나 안전하게 쓸 수 없다.

    이 예외의 **문자열에는 비밀 값도, 비밀 파일 경로도 넣지 않는다.** 값이 아니라 위치의 노출도
    되돌릴 수 없기 때문이다. A-04가 이 예외를 500 본문이나 진단 필드로 옮기면 사용자의 홈
    디렉터리 구조와 API 키 파일의 정확한 위치가 인증 없이 나간다
    (`.claude/rules/error-messages.md` 보안 예외: 외부로 내보내는 메시지의 절대 경로 금지,
    포트 docstring `provider_secrets.py`: "예외 메시지에도 profile_id만 적는다").

    진단에 경로가 필요한 호출자는 `error.path` 속성으로 읽는다. 속성은 `args`에 들어가지 않아
    `str()`·`repr()`·기본 직렬화 어디에도 따라 나오지 않는다.
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path
