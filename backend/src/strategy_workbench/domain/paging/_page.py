"""카탈로그 목록의 페이지 요청 검증과 페이지 창 계산의 단일 정본(#375 DR-A-14)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

MAX_PAGE_SIZE = 100

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    total: int
    page: int
    page_size: int
    page_count: int


def validate_page_request(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError(f"catalog page must be >= 1 — page={page}")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(
            f"catalog page_size must be within [1, {MAX_PAGE_SIZE}] — page_size={page_size}"
        )


def paginate(items: Sequence[T], page: int, page_size: int) -> Page[T]:
    """1부터 세는 `page`의 창을 자른다. 범위를 넘은 페이지는 빈 창이고 총계는 그대로다."""
    validate_page_request(page, page_size)
    start = (page - 1) * page_size
    return Page(
        items=tuple(items[start : start + page_size]),
        total=len(items),
        page=page,
        page_size=page_size,
        page_count=(len(items) + page_size - 1) // page_size,
    )
