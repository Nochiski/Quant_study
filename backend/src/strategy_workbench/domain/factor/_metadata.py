"""원천 필드 계약을 팩터 컴파일 계약으로 옮기는 단일 경계."""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import FieldValueType

from ._nodes import FieldMetadata, NodeValueType


class SourceFieldContract(Protocol):
    @property
    def field_id(self) -> str: ...

    @property
    def unit(self) -> str: ...

    @property
    def value_type(self) -> FieldValueType: ...


def field_metadata(
    profile: SourceFieldContract, adjusted_field_id: str | None = None
) -> FieldMetadata:
    """CATEGORY만 그룹 시리즈이며 숫자 필드의 단위와 조정 짝은 원천 선언을 보존한다."""
    return FieldMetadata(
        field_id=profile.field_id,
        unit=profile.unit,
        value_type=(
            NodeValueType.GROUP_SERIES
            if profile.value_type is FieldValueType.CATEGORY
            else NodeValueType.NUMERIC_SERIES
        ),
        adjusted_field_id=adjusted_field_id,
    )
