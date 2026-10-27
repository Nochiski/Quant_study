"""numba 가 있으면 반복문 계산을 컴파일하고, 없으면 같은 함수를 그대로 쓴다.

numba 는 기본 의존성이다(자동 채널 점수가 순수 파이썬으로는 1분 넘게 걸린다). 그래도 numba 를 못 까는
환경에서는 같은 함수 본문이 순수 파이썬으로 돌아 결과는 같고 느리기만 하다.
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
