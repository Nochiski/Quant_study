"""개별종목차트 서비스의 계열 캐시(가장 오래 안 쓴 것부터 버리는 작은 LRU)."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import Generic, TypeVar

_K = TypeVar("_K")


_V = TypeVar("_V")


class Lru(Generic[_K, _V]):
    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[_K, _V] = OrderedDict()
        self._lock = Lock()

    def get(self, key: _K, build: Callable[[], _V]) -> _V:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = build()  # 같은 키를 두 스레드가 함께 만들어도 결과가 같다(포트가 같은 답을 준다)
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return value
