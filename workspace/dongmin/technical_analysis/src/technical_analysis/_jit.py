"""numba 가 있으면 반복문 계산을 컴파일하고, 없으면 같은 함수를 그대로 쓴다.

도구는 한 종목만 계산해 순수 파이썬으로도 충분하고, 연구처럼 종목 수천 개를 돌릴 때만 numba 를 깐다
(`uv sync --extra fast`). 두 경우 모두 같은 함수 본문이 돌아서 정의가 갈리지 않는다.
"""

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])

try:
    from numba import njit as _njit
except ImportError:  # numba 는 선택 의존성이다
    _njit = None


def jit(fn: F) -> F:
    """numba 가 있으면 `njit(cache=True)` 로 감싸고, 없으면 그대로 돌려준다."""
    if _njit is None:
        return fn
    return _njit(cache=True)(fn)  # pyright: ignore[reportReturnType]  # reason: numba 디스패처는 원래 함수처럼 호출된다
