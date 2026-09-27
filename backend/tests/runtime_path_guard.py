"""테스트 프로세스가 개발자 로컬의 런타임 상태 경로를 만지지 못하게 막는 가드(#211).

런타임 앱을 조립하는 테스트가 기본 경로(`backend/.local/`의 assistant·strategy DB, 백테스트
산출물, OS 사용자 설정 디렉터리의 공급자 비밀)를 열면, 테스트가 개발자의 실제 대화 이력 DB를
업그레이드하고 브랜치를 바꾼 뒤 옛 코드의 테스트가 `stored=2`로 실패한다. 디렉터리를 테스트 전후로
비교하면 같은 체크아웃에서 떠 있는 개발 서버의 쓰기까지 잡아 거짓 실패가 나므로, 이 프로세스의
파일 열기만 Python 감사 훅(`sys.addaudithook`)으로 본다.

감사 훅이 볼 수 없는 것이 둘 있다.

- 하위 프로세스. 하위 프로세스로 앱을 만드는 테스트는 경로를 직접 격리한다.
- 네이티브 라이브러리가 C 코드에서 직접 여는 파일. 예를 들어 in-memory 연결의 SQLite
  `ATTACH DATABASE '<path>'`와 `duckdb.connect(path)`는 감사 이벤트를 내지 않는다. 지금 운영 코드는
  감시 경로를 `sqlite3.connect(path)`·`open`·`mkdir`·`os.replace`로만 열므로 잠재 위험이다. 그런
  경로가 생기면 격리 fixture(`isolate_runtime_state_paths`)로 막아야 하고 이 가드에 기대면 안 된다.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

# 경로를 첫 인자로 받는 감사 이벤트. 파일 내용·메타데이터를 바꾸는 truncate·utime·chmod 계열도 본다.
_SINGLE_PATH_EVENTS = frozenset(
    {
        "open",
        "sqlite3.connect",
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.truncate",
        "os.utime",
        "os.chmod",
        "os.chown",
        "os.chflags",
    }
)
# 경로 둘을 받는 이벤트는 둘 다 본다. `os.replace`도 `os.rename` 이벤트를 낸다. link·symlink는
# (원본, 새 링크) 순서다.
_TWO_PATH_EVENTS = frozenset({"os.rename", "os.link", "os.symlink"})


def _normalized(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class RuntimePathGuard:
    """감시 root 아래 경로를 건드린 감사 이벤트를 모은다. 훅 안에서는 절대 예외를 내지 않는다."""

    def __init__(self, roots: Iterable[Path]) -> None:
        self._roots = tuple(sorted({_normalized(root) for root in roots}))
        self._touched: list[str] = []

    @property
    def roots(self) -> tuple[str, ...]:
        return self._roots

    def __call__(self, event: str, args: tuple[object, ...]) -> None:
        if event in _SINGLE_PATH_EVENTS:
            candidates = args[:1]
        elif event in _TWO_PATH_EVENTS:
            candidates = args[:2]
        else:
            return
        try:
            for candidate in candidates:
                hit = self._watched(candidate)
                if hit is not None:
                    self._touched.append(f"{event} {hit}")
        except Exception:  # 감사 훅이 예외를 내면 감시 대상 연산이 깨진다
            return

    def _watched(self, candidate: object) -> str | None:
        if isinstance(candidate, bytes):
            candidate = os.fsdecode(candidate)
        if not isinstance(candidate, (str, os.PathLike)):
            return None  # 파일 디스크립터(int) 등
        text = os.fspath(candidate)
        if not isinstance(text, str) or text == "" or text == ":memory:":
            return None
        if text.startswith("file:"):
            # SQLite URI(`file:path?mode=…`): in-memory 이름은 경로가 아니다.
            text = text[len("file:") :].split("?", 1)[0]
            if text == "" or text.startswith(":memory:"):
                return None
        path = _normalized(text)
        for root in self._roots:
            if path == root or path.startswith(root + os.sep):
                return path
        return None

    def drain(self) -> list[str]:
        """모은 이벤트를 돌려주고 비운다."""
        touched, self._touched = self._touched, []
        return touched
