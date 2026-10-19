"""연결된 데이터가 어떤 필드를 주는가. 목록이 "지금 쓸 수 있는지"를 싣는 데 쓴다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FieldAvailability:
    available: frozenset[str]
    unavailable_reasons: Mapping[str, str]
    """선언했지만 주지 않는 필드 → 어댑터가 완성한 사유 문장."""


class FieldAvailabilityPort(Protocol):
    def field_availability(self) -> FieldAvailability: ...
