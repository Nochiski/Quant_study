"""compile 이 읽는 필드 계약 port (P2-07, spec D5).

연결된 equity 어댑터가 제공하는 팩터 필드 계약 전부를 돌려준다. compile 은 이 목록으로 없는
`field_id`(`strategy.expression.field_missing`)와 어댑터 capability(그룹 필드 `group_series` 제공
여부 → `strategy.operator.unsupported`, 연산자 카탈로그 `availability`)를 판정한다.

원천 값·단위·값 타입의 owner 는 어댑터다. 목록은 매 호출 읽고 복사해 두지 않는다 — 같은 어댑터의
`resolve_factor_fields` 와 같은 변환을 거쳐야 compile 과 실행이 같은 계약을 본다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.factor.facade.expression import FieldMetadata


class FieldCatalogPort(Protocol):
    def factor_field_catalog(self) -> tuple[FieldMetadata, ...]:
        """어댑터가 제공하는 필드 계약 전부. 실행 경로의 필드 계약 조회와 같은 값을 답한다."""
        ...
