"""mock·duckdb 두 Equity 어댑터의 필드 계약 대조 (#207).

테스트·e2e 는 `equity_mock`, 실사용은 `equity_duckdb` 를 쓴다. compile 은 연결된 어댑터의 필드
계약(`resolve_factor_fields`·`list_fields`)으로 단위 경고·field_missing·타입 검사를 하므로, 같은
field_id 의 단위나 값 타입이 두 어댑터에서 다르면 mock 으로 green 인 문서가 실데이터에서만 다르게
동작한다. 정본은 원장 스키마를 옮긴 duckdb 선언표(`FIELD_SPECS`)이고 mock 이 거기에 맞춘다.

대조는 선언표를 직접 읽는다 — 손 픽스처 루트의 `list_fields()` 는 그 루트에 원천 테이블이 있는
필드만 내므로, 새 `FieldSpec` 이 픽스처보다 먼저 들어오면 대조를 빠져나갈 수 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    UNSUPPORTED_FIELDS,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.domain.equity.facade.research_data import DatasetFieldProfile

# 두 어댑터가 함께 내는 필드 — 대조가 빈 교집합으로 공허하게 통과하지 않게 최소 집합을 못박는다.
EXPECTED_SHARED = frozenset(
    {
        "price.close",
        "price.market_cap",
        "financial.book_equity",
        "consensus.forward_eps",
        "flow.foreign_net_buy",
        "credit.margin_balance",
    }
)


def _mock_profiles() -> dict[str, DatasetFieldProfile]:
    return {profile.field_id: profile for profile in MockEquityDataAdapter.demo().list_fields()}


def _shared_field_ids() -> list[str]:
    return sorted(set(_mock_profiles()) & set(FIELD_BY_ID))


def test_공통_필드_집합이_비어_있지_않다() -> None:
    shared = set(_shared_field_ids())
    assert shared >= EXPECTED_SHARED, (
        f"mock·duckdb 공통 필드가 기대 집합보다 작다 — missing={sorted(EXPECTED_SHARED - shared)} "
        f"shared={sorted(shared)}"
    )


def test_mock_필드는_원장이_아는_id_만_쓴다() -> None:
    """mock 전용 필드는 duckdb 가 미지원 사유를 적어 둔 id 여야 한다.

    mock 이 원장에 없는 id 를 지어내면 그 필드를 쓰는 문서는 실데이터에서 field_missing 이 된다.
    """
    unknown = sorted(
        field_id
        for field_id in _mock_profiles()
        if field_id not in FIELD_BY_ID and field_id not in UNSUPPORTED_FIELDS
    )
    assert not unknown, f"원장 선언표에 없는 mock field_id — unknown={unknown}"


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_단위와_값_타입이_같다(field_id: str) -> None:
    mock = _mock_profiles()[field_id]
    spec = FIELD_BY_ID[field_id]
    got = (mock.unit, mock.value_type)
    expected = (spec.unit, spec.value_type)
    assert got == expected, (
        f"mock 필드 계약이 원장 정본과 다르다 — field_id={field_id} "
        f"expected(unit, value_type)={expected} got={got}"
    )


def test_resolve_factor_fields_가_두_어댑터에서_같은_계약을_낸다(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """compile 이 실제로 읽는 포트 응답(`FieldMetadata`)까지 같아야 한다.

    원주가 표시(`adjusted_field_id`)도 포함한다 — compile 의 원주가 warning(BACKLOG-018)이 이 값을
    읽으므로 어댑터를 바꾸면 warning 이 켜지거나 꺼지면 안 된다.
    """
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    root: Path = tmp_path_factory.mktemp("field-parity") / "equity"
    duckdb_adapter = EquityDuckdbAdapter(build_workbench_root(root))
    shared = tuple(_shared_field_ids())
    mock_fields = {
        item.field_id: (item.unit, item.value_type, item.adjusted_field_id)
        for item in MockEquityDataAdapter.demo().resolve_factor_fields(shared).fields
    }
    duckdb_fields = {
        item.field_id: (item.unit, item.value_type, item.adjusted_field_id)
        for item in duckdb_adapter.resolve_factor_fields(shared).fields
    }
    missing = sorted(set(shared) - set(duckdb_fields))
    assert not missing, f"손 픽스처가 공통 필드를 다 내지 않는다 — missing={missing}"
    assert mock_fields == duckdb_fields


def test_duckdb_선언표의_원주가_표시는_같은_단위의_실재하는_조정_짝을_가리킨다() -> None:
    """compile 의 원주가 warning(BACKLOG-018)이 읽는 표시의 정본은 이 선언표다.

    duckdb extra 가 없는 CI 에서도 표시 자체가 지워지는 회귀를 잡는다(리뷰 #232 DEFECT-232-06).
    표시가 어댑터 응답까지 실리는지는 위 `resolve_factor_fields` 대조가 본다.
    """
    marked = {
        field_id: spec.adjusted_field_id
        for field_id, spec in FIELD_BY_ID.items()
        if spec.adjusted_field_id is not None
    }

    assert marked == {"price.close": "price.adj_close"}
    for raw, adjusted in marked.items():
        assert FIELD_BY_ID[adjusted].unit == FIELD_BY_ID[raw].unit
        assert FIELD_BY_ID[adjusted].adjusted_field_id is None, "조정 짝은 원주가가 아니다"
