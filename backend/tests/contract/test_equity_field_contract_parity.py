"""mock·duckdb 두 Equity 어댑터의 필드 계약 대조 (#207).

테스트·e2e 는 `equity_mock`, 실사용은 `equity_duckdb` 를 쓴다. compile 은 연결된 어댑터의 필드
계약(`resolve_factor_fields`·`list_fields`)으로 단위 경고·field_missing·타입 검사를 하므로, 같은
field_id 의 단위나 값 타입이 두 어댑터에서 다르면 mock 으로 green 인 문서가 실데이터에서만 다르게
동작한다. 정본은 원장이다. mock 은 원장 스키마를 옮긴 duckdb 선언표(`FIELD_SPECS`)에 단위·값
타입을 맞추고, 선언표의 값 타입과 mock 의 랙은 원장 `dataset_profile` 선언에 맞춘다(아래 #230 절).

대조는 선언표를 직접 읽는다 — 손 픽스처 루트의 `list_fields()` 는 그 루트에 원천 테이블이 있는
필드만 내므로, 새 `FieldSpec` 이 픽스처보다 먼저 들어오면 대조를 빠져나갈 수 있다.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
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
        "price.adj_close",
        "price.market_cap",
        "financial.book_equity",
        "financial.net_income",
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
    """compile 이 실제로 읽는 포트 응답(`FieldMetadata`)까지 같아야 한다."""
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    root: Path = tmp_path_factory.mktemp("field-parity") / "equity"
    duckdb_adapter = EquityDuckdbAdapter(build_workbench_root(root))
    shared = tuple(_shared_field_ids())
    mock_fields = {
        item.field_id: (item.unit, item.value_type)
        for item in MockEquityDataAdapter.demo().resolve_factor_fields(shared).fields
    }
    duckdb_fields = {
        item.field_id: (item.unit, item.value_type)
        for item in duckdb_adapter.resolve_factor_fields(shared).fields
    }
    missing = sorted(set(shared) - set(duckdb_fields))
    assert not missing, f"손 픽스처가 공통 필드를 다 내지 않는다 — missing={missing}"
    assert mock_fields == duckdb_fields


# ── 랙·빈도·값 타입 (#230) ─────────────────────────────────────────────────────
# 랙과 값 타입의 정본은 원장 `dataset_profile`(S19)이다. 원장 빌드가 그 표를 만드는 선언
# (`database/src/equity` 의 `rules_s*.FIELDS`)을 그대로 읽어 대조한다. 실원장 파일 없이 CI
# 에서 돈다.
# 빈도는 원장 어휘(session·report)가 아니라 duckdb 어댑터가 `list_fields()` 로 내는 어휘
# (`SourceSpec.frequency`)로 맞춘다. mock 이 대신 서는 것은 그 어댑터이기 때문이다.
_EQUITY_SRC = Path(__file__).resolve().parents[3] / "database" / "src"


def _ledger_profiles() -> dict[str, Any]:
    """원장 빌드가 `dataset_profile` 로 내는 필드 선언(field_id → `FieldProfile`)."""
    pytest.importorskip("duckdb", reason="원장 선언 모듈이 duckdb 를 import 한다(extra `equity`)")
    if not _EQUITY_SRC.is_dir():
        pytest.fail(f"원장 선언 경로가 없다 — path={_EQUITY_SRC}")
    # 경로는 import 하는 동안만 올린다. `database/src` 최상위의 일반 이름 모듈(api·stage 등)이
    # 이후 테스트의 import 를 가리지 않게 한다(저장소 관례, test_core_parity.py).
    added = str(_EQUITY_SRC) not in sys.path
    if added:
        sys.path.insert(0, str(_EQUITY_SRC))
    try:
        rules_s19 = importlib.import_module("equity.rules_s19")
        return {profile.field_id: profile for _, profile in rules_s19.owned_fields()}
    finally:
        if added:
            sys.path.remove(str(_EQUITY_SRC))


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_랙이_원장_선언과_같다(field_id: str) -> None:
    """mock 은 PIT 를 이 랙으로 흉내 낸다. 다르면 mock 에서 본 공개 시점이 실데이터와 어긋난다."""
    mock = _mock_profiles()[field_id]
    ledger = _ledger_profiles()[field_id]
    assert mock.recommended_lag_sessions == ledger.recommended_lag_sessions, (
        f"mock 랙이 원장 dataset_profile 과 다르다 — field_id={field_id} "
        f"expected={ledger.recommended_lag_sessions} got={mock.recommended_lag_sessions}"
    )


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_빈도가_duckdb_어댑터와_같다(field_id: str) -> None:
    mock = _mock_profiles()[field_id]
    expected = SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].frequency
    assert mock.frequency == expected, (
        f"mock 빈도가 duckdb 어댑터와 다르다 — field_id={field_id} "
        f"expected={expected} got={mock.frequency}"
    )


@pytest.mark.parametrize("field_id", sorted(FIELD_BY_ID))
def test_duckdb_선언표의_값_타입이_원장_선언과_같다(field_id: str) -> None:
    """`FIELD_SPECS` 는 원장 스키마를 옮긴 표다. 값 타입이 원장 `dataset_profile` 과 같아야 한다."""
    ledger = _ledger_profiles().get(field_id)
    assert ledger is not None, f"원장 dataset_profile 에 없는 field_id — field_id={field_id}"
    got = FIELD_BY_ID[field_id].value_type.value
    assert got == ledger.value_type, (
        f"FIELD_SPECS 값 타입이 원장 dataset_profile 과 다르다 — field_id={field_id} "
        f"expected={ledger.value_type} got={got}"
    )
