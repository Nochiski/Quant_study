"""원천 필드 단위는 연산 기호 없는 이름 하나다 — 단위 약분(`unit_dimensions`, #668 IT-01)의 전제.

곱·나눗셈 노드는 입력 단위를 `(a*b)`·`(a/b)` 로 이어 붙이고, 단위가 같은지는 그 표기를 약분해 본다.
원천 필드 단위에 `()*/` 가 있으면(예 `KRW(1000)`) 표기 전체를 읽지 못해 같은 단위 나눗셈이
무차원으로 줄지 않는다. `unknown`·`1` 은 판정용 예약어라 원천 단위로 쓰지 않는다. 두 어댑터의 필드 계약이 이
전제를 지키는지 본다.
"""

from __future__ import annotations

import re

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import FIELD_BY_ID
from strategy_workbench.adapters.outbound.equity_mock._fixture import build_demo_fixture
from strategy_workbench.domain.factor.facade.validation import DIMENSIONLESS_UNIT, UNKNOWN_UNIT

_ATOM = re.compile(r"^[^()*/]*$")
_RESERVED = {UNKNOWN_UNIT, DIMENSIONLESS_UNIT}

_UNITS = sorted(
    {("duckdb", spec.field_id, spec.unit) for spec in FIELD_BY_ID.values()}
    | {("mock", profile.field_id, profile.unit) for profile in build_demo_fixture().profiles}
)


@pytest.mark.parametrize(("adapter", "field_id", "unit"), _UNITS)
def test_source_field_units_are_atoms(adapter: str, field_id: str, unit: str) -> None:
    assert _ATOM.match(unit), f"{adapter} {field_id} unit={unit!r} 에 연산 기호가 있다"
    assert unit not in _RESERVED, f"{adapter} {field_id} unit={unit!r} 는 판정용 예약어다"
